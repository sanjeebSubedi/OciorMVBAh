#!/usr/bin/env python3
"""
MVBA Node Client Implementation
==============================

This client:
1. Connects to the bootstrap server to get initialization data
2. Sets up ZeroMQ connections for P2P communication
3. Provides framework for MVBA protocol implementation

Usage: python node.py [--server-port PORT] [--input VALUE]
"""

import argparse
import json
import logging
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from erasure_coding import erasure_encode
from predicate import predicate
from vector_commitment import vc_commit, vc_open, vc_verify

# ZeroMQ imports
try:
    import zmq
except ImportError:
    print("ERROR: ZeroMQ not installed. Install with: pip install pyzmq")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


class MessageType(Enum):
    """MVBA Protocol message types"""

    # ACIDh protocol messages
    SHARE = "SHARE"
    VOTE = "VOTE"
    LOCK = "LOCK"
    READY = "READY"
    FINISH = "FINISH"
    ELECTION = "ELECTION"
    CONFIRM = "CONFIRM"

    # DRh protocol messages
    ECHOSHARE = "ECHOSHARE"

    # Internal messages
    HEARTBEAT = "HEARTBEAT"
    SHUTDOWN = "SHUTDOWN"


@dataclass
class MVBAMessage:
    """Structured message for MVBA protocol"""

    msg_type: MessageType
    sender_id: int
    session_id: int
    protocol_id: str
    data: Dict[str, Any]
    timestamp: float = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()

    def to_json(self) -> str:
        return json.dumps(
            {
                "msg_type": self.msg_type.value,
                "sender_id": self.sender_id,
                "session_id": self.session_id,
                "protocol_id": self.protocol_id,
                "data": self.data,
                "timestamp": self.timestamp,
            }
        )

    @classmethod
    def from_json(cls, json_str: str) -> "MVBAMessage":
        data = json.loads(json_str)
        return cls(
            msg_type=MessageType(data["msg_type"]),
            sender_id=data["sender_id"],
            session_id=data["session_id"],
            protocol_id=data["protocol_id"],
            data=data["data"],
            timestamp=data["timestamp"],
        )


class ZeroMQManager:
    """Manages ZeroMQ connections for P2P communication"""

    def __init__(self, node_id: int, my_port: int, peers: List[Dict]):
        self.node_id = node_id
        self.my_port = my_port
        self.peers = peers

        self.context = zmq.Context()
        self.poller = zmq.Poller()

        # Sockets for communication
        self.router_socket = None  # For receiving messages
        self.dealer_sockets = {}  # For sending to specific peers

        self.running = False
        self.message_handler = None

        self.logger = logging.getLogger(f"ZMQ-Node{node_id}")

    def setup_connections(self) -> bool:
        """Set up ZeroMQ connections"""
        try:
            # Set up ROUTER socket for receiving messages
            self.router_socket = self.context.socket(zmq.ROUTER)
            self.router_socket.bind(f"tcp://*:{self.my_port}")
            self.poller.register(self.router_socket, zmq.POLLIN)

            self.logger.info(f"Listening on port {self.my_port}")

            # Set up DEALER sockets for sending to peers
            for peer in self.peers:
                peer_id = peer["node_id"]
                peer_port = peer["zmq_port"]
                peer_address = peer["address"]

                dealer = self.context.socket(zmq.DEALER)
                dealer.setsockopt(zmq.IDENTITY, str(self.node_id).encode())

                # Connect to peer
                dealer.connect(f"tcp://{peer_address}:{peer_port}")
                self.dealer_sockets[peer_id] = dealer

                self.logger.info(
                    f"Connected to peer {peer_id} on {peer_address}:{peer_port}"
                )

            return True

        except Exception as e:
            self.logger.error(f"Failed to set up connections: {e}")
            return False

    def send_message(self, target_node_id: int, message: MVBAMessage) -> bool:
        """Send message to specific node"""
        try:
            if target_node_id not in self.dealer_sockets:
                self.logger.error(f"No connection to node {target_node_id}")
                return False

            socket = self.dealer_sockets[target_node_id]
            socket.send_string(message.to_json())

            self.logger.debug(f"Sent {message.msg_type.value} to node {target_node_id}")
            return True

        except Exception as e:
            self.logger.error(f"Error sending message to node {target_node_id}: {e}")
            return False

    def broadcast_message(self, message: MVBAMessage) -> int:
        """Broadcast message to all peers. Returns number of successful sends."""
        successful_sends = 0

        for peer_id in self.dealer_sockets:
            if self.send_message(peer_id, message):
                successful_sends += 1

        self.logger.debug(
            f"Broadcast {message.msg_type.value} to {successful_sends} peers"
        )
        return successful_sends

    def start_message_loop(self, message_handler):
        """Start the message receiving loop"""
        self.message_handler = message_handler
        self.running = True

        thread = threading.Thread(target=self._message_loop, daemon=True)
        thread.start()

        self.logger.info("Message loop started")

    def _message_loop(self):
        """Main message receiving loop"""
        while self.running:
            try:
                # Poll for messages with timeout
                socks = dict(self.poller.poll(timeout=100))

                if self.router_socket in socks:
                    # Receive message
                    sender_identity, message_data = self.router_socket.recv_multipart()

                    try:
                        message = MVBAMessage.from_json(message_data.decode())
                        self.logger.debug(
                            f"Received {message.msg_type.value} from node {message.sender_id}"
                        )

                        # Handle message
                        if self.message_handler:
                            self.message_handler(message)

                    except Exception as e:
                        self.logger.error(f"Error processing message: {e}")

            except zmq.ZMQError as e:
                if e.errno == zmq.ETERM:
                    break
                self.logger.error(f"ZMQ error in message loop: {e}")
            except Exception as e:
                self.logger.error(f"Unexpected error in message loop: {e}")

    def stop(self):
        """Stop the ZeroMQ manager"""
        self.running = False

        # Close sockets
        if self.router_socket:
            self.router_socket.close()

        for socket in self.dealer_sockets.values():
            socket.close()

        self.context.term()
        self.logger.info("ZeroMQ manager stopped")


