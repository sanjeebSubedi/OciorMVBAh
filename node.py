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
import hashlib
import json
import logging
import secrets
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from erasure_coding import erasure_decode, erasure_encode
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

    COIN_COMMIT = "COIN_COMMIT"
    COIN_REVEAL = "COIN_REVEAL"

    # DRh protocol messages
    ECHOSHARE = "ECHOSHARE"

    # Internal messages
    HEARTBEAT = "HEARTBEAT"
    SHUTDOWN = "SHUTDOWN"
    INPUT_READY = "INPUT_READY"

    # --- ABBBA (biased BA) ---
    ABBBA_PREVOTE = "ABBBA_PREVOTE"
    ABBBA_VOTE = "ABBBA_VOTE"
    ABBBA_DECIDE = "ABBBA_DECIDE"

    # --- ABBA (un-biased BA) ---
    ABBA_PREVOTE = "ABBA_PREVOTE"
    ABBA_VOTE = "ABBA_VOTE"
    ABBA_DECIDE = "ABBA_DECIDE"


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
                # Bootstrap delivers IDs as strings ("0", "1", ...).
                # Keep *both* forms so the rest of the code (which uses ints)
                # and ZeroMQ (which uses the original strings) work seamlessly.
                peer_id_str = peer["node_id"]  # "2"
                peer_id_int = int(peer_id_str)
                peer_port = peer["zmq_port"]
                peer_address = peer["address"]

                dealer = self.context.socket(zmq.DEALER)
                dealer.setsockopt(zmq.IDENTITY, str(self.node_id).encode())
                dealer.connect(f"tcp://{peer_address}:{peer_port}")

                # Store under both key types
                self.dealer_sockets[peer_id_int] = dealer
                self.dealer_sockets[peer_id_str] = dealer

                self.logger.info(
                    f"Connected to peer {peer_id_int} on {peer_address}:{peer_port}"
                )

            return True

        except Exception as e:
            self.logger.error(f"Failed to set up connections: {e}")
            return False

    def send_message(self, target_node_id: int, message: MVBAMessage) -> bool:
        """Send message to specific node"""
        try:
            if target_node_id not in self.dealer_sockets:
                alt_id = (
                    str(target_node_id)
                    if isinstance(target_node_id, int)
                    else int(target_node_id)
                )
                if alt_id not in self.dealer_sockets:
                    self.logger.error(f"No connection to node {target_node_id}")
                    return False
                target_node_id = alt_id

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
        self.buffered_messages = []

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

        self.input_ready = False
        self.nodes_ready = set()
        self.all_nodes_ready = False
        self.sync_complete = False

        self.current_round = 1  # r starts at 1
        self.election_started = False
        self.coin_commit = {}  # {r: {node_id: H_j}}
        self.coin_reveal = {}  # {r: {node_id: s_j_hex}}
        self.coin_ready = {}  # {r: True when coin decided}
        self.leader_round = {}  # {r: l}

        # ------------- ABBBA / ABBA -------------
        self.abbba_state = {}  # key = leader l  →  dict with local data
        self.abba_state = {}  # key = leader l  →  dict with local data
        self.mvba_decided = False

        self.dr_state: Dict[int, Dict] = {}
        self.dr_started: set[int] = set()

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
        elif message.msg_type == MessageType.INPUT_READY:
            self._handle_input_ready(message)
        elif message.msg_type in [
            MessageType.SHARE,
            MessageType.VOTE,
            MessageType.LOCK,
            MessageType.READY,
            MessageType.FINISH,
        ]:
            if self.sync_complete:
                self._handle_acid_message(message)
            else:
                if not hasattr(self, "buffered_messages"):
                    self.buffered_messages = []
                self.buffered_messages.append(message)

        elif message.msg_type in [MessageType.ELECTION]:
            if self.sync_complete:
                self._handle_election_message(message)
            else:
                self.logger.debug(
                    f"Buffering {message.msg_type.value} until sync complete"
                )
                self.buffered_messages.append(message)
        elif message.msg_type == MessageType.CONFIRM:
            if self.sync_complete:
                self._handle_confirm_message(message)
            else:
                self.logger.debug(
                    f"Buffering {message.msg_type.value} until sync complete"
                )
                self.buffered_messages.append(message)
        elif message.msg_type in [MessageType.COIN_COMMIT, MessageType.COIN_REVEAL]:
            self._handle_coin_message(message)
        elif message.msg_type == MessageType.ECHOSHARE:
            self._handle_dr_message(message)

        # -------- ABBBA / ABBA ------------------------------------------------
        elif message.msg_type in [
            MessageType.ABBBA_PREVOTE,
            MessageType.ABBBA_VOTE,
            MessageType.ABBBA_DECIDE,
        ]:
            self._handle_abbba_message(message)
        elif message.msg_type in [
            MessageType.ABBA_PREVOTE,
            MessageType.ABBA_VOTE,
            MessageType.ABBA_DECIDE,
        ]:
            self._handle_abba_message(message)
        else:
            self.logger.warning(f"Unknown message type: {message.msg_type}")

    def _handle_heartbeat(self, message: MVBAMessage):
        """Handle heartbeat messages"""
        self.logger.debug(f"Heartbeat from node {message.sender_id}")
        # Could add peer liveness tracking here

    def _handle_input_ready(self, message: MVBAMessage):
        """Handle INPUT_READY messages from other nodes"""
        sender_id = message.sender_id

        if sender_id not in self.nodes_ready:
            self.nodes_ready.add(sender_id)
            self.logger.info(
                f"📝 Node {sender_id} reported ready ({len(self.nodes_ready)}/{self.total_nodes})"
            )

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
        elif message.msg_type == MessageType.LOCK:
            self._handle_lock_message(message)
        elif message.msg_type == MessageType.READY:
            self._handle_ready_message(message)
        elif message.msg_type == MessageType.FINISH:
            self._handle_finish_message(message)

    def _extract_bytes_from_string(self, shard_str: str) -> bytes:
        """
        Convert the JSON-serialised shard back to bytes.

        1.  First try pure-hex (the format we now transmit).
        2.  Fall back to the two legacy `repr()` formats that appeared
            before the switch.
        3.  Finally treat it as a UTF-8 literal.
        """
        # 1. new format – plain hex
        try:
            return bytes.fromhex(shard_str)
        except ValueError:
            pass

        # 2. legacy formats
        if shard_str.startswith("bytearray(b'") and shard_str.endswith("')"):
            return shard_str[12:-2].encode("latin-1")
        if shard_str.startswith("b'") and shard_str.endswith("'"):
            return shard_str[2:-1].encode("latin-1")

        # 3. last resort
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
            if C not in self.acidh_hash:
                self.acidh_hash[C] = j
            else:
                self.acidh_hash[C] = min(self.acidh_hash[C], j)

            self.acidh_shares[j] = (C, y, omega)

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
            if len(voters) >= threshold and commitment in self.acidh_hash:
                # We have enough votes and the commitment is known
                j_star = self.acidh_hash[commitment]

                if j_star not in self.acidh_locks or self.acidh_locks[j_star] != 1:
                    # Haven't locked this yet
                    self.acidh_locks[j_star] = 1
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
                if self.acidh_locks.get(j_star, 0) != 1:
                    self.acidh_locks[j_star] = 1

                    self.logger.info(
                        f"🔒 Locked proposal from node {j_star} (commitment {commitment[:16]}...)"
                    )

                    # Line 18: send ("LOCK",ID, C) to all nodes
                    self._send_lock_message(commitment)

                    # Prevent processing this commitment again
                    # Clear to prevent reprocessing
                else:
                    self.logger.debug(
                        f"💤 Proposal from node {j_star} already locked, ignoring additional votes"
                    )
                self.vote_counts[commitment] = set()

            else:
                # Commitment not in Hhash yet - we need to wait
                # This could happen if we receive votes before the corresponding SHARE
                self.logger.warning(
                    f"⏳ Have enough votes for {commitment[:16]}... but waiting for SHARE"
                )

    def _send_ready_message(self, commitment: str):
        ready_msg = MVBAMessage(
            msg_type=MessageType.READY,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="ready",
            data={"commitment": commitment},
        )
        self.zmq_manager.broadcast_message(ready_msg)
        self.logger.info(f"📤 Sent READY for commitment {commitment[:16]}...")

    def _handle_lock_message(self, message: MVBAMessage):
        """
        ACID-Ready Phase (Algorithm 9, lines 19–22):
        upon receiving n−t ("LOCK",ID,C) from distinct nodes:
            wait until C ∈ acidh_hash
            j⋆ ← acidh_hash[C]; Rready[j⋆] ← 1
            send ("READY",ID,C) to all nodes
        """
        sender_id = message.sender_id
        commitment = message.data["commitment"]

        self.logger.debug(
            f"🔐 Received LOCK from node {sender_id} for commitment {commitment[:16]}..."
        )

        # initialize lock_counts map if needed
        if not hasattr(self, "lock_counts"):
            self.lock_counts = {}  # commitment → set of sender IDs

        if commitment not in self.lock_counts:
            self.lock_counts[commitment] = set()
        self.lock_counts[commitment].add(sender_id)
        lock_count = len(self.lock_counts[commitment])
        threshold = self.total_nodes - self.byzantine_threshold  # n - t

        if lock_count >= threshold:
            # Ensure we know the SHARE first (Alg 9 line 20)
            if commitment in self.acidh_hash:
                j_star = self.acidh_hash[commitment]

                # Mark Rready[j⋆] ← 1  (initialized in initialize_state)
                self.acidh_ready[j_star] = 1

                if not hasattr(self, "ready_sent"):
                    self.ready_sent = set()
                if commitment not in self.ready_sent:
                    self.ready_sent.add(commitment)
                    self._send_ready_message(commitment)

                self.logger.info(
                    f"✅ Ready to deliver proposal from node {j_star} (commitment {commitment[:16]}...)"
                )

                # Broadcast READY only once
                # if not hasattr(self, "ready_sent"):
                #     self.ready_sent = set()
                # self.ready_sent.add(commitment)

                # self._send_ready_message(commitment)

                # Clear lock counts to avoid re-processing
                self.lock_counts[commitment] = set()
            else:
                # We have locks but not the share yet – keep waiting
                self.logger.debug(
                    f"⏳ Have {lock_count}/{threshold} LOCKs for {commitment[:16]}..., waiting for SHARE"
                )

    def _send_finish_message(self, target_node_id: int, commitment: str):
        """Broadcast FINISH message to all nodes (corrected from algorithm spec)."""
        finish_msg = MVBAMessage(
            msg_type=MessageType.FINISH,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="finish",
            data={"commitment": commitment},
        )

        # FIXED: Broadcast FINISH to all nodes instead of just the proposer
        # This ensures all nodes can receive n-t FINISH messages to trigger ELECTION
        self._handle_acid_message(finish_msg)  # local delivery
        self.zmq_manager.broadcast_message(finish_msg)  # broadcast to all peers

        self.logger.info(
            f"📤 Sent FINISH for commitment {commitment[:16]}... to all nodes"
        )

    def _handle_ready_message(self, message: MVBAMessage):
        """
        ACID-finish phase (Algorithm 9 lines 23-26):

        23  upon receiving n−t ("READY",ID,C) from distinct nodes, for the same C do
        24      wait until C ∈ Hhash
        25      j⋆ ← Hhash[C];  Ffinish[j⋆] ← 1
        26      send ("FINISH",ID) to Pj⋆
        """
        sender_id = message.sender_id
        commitment = message.data["commitment"]

        self.logger.debug(
            f"📨 READY from node {sender_id} for commitment {commitment[:16]}..."
        )

        # Track READYs per commitment
        if not hasattr(self, "ready_counts"):
            self.ready_counts: Dict[str, set] = {}

        if commitment not in self.ready_counts:
            self.ready_counts[commitment] = set()
        self.ready_counts[commitment].add(sender_id)

        ready_cnt = len(self.ready_counts[commitment])
        threshold = self.total_nodes - self.byzantine_threshold  # n − t

        if ready_cnt >= threshold:
            # Ensure the SHARE for this commitment is already known (Alg 9 line 24)
            if commitment in self.acidh_hash:
                j_star = self.acidh_hash[commitment]

                # Already finished for j⋆ ?
                if self.acidh_finish.get(j_star, 0) == 1:
                    return

                # Line 25: mark Ffinish[j⋆] ← 1
                self.acidh_finish[j_star] = 1
                self.logger.info(
                    f"🏁 FINISH conditions met for proposal of node {j_star} "
                    f"(commitment {commitment[:16]}...)"
                )

                # Line 26: send FINISH to Pj⋆   (only once per commitment)
                if not hasattr(self, "finish_sent"):
                    self.finish_sent = set()
                if commitment not in self.finish_sent:
                    self.finish_sent.add(commitment)
                    self._send_finish_message(j_star, commitment)

                # clear to avoid re-trigger
                self.ready_counts[commitment] = set()
            else:
                # SHARE not seen yet; wait
                self.logger.debug(
                    f"⏳ {ready_cnt}/{threshold} READY for {commitment[:16]}..., "
                    f"waiting for SHARE"
                )

    def _send_election_message(self, commitment: str):
        """Broadcast ELECTION once ACID[(ID,j)] is finished"""
        election_msg = MVBAMessage(
            msg_type=MessageType.ELECTION,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="election",
            data={"commitment": commitment},
        )
        self._handle_election_message(election_msg)
        self.zmq_manager.broadcast_message(election_msg)
        self.logger.info(f"📤 Sent ELECTION for commitment {commitment[:16]}...")

    def _handle_finish_message(self, message: MVBAMessage):
        """
        Algorithm 9, lines 27-28
         27 upon receiving n−t ("FINISH",ID) from distinct nodes do
         28     send ("ELECTION",ID) to all nodes
        """
        sender_id = message.sender_id
        commitment = message.data["commitment"]

        self.logger.debug(
            f"🏁 FINISH from node {sender_id} for commitment {commitment[:16]}..."
        )

        # Track FINISH messages per commitment
        if not hasattr(self, "finish_counts"):
            self.finish_counts: Dict[str, set] = {}

        if commitment not in self.finish_counts:
            self.finish_counts[commitment] = set()
        self.finish_counts[commitment].add(sender_id)

        finish_cnt = len(self.finish_counts[commitment])
        threshold = self.total_nodes - self.byzantine_threshold  # n − t

        # Already sent ELECTION for this commitment?
        if hasattr(self, "election_sent") and commitment in self.election_sent:
            return

        if finish_cnt >= threshold:
            # Broadcast ELECTION exactly once
            if not hasattr(self, "election_sent"):
                self.election_sent = set()
            self.election_sent.add(commitment)
            self._send_election_message(commitment)

            # Clear to avoid re-trigger
            self.finish_counts[commitment] = set()

    def _handle_election_message(self, message: MVBAMessage):
        sender_id = message.sender_id
        commitment = message.data["commitment"]  # kept only for logging

        self.logger.info(f"Election: ELECTION from node {sender_id}")

        # Track senders globally (per-ID, not per-commitment)
        if not hasattr(self, "election_senders"):
            self.election_senders: set[int] = set()

        self.election_senders.add(sender_id)

        # n − t distinct nodes trigger first CONFIRM
        if len(self.election_senders) >= self.total_nodes - self.byzantine_threshold:
            if not getattr(self, "confirm_already_sent", False):
                self.confirm_already_sent = True
                # any commitment is fine – use our own if we have it
                chosen_C = getattr(self, "vc_commit", commitment)
                self._send_confirm_message(chosen_C)

    def _send_confirm_message(self, commitment: str):
        """Broadcast CONFIRM once (we also process it locally)."""
        confirm_msg = MVBAMessage(
            msg_type=MessageType.CONFIRM,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="confirm",
            data={"commitment": commitment},
        )

        # Process our own CONFIRM first so our vote counts
        self._handle_confirm_message(confirm_msg)

        # Then broadcast to peers
        self.zmq_manager.broadcast_message(confirm_msg)
        self.logger.info(f"📤 Sent CONFIRM for commitment {commitment[:16]}...")

    def _handle_confirm_message(self, message: MVBAMessage):
        sender_id = message.sender_id
        commitment = message.data["commitment"]

        self.logger.debug(f"CONFIRM from node {sender_id}")

        if not hasattr(self, "confirm_senders"):
            self.confirm_senders: set[int] = set()

        # store once per node
        self.confirm_senders.add(sender_id)

        t = self.byzantine_threshold

        # Amplify phase – send CONFIRM after t+1 distinct confirmations
        if len(self.confirm_senders) >= t + 1 and not getattr(
            self, "confirm_already_sent", False
        ):
            self.confirm_already_sent = True
            self._send_confirm_message(commitment)

        # Termination: 2t + 1 confirmations
        if len(self.confirm_senders) >= 2 * t + 1:
            self.logger.info("✅ CONFIRM phase complete – ready for DRh / decision.")

            if not self.election_started:
                self._start_election_round()

    def _broadcast_coin_commit(self, r: int, commit_hex: str):
        msg = MVBAMessage(
            msg_type=MessageType.COIN_COMMIT,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id=f"coin_commit_round_{r}",
            data={"round": r, "commit": commit_hex},
        )
        self.zmq_manager.broadcast_message(msg)

    def _broadcast_coin_reveal(self, r: int, share_hex: str):
        msg = MVBAMessage(
            msg_type=MessageType.COIN_REVEAL,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id=f"coin_reveal_round_{r}",
            data={"round": r, "share": share_hex},
        )
        self.zmq_manager.broadcast_message(msg)

    # ---- round bootstrap -------------------------------------------------
    def _start_election_round(self):
        if self.election_started:
            return
        self.election_started = True

        r = self.current_round
        # generate local random share s_i
        s_bytes = secrets.token_bytes(32)
        s_hex = s_bytes.hex()
        H_hex = hashlib.sha256(s_bytes).hexdigest()

        # record our own commit immediately
        self.coin_commit.setdefault(r, {})[self.node_id] = H_hex

        self.logger.info(f"🔑 [Round {r}] Broadcasting COIN_COMMIT")
        self._broadcast_coin_commit(r, H_hex)

        # store share for later reveal
        self._my_coin_share = (r, s_hex)

    # ---- message dispatcher ---------------------------------------------
    def _handle_coin_message(self, msg: MVBAMessage):
        if msg.msg_type == MessageType.COIN_COMMIT:
            self._handle_coin_commit(msg)
        else:
            self._handle_coin_reveal(msg)

    # ---- commit ----------------------------------------------------------
    def _handle_coin_commit(self, msg: MVBAMessage):
        r = msg.data["round"]
        H = msg.data["commit"]

        self.coin_commit.setdefault(r, {})[msg.sender_id] = H

        threshold = self.total_nodes - self.byzantine_threshold
        if len(
            self.coin_commit[r]
        ) >= threshold and self.node_id not in self.coin_reveal.get(r, {}):
            # reveal our own share now
            if hasattr(self, "_my_coin_share") and self._my_coin_share[0] == r:
                _, s_hex = self._my_coin_share
                self.coin_reveal.setdefault(r, {})[self.node_id] = s_hex
                self.logger.info(f"🔑 [Round {r}] Broadcasting COIN_REVEAL")
                self._broadcast_coin_reveal(r, s_hex)

    # ---- reveal & coin decision -----------------------------------------
    def _handle_coin_reveal(self, msg: MVBAMessage):
        r = msg.data["round"]
        s_hex = msg.data["share"]

        # need matching commit
        commit_dict = self.coin_commit.get(r, {})
        if msg.sender_id not in commit_dict:
            return  # commit not seen yet, ignore for now

        H_expected = commit_dict[msg.sender_id]
        if hashlib.sha256(bytes.fromhex(s_hex)).hexdigest() != H_expected:
            return  # invalid reveal

        self.coin_reveal.setdefault(r, {})[msg.sender_id] = s_hex

        threshold = self.total_nodes - self.byzantine_threshold
        if len(self.coin_reveal[r]) >= threshold:  # ≥ n−t reveals
            # deterministic ordering → identical hash everywhere
            sorted_ids = sorted(self.coin_reveal[r])
            concat = b"".join(
                bytes.fromhex(self.coin_reveal[r][sid]) for sid in sorted_ids
            )
            coin_val = int.from_bytes(hashlib.sha256(concat).digest(), "big")
            l = coin_val % self.total_nodes

            # ---------- UPDATED LOGIC ----------
            # Always start ABBBA for the final leader that the
            # common-coin selects, even if a provisional leader was
            # handled earlier.
            prev = self.leader_round.get(r)
            if prev != l:  # leader changed
                self.leader_round[r] = l
                self.logger.info(
                    f"🎲 [Round {r}] Common coin -> leader {l}"
                    + (" (updated)" if prev is not None else "")
                )

                if l not in self.abbba_state:  # spawn once per leader
                    self._start_abbba_instance(l)

            self.coin_ready[r] = True

    def _start_abbba_instance(self, leader_id: int):
        """
        Spawn ABBBA(ID, leader_id) with inputs
            a1 = Rready[leader_id] ,  a2 = Ffinish[leader_id]
        """
        a1 = self.acidh_ready.get(leader_id, 0)
        a2 = self.acidh_finish.get(leader_id, 0)

        st = {
            "a1": a1,
            "a2": a2,
            "prevotes": {},  # node_id → (a1,a2)
            "votes": {},  # node_id → bit
            "decided": None,
        }
        self.abbba_state[leader_id] = st

        # Broadcast PREVOTE
        msg = MVBAMessage(
            msg_type=MessageType.ABBBA_PREVOTE,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id=f"abbba_prev_{leader_id}",
            data={
                "leader": leader_id,
                "a1": a1,
                "a2": a2,
            },
        )
        self._handle_abbba_message(msg)  # local delivery
        self.zmq_manager.broadcast_message(msg)
        self.logger.info(f"🔄 ABBBA[{leader_id}] PREVOTE (a1={a1},a2={a2}) sent")

    # ---------------- handlers -----------------
    def _handle_abbba_message(self, msg: MVBAMessage):
        l = msg.data["leader"]
        st = self.abbba_state.setdefault(
            l, {"a1": 0, "a2": 0, "prevotes": {}, "votes": {}, "decided": None}
        )

        if msg.msg_type == MessageType.ABBBA_PREVOTE:
            st["prevotes"][msg.sender_id] = (msg.data["a1"], msg.data["a2"])

            # When we have n-t PREVOTEs we compute our VOTE
            if (
                len(st["prevotes"]) >= self.total_nodes - self.byzantine_threshold
                and self.node_id not in st["votes"]
            ):

                # Decision rule (simple version):
                has_a2 = any(a2 for (_, a2) in st["prevotes"].values())
                a1_count = sum(a1 for (a1, _) in st["prevotes"].values())
                bit = 1 if has_a2 or a1_count >= self.byzantine_threshold + 1 else 0

                st["votes"][self.node_id] = bit

                vote_msg = MVBAMessage(
                    msg_type=MessageType.ABBBA_VOTE,
                    sender_id=self.node_id,
                    session_id=self.session_id,
                    protocol_id=f"abbba_vote_{l}",
                    data={"leader": l, "bit": bit},
                )
                self._handle_abbba_message(vote_msg)
                self.zmq_manager.broadcast_message(vote_msg)
                self.logger.info(f"🔄 ABBBA[{l}] VOTE({bit}) broadcast")

        elif msg.msg_type == MessageType.ABBBA_VOTE:
            bit = msg.data["bit"]
            st["votes"][msg.sender_id] = bit

            # Decide when n-t identical votes received
            threshold = self.total_nodes - self.byzantine_threshold
            for b in (0, 1):
                if (
                    list(st["votes"].values()).count(b) >= threshold
                    and st["decided"] is None
                ):
                    st["decided"] = b

                    decide_msg = MVBAMessage(
                        msg_type=MessageType.ABBBA_DECIDE,
                        sender_id=self.node_id,
                        session_id=self.session_id,
                        protocol_id=f"abbba_decide_{l}",
                        data={"leader": l, "bit": b},
                    )
                    self._handle_abbba_message(decide_msg)
                    self.logger.info(f"✅ ABBBA[{l}] decided {b}")

                    # Start ABBA with input = b
                    self._start_abba_instance(l, b)

        elif msg.msg_type == MessageType.ABBBA_DECIDE:
            # (Not used – we decide locally and immediately continue to ABBA)
            pass

    # ------------------------------------------------------------------ #
    #                    -------  ABBA (un-biased) -------               #
    # ------------------------------------------------------------------ #
    def _start_abba_instance(self, leader_id: int, input_bit: int):
        if self.mvba_decided:
            return

        st = {
            "input": input_bit,
            "prevotes": {self.node_id: input_bit},
            "votes": {},
            "decided": None,
        }
        self.abba_state[leader_id] = st

        prevote_msg = MVBAMessage(
            msg_type=MessageType.ABBA_PREVOTE,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id=f"abba_prev_{leader_id}",
            data={"leader": leader_id, "bit": input_bit},
        )
        self._handle_abba_message(prevote_msg)
        self.zmq_manager.broadcast_message(prevote_msg)
        self.logger.info(f"🔄 ABBA[{leader_id}] PREVOTE({input_bit}) broadcast")

    def _handle_abba_message(self, msg: MVBAMessage):
        l = msg.data["leader"]
        st = self.abba_state.setdefault(
            l, {"input": 0, "prevotes": {}, "votes": {}, "decided": None}
        )

        if msg.msg_type == MessageType.ABBA_PREVOTE:
            st["prevotes"][msg.sender_id] = msg.data["bit"]

            if (
                len(st["prevotes"]) >= self.total_nodes - self.byzantine_threshold
                and self.node_id not in st["votes"]
            ):
                # Majority of prevotes
                zeros = list(st["prevotes"].values()).count(0)
                ones = len(st["prevotes"]) - zeros
                bit = 1 if ones > zeros else 0

                st["votes"][self.node_id] = bit
                vote_msg = MVBAMessage(
                    msg_type=MessageType.ABBA_VOTE,
                    sender_id=self.node_id,
                    session_id=self.session_id,
                    protocol_id=f"abba_vote_{l}",
                    data={"leader": l, "bit": bit},
                )
                self._handle_abba_message(vote_msg)
                self.zmq_manager.broadcast_message(vote_msg)
                self.logger.info(f"🔄 ABBA[{l}] VOTE({bit}) broadcast")

        elif msg.msg_type == MessageType.ABBA_VOTE:
            bit = msg.data["bit"]
            st["votes"][msg.sender_id] = bit

            # 2t+1 identical votes → decision
            if (
                list(st["votes"].values()).count(bit)
                >= 2 * self.byzantine_threshold + 1
                and st["decided"] is None
            ):
                st["decided"] = bit

                self.logger.info(f"🏁 ABBA[{l}] DECIDE {bit}")
                if bit == 1 and not self.mvba_decided:
                    self._invoke_drh_and_attempt_output(l)

        elif msg.msg_type == MessageType.ABBA_DECIDE:
            pass  # (not used – local decide)

    def _send_echoshare_message(self, leader_id: int, C: str, y: bytes, omega):
        """Broadcast DRh ECHOSHARE"""
        msg = MVBAMessage(
            msg_type=MessageType.ECHOSHARE,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id=f"dr_echoshare_{leader_id}",
            data={
                "leader": leader_id,
                "commitment": C,
                "shard": y.hex(),
                "proof": omega,
            },
        )
        self._handle_dr_message(msg)  # local delivery
        self.logger.debug(f"Sending shard {y.hex()[:16]}…")
        self.zmq_manager.broadcast_message(msg)

    def _start_drh_instance(self, leader_id: int):
        if hasattr(self, "dr_started") and leader_id in self.dr_started:
            return
        self.dr_started = getattr(self, "dr_started", set())
        self.dr_started.add(leader_id)
        self.dr_state.setdefault(leader_id, {"Y": {}, "done": False})

        lock_ind = self.acidh_locks.get(leader_id, 0)
        C, y, omega = self.acidh_shares.get(leader_id, (-1, -1, -1))
        if lock_ind == 1 and C != -1:
            self.logger.info(f"📤 DRh[{leader_id}] sending our ECHOSHARE")
            self._send_echoshare_message(leader_id, C, y, omega)

    def _handle_dr_message(self, message: MVBAMessage):
        if message.msg_type != MessageType.ECHOSHARE:
            return
        l = message.data["leader"]
        C = message.data["commitment"]
        y = self._extract_bytes_from_string(message.data["shard"])
        omega = message.data["proof"]
        j = message.sender_id

        # deduplicate
        key = (l, C, j)
        self.echoshare_seen = getattr(self, "echoshare_seen", set())
        if key in self.echoshare_seen:
            return
        self.echoshare_seen.add(key)

        if not vc_verify(j, C, y, omega):
            return

        leader_st = self.dr_state.setdefault(l, {"Y": {}, "done": False})
        Ys = leader_st["Y"].setdefault(C, {})
        Ys[j] = y

        if not leader_st["done"] and len(Ys) >= self.byzantine_threshold + 1:
            self._attempt_drh_output(l, C)

    def _invoke_drh_and_attempt_output(self, leader_id: int):
        # already decided → nothing to do
        if self.mvba_decided:
            return
        # start or continue DRh for this leader
        self._start_drh_instance(leader_id)

    def _attempt_drh_output(self, leader_id: int, commitment: str):
        st = self.dr_state[leader_id]
        if st["done"]:
            return

        shares = st["Y"][commitment]  # dict {j: yj}
        try:
            # FIXED: Pass the leader_id (proposer) to decode with correct Vandermonde matrix
            w_bytes = erasure_decode(
                self.total_nodes, self.byzantine_threshold, shares, leader_id
            )
        except Exception as e:
            self.logger.warning(f"DRh decode error: {e}")
            return

        # Re-commit and compare (use leader's node_id for consistency)
        expected_shards = erasure_encode(
            self.total_nodes, self.byzantine_threshold, w_bytes, leader_id
        )
        if vc_commit(expected_shards) != commitment:
            self.logger.warning("DRh commitment mismatch")
            return

        w_hat = w_bytes.decode("utf-8", errors="ignore")
        if predicate(w_hat, self):
            st["done"] = True
            self.mvba_decided = True
            self.logger.info(
                f"🎉 MVBA DECIDED on value from leader {leader_id}: {w_hat}"
            )

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

    def start_communication(self):
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

        self.input_ready = True
        self._broadcast_input_ready()
        self.nodes_ready.add(self.node_id)

        # Wait for all nodes to be ready
        self._wait_for_all_nodes_ready()
        return True

    def _broadcast_input_ready(self):
        """Broadcast that this node has input ready"""
        ready_msg = MVBAMessage(
            msg_type=MessageType.INPUT_READY,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="sync",
            data={"node_id": self.node_id},
        )

        self.zmq_manager.broadcast_message(ready_msg)

    def _wait_for_all_nodes_ready(self):
        """Wait until all nodes have reported ready"""
        while not self.all_nodes_ready:
            time.sleep(0.5)  # Check every 500ms

            # Check if we have all nodes ready
            if len(self.nodes_ready) == self.total_nodes:
                self.all_nodes_ready = True
                print(f"[Node {self.node_id}] 🚀 All nodes ready! Starting protocol...")
                self._start_mvba_protocol()
                break

    def _start_mvba_protocol(self):
        """Actually start the protocol once all nodes are ready"""

        "\n === MVBA Protocol STARTED ==== \n"

        if self.sync_complete:
            return

        self.sync_complete = True
        self.protocol_started = True
        # FIXED: Pass node_id to ensure unique commitments per node
        self.shards = erasure_encode(
            self.total_nodes, self.byzantine_threshold, self.input_value, self.node_id
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
                    "shard": shard.hex(),
                    "commitment": self.vc_commit,
                    "proof": proof,
                    "peer_id": peer_id,
                },
            )
            self.zmq_manager.send_message(peer_id, message)

        shard, proof = vc_open(self.shards, self.node_id)
        self_message = MVBAMessage(
            msg_type=MessageType.SHARE,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="share",
            data={
                "shard": shard.hex(),
                "commitment": self.vc_commit,
                "proof": proof,
                "peer_id": self.node_id,
            },
        )
        # Process self-SHARE immediately
        self._handle_acid_message(self_message)
        print(f"[Node {self.node_id}] 📤 Sent SHARE messages to all peers")

        if hasattr(self, "buffered_messages") and self.buffered_messages:
            # Sort messages: SHARE first, then VOTE, then others
            share_messages = [
                msg
                for msg in self.buffered_messages
                if msg.msg_type == MessageType.SHARE
            ]
            vote_messages = [
                msg
                for msg in self.buffered_messages
                if msg.msg_type == MessageType.VOTE
            ]
            other_messages = [
                msg
                for msg in self.buffered_messages
                if msg.msg_type not in [MessageType.SHARE, MessageType.VOTE]
            ]

            all_ordered_messages = share_messages + vote_messages + other_messages

            self.logger.info(
                f"Processing {len(all_ordered_messages)} buffered messages ({len(share_messages)} SHARE, {len(vote_messages)} VOTE)"
            )

            for msg in all_ordered_messages:
                if msg.msg_type in [
                    MessageType.SHARE,
                    MessageType.VOTE,
                    MessageType.LOCK,
                    MessageType.READY,
                    MessageType.FINISH,
                ]:
                    self._handle_acid_message(msg)
                elif msg.msg_type in [MessageType.ELECTION, MessageType.CONFIRM]:
                    self._handle_election_message(msg)
                elif msg.msg_type == MessageType.ECHOSHARE:
                    self._handle_dr_message(msg)

            self.buffered_messages.clear()

    def run(self):
        """Main run loop for the node"""
        self.running = True

        try:
            while self.running:
                # Periodic heartbeat
                if self.zmq_manager:
                    self._send_heartbeat()

                # Check if we should start the protocol (for demo purposes)
                if not self.input_ready and not self.protocol_started:
                    time.sleep(1)  # Give time for all nodes to connect
                    self.start_communication()

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