class MVBANode:
    """Main MVBA Node implementation"""

    def __init__(self, bootstrap_host: str = "localhost", bootstrap_port: int = 8080):
        self.bootstrap_host = bootstrap_host
        self.bootstrap_port = bootstrap_port

        # Node configuration (set during initialization)
        self.node_id: Optional[int] = None
        self.total_nodes: Optional[int] = None
        self.byzantine_threshold: Optional[int] = None
        self.session_id: Optional[int] = None
        self.peers: List[Dict] = []

        # ZeroMQ manager
        self.zmq_manager: Optional[ZeroMQManager] = None

        # Protocol state
        self.running = False
        self.input_value: Optional[str] = None
        self.protocol_started = False
        self.initialize_state()

        # MVBA protocol state (will be expanded)
        self.protocol_state = {
            "acid_state": {},  # ACIDh protocol state
            "election_state": {},  # Election state
            "dr_state": {},  # Data retrieval state
        }

        self.logger = logging.getLogger("MVBANode")

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def initialize_state(self):
        self.acidh_locks = {}
        self.acidh_ready = {}
        self.acidh_finish = {}
        self.acidh_shares = {}
        self.acidh_hash = {}
        self.received_shares = set()

        for peer in self.peers:
            self.acidh_locks[peer["node_id"]] = 0
            self.acidh_ready[peer["node_id"]] = 0
            self.acidh_finish[peer["node_id"]] = 0
            self.acidh_shares[peer["node_id"]] = (-1, -1, -1)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
        sys.exit(0)

    def connect_to_bootstrap(self) -> bool:
        """Connect to bootstrap server and get initialization data"""
        try:
            self.logger.info(
                f"Connecting to bootstrap server at {self.bootstrap_host}:{self.bootstrap_port}"
            )

            # Connect to bootstrap server
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((self.bootstrap_host, self.bootstrap_port))

            # Receive connection acknowledgment
            response = sock.recv(1024).decode().strip()
            ack_data = json.loads(response)

            if ack_data["status"] == "connected":
                self.node_id = ack_data["node_id"]
                waiting_for = ack_data["waiting_for"]

                self.logger.info(
                    f"Connected as node {self.node_id}, waiting for {waiting_for} more nodes"
                )

                # Wait for initialization data
                init_response = sock.recv(4096).decode().strip()
                init_data = json.loads(init_response)

                if init_data["status"] == "initialize":
                    self._process_initialization_data(init_data["data"])

                    # Send acknowledgment
                    ack_msg = json.dumps({"status": "ready"})
                    sock.send(f"{ack_msg}\n".encode())

                    # Wait for start signal
                    start_response = sock.recv(1024).decode().strip()
                    start_data = json.loads(start_response)

                    if start_data["status"] == "start":
                        self.logger.info("Received start signal from bootstrap server")
                        sock.close()
                        return True

            sock.close()
            return False

        except Exception as e:
            self.logger.error(f"Error connecting to bootstrap server: {e}")
            return False

    def _process_initialization_data(self, data: Dict):
        """Process initialization data from bootstrap server"""
        self.node_id = data["node_id"]
        self.total_nodes = data["total_nodes"]
        self.byzantine_threshold = data["byzantine_threshold"]
        self.session_id = data["protocol_config"]["session_id"]
        self.peers = data["peers"]

        my_port = data["my_zmq_port"]

        self.logger.info(f"Initialized as Node {self.node_id}")
        self.logger.info(
            f"Network: {self.total_nodes} nodes, Byzantine threshold: {self.byzantine_threshold}"
        )
        self.logger.info(f"Session ID: {self.session_id}")

        # Set up ZeroMQ manager
        self.zmq_manager = ZeroMQManager(self.node_id, my_port, self.peers)

    def setup_p2p_communication(self) -> bool:
        """Set up peer-to-peer communication"""
        if not self.zmq_manager:
            self.logger.error("ZeroMQ manager not initialized")
            return False

        # Small delay to ensure all nodes are ready
        time.sleep(1)

        if not self.zmq_manager.setup_connections():
            return False

        # Start message handling
        self.zmq_manager.start_message_loop(self._handle_message)

        # Send heartbeat to all peers
        self._send_heartbeat()

        return True

    def _handle_message(self, message: MVBAMessage):
        """Handle incoming MVBA protocol messages"""
        self.logger.debug(
            f"Handling {message.msg_type.value} from node {message.sender_id}"
        )

        # Route message to appropriate handler
        if message.msg_type == MessageType.HEARTBEAT:
            self._handle_heartbeat(message)
        elif message.msg_type in [
            MessageType.SHARE,
            MessageType.VOTE,
            MessageType.LOCK,
            MessageType.READY,
            MessageType.FINISH,
        ]:
            self._handle_acid_message(message)
        elif message.msg_type in [MessageType.ELECTION, MessageType.CONFIRM]:
            self._handle_election_message(message)
        elif message.msg_type == MessageType.ECHOSHARE:
            self._handle_dr_message(message)
        else:
            self.logger.warning(f"Unknown message type: {message.msg_type}")

    def _handle_heartbeat(self, message: MVBAMessage):
        """Handle heartbeat messages"""
        self.logger.debug(f"Heartbeat from node {message.sender_id}")
        # Could add peer liveness tracking here

    def _handle_acid_message(self, message: MVBAMessage):
        """Handle ACIDh protocol messages"""
        # Placeholder for ACIDh protocol implementation
        self.logger.info(
            f"ACIDh: {message.msg_type.value} from the node {message.sender_id}"
        )

        if message.msg_type == MessageType.SHARE:
            self._handle_share_message(message)
        elif message.msg_type == MessageType.VOTE:
            self._handle_vote_message(message)

    def _extract_bytes_from_string(self, shard_str: str) -> bytes:
        """Convert string representation back to bytes"""
        if shard_str.startswith("bytearray(b'") and shard_str.endswith("')"):
            content = shard_str[12:-2]
            return content.encode("latin-1")
        elif shard_str.startswith("b'") and shard_str.endswith("'"):
            content = shard_str[2:-1]
            return content.encode("latin-1")
        else:
            return shard_str.encode("utf-8")

    def _send_vote_message(self, commitment: str):
        """Send VOTE message (Algorithm 9, line 14)"""
        vote_msg = MVBAMessage(
            msg_type=MessageType.VOTE,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="vote",
            data={"commitment": commitment},
        )

        self.zmq_manager.broadcast_message(vote_msg)
        self.logger.info(f"📤 Sent VOTE for commitment {commitment[:16]}...")

    def _handle_share_message(self, message: MVBAMessage):

        j = message.sender_id
        share_key = (j, message.session_id, message.protocol_id)

        # Line 11: "for the first time"
        if share_key in self.received_shares:
            self.logger.debug(f"Ignoring duplicate SHARE from node {j}")
            return

        self.received_shares.add(share_key)

        # Extract data
        C = message.data["commitment"]
        y_str = message.data["shard"]
        omega = message.data["proof"]

        # Convert shard back to bytes
        y = self._extract_bytes_from_string(y_str)

        self.logger.info(f"📦 Processing SHARE from node {j}")

        # Line 12: VcVerify(i, C, y, ω) = true

        is_valid = vc_verify(self.node_id, C, y, omega)

        if is_valid:
            self.logger.info(f"✅ Share verification successful for node {j}")

            # Line 13: Store share data
            self.acidh_shares[j] = (C, y, omega)
            self.acidh_hash[C] = j

            # Send VOTE message (Algorithm 9, line 14)
            self._send_vote_message(C)

            self._check_pending_votes()
        else:
            self.logger.warning(f"❌ Share verification failed for node {j}")

    def _send_lock_message(self, commitment: str):
        """Send LOCK message to all nodes (Algorithm 9, line 18)"""
        lock_msg = MVBAMessage(
            msg_type=MessageType.LOCK,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="lock",
            data={"commitment": commitment},
        )

        self.zmq_manager.broadcast_message(lock_msg)
        self.logger.info(f"📤 Sent LOCK for commitment {commitment[:16]}...")

    def _check_pending_votes(self):
        """
        Check if any pending votes can now be processed
        (Call this after receiving a new SHARE)
        """
        if not hasattr(self, "vote_counts"):
            return

        threshold = self.total_nodes - self.byzantine_threshold

        for commitment, voters in self.vote_counts.items():
            if len(voters) >= threshold and commitment in self.Hhash:
                # We have enough votes and the commitment is known
                j_star = self.Hhash[commitment]

                if j_star not in self.Llock or self.Llock[j_star] != 1:
                    # Haven't locked this yet
                    self.Llock[j_star] = 1
                    self.logger.info(f"🔒 Locked proposal from node {j_star} (delayed)")
                    self._send_lock_message(commitment)

                    # Clear votes to prevent reprocessing
                    self.vote_counts[commitment] = set()

    def _handle_vote_message(self, message: MVBAMessage):

        sender_id = message.sender_id
        commitment = message.data["commitment"]

        self.logger.debug(
            f"📥 Received VOTE from node {sender_id} for commitment {commitment[:16]}..."
        )

        # Initialize vote tracking if not exists
        if not hasattr(self, "vote_counts"):
            self.vote_counts = {}  # commitment -> set of voter node IDs

        if not hasattr(self, "acidh_locks"):
            self.acidh_locks = {}  # Llock[j] = 1 if node j's proposal is locked

        # Track votes per commitment from distinct nodes
        if commitment not in self.vote_counts:
            self.vote_counts[commitment] = set()

        # Add this voter (ignore duplicates automatically due to set)
        self.vote_counts[commitment].add(sender_id)

        vote_count = len(self.vote_counts[commitment])
        threshold = self.total_nodes - self.byzantine_threshold  # n - t

        self.logger.debug(
            f"📊 Vote count for {commitment[:16]}...: {vote_count}/{threshold}"
        )

        # Line 15: Check if we have n-t votes from distinct nodes
        if vote_count >= threshold:
            self.logger.info(
                f"🎯 Reached vote threshold ({vote_count}/{threshold}) for commitment {commitment[:16]}..."
            )

            # Line 16: Wait until C ∈ Hhash
            if commitment in self.acidh_hash:
                # Line 17: j⋆ ← Hhash[C]; Llock[j⋆] ← 1
                j_star = self.acidh_hash[commitment]
                self.acidh_locks[j_star] = 1

                self.logger.info(
                    f"🔒 Locked proposal from node {j_star} (commitment {commitment[:16]}...)"
                )

                # Line 18: send ("LOCK",ID, C) to all nodes
                self._send_lock_message(commitment)

                # Prevent processing this commitment again
                self.vote_counts[commitment] = set()  # Clear to prevent reprocessing

            else:
                # Commitment not in Hhash yet - we need to wait
                # This could happen if we receive votes before the corresponding SHARE
                self.logger.warning(
                    f"⏳ Have enough votes for {commitment[:16]}... but waiting for SHARE"
                )

    def _handle_election_message(self, message: MVBAMessage):
        """Handle election protocol messages"""
        # Placeholder for election protocol implementation
        self.logger.info(
            f"Election: {message.msg_type.value} from node {message.sender_id}"
        )
        # TODO: Implement election protocol logic

    def _handle_dr_message(self, message: MVBAMessage):
        """Handle data retrieval protocol messages"""
        # Placeholder for data retrieval implementation
        self.logger.info(f"DR: {message.msg_type.value} from node {message.sender_id}")
        # TODO: Implement DRh protocol logic

    def _send_heartbeat(self):
        """Send heartbeat to all peers"""
        if not self.zmq_manager:
            return

        heartbeat_msg = MVBAMessage(
            msg_type=MessageType.HEARTBEAT,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="heartbeat",
            data={"timestamp": time.time()},
        )

        self.zmq_manager.broadcast_message(heartbeat_msg)
        self.logger.debug("Sent heartbeat to all peers")

    def set_input_value(self, value: str):
        """Set the input value for MVBA protocol"""
        self.input_value = value
        self.logger.info(f"Input value set to: {value}")

    def start_mvba_protocol(self):
        """Start the MVBA protocol with the set input value"""

        if self.protocol_started:
            self.logger.warning("MVBA protocol already started")
            return False

        while not self.input_value:
            value = input("Enter input value for MVBA protocol: ")
            if predicate(value, self):
                self.set_input_value(value)
                break
            else:
                print("Input rejected by the predicate. Try again.")

        self.protocol_started = True
        self.shards = erasure_encode(
            self.total_nodes, self.byzantine_threshold, self.input_value
        )
        print("Input encoded using reed-solomon...")
        self.vc_commit = vc_commit(self.shards)
        print("Created vector commitment over encoded shards...")
        peer_ids = [peer["node_id"] for peer in self.peers]
        for peer_id in peer_ids:
            # self.zmq_manager.send_message(peer_id, shard)
            shard, proof = vc_open(self.shards, int(peer_id))
            message = MVBAMessage(
                msg_type=MessageType.SHARE,
                sender_id=self.node_id,
                session_id=self.session_id,
                protocol_id="share",
                data={
                    "shard": str(shard),
                    "commitment": self.vc_commit,
                    "proof": proof,
                    "peer_id": peer_id,
                },
            )
            self.zmq_manager.send_message(peer_id, message)
        # exit()

        return True

    def run(self):
        """Main run loop for the node"""
        print("\n\n\n")
        self.running = True

        try:
            while self.running:
                # Periodic heartbeat
                if self.zmq_manager:
                    self._send_heartbeat()

                # Check if we should start the protocol (for demo purposes)
                if not self.protocol_started:
                    time.sleep(2)  # Give time for all nodes to connect
                    self.start_mvba_protocol()

                time.sleep(5)  # Heartbeat interval

        except KeyboardInterrupt:
            self.logger.info("Node stopped by user")
        finally:
            self.stop()

    def stop(self):
        """Stop the node"""
        self.running = False

        if self.zmq_manager:
            self.zmq_manager.stop()

        self.logger.info("Node stopped")


def main():
    parser = argparse.ArgumentParser(description="MVBA Node Client")
    parser.add_argument(
        "--server-host",
        default="localhost",
        help="Bootstrap server host (default: localhost)",
    )
    parser.add_argument(
        "--server-port",
        "-p",
        type=int,
        default=8080,
        help="Bootstrap server port (default: 8080)",
    )
    parser.add_argument("--input", "-i", type=str, help="Input value for MVBA protocol")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Create and start node
    node = MVBANode(bootstrap_host=args.server_host, bootstrap_port=args.server_port)

    # Set input value if provided
    if args.input:
        node.set_input_value(args.input)

    # Connect to bootstrap server
    if not node.connect_to_bootstrap():
        print("Failed to connect to bootstrap server")
        return 1

    # Set up P2P communication
    if not node.setup_p2p_communication():
        print("Failed to set up P2P communication")
        return 1

    print(f"Node {node.node_id} ready! P2P connections established.")

    # Start the main run loop
    node.run()

    return 0


if __name__ == "__main__":
    sys.exit(main())
