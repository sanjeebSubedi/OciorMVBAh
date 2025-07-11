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
from typing import Any, Dict, List, Optional

# Cryptographic utilities
from crypto import erasure_decode, erasure_encode, vc_commit, vc_open, vc_verify
from predicate import predicate

# New, canonical message definitions
from protocol.common.message import MessageType, MVBAMessage

# ZeroMQ imports
try:
    import zmq
except ImportError:
    print("ERROR: ZeroMQ not installed. Install with: pip install pyzmq")
    sys.exit(1)

from core.router import MessageRouter
from network.zmq_manager import ZeroMQManager

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


# Message classes defined in protocol.common.message for consistency across all modules


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

        # Note: Protocol state is now managed by individual protocol modules
        # - ACIDh state → protocol.acid.state.ACIDState
        # - Election/DR state → managed locally in MVBANode methods

        self.logger = logging.getLogger("MVBANode")

        # Message routing
        self.router = MessageRouter(self.logger)
        # Protocol sub-handlers
        from protocol.acid.handler import ACIDHandler  # local import to avoid cycles
        from protocol.drh.handler import DRhHandler

        self.acid_handler = ACIDHandler(self)
        self.drh_handler = DRhHandler(self)
        self._register_router_handlers()

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

    def initialize_state(self):
        """Initialize node state.

        ACIDh state variables are now managed by ACIDState and mirrored
        via ACIDHandler as needed for ABBBA/ABBA coordination.
        """
        # ACIDh state is now managed by ACIDState class - these are just mirrors
        # that get updated by ACIDHandler.handle() when needed
        self.acidh_locks = {}
        self.acidh_ready = {}
        self.acidh_finish = {}

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
        self.zmq_manager.start_message_loop(self.router.dispatch)

        # Send heartbeat to all peers
        self._send_heartbeat()

        return True

    def _handle_heartbeat(self, message: MVBAMessage):
        """Handle heartbeat messages"""
        sender_id = message.sender_id
        data = message.data

        # Only log heartbeats with interesting state
        if data.get("mvba_decided"):
            self.logger.info(f"📍 Node {sender_id} has decided MVBA")
        elif "abba_decisions" in data and any(
            v == 1 for v in data["abba_decisions"].values()
        ):
            self.logger.info(
                f"📍 Node {sender_id}: ABBA decisions with 1: {data['abba_decisions']}"
            )

    def _handle_input_ready(self, message: MVBAMessage):
        """Handle INPUT_READY messages from other nodes"""
        sender_id = message.sender_id

        if sender_id not in self.nodes_ready:
            self.nodes_ready.add(sender_id)
            self.logger.info(
                f"📝 Node {sender_id} reported ready ({len(self.nodes_ready)}/{self.total_nodes})"
            )

    # _extract_bytes_from_string method removed - now encapsulated in ACIDState

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

        NOTE: This method handles inter-protocol coordination by counting
        FINISH messages to trigger the ELECTION phase. It is called by
        the ACIDHandler to ensure proper protocol flow.
        """
        sender_id = message.sender_id
        commitment = message.data["commitment"]

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
        if self.mvba_decided:
            return
        sender_id = message.sender_id
        commitment = message.data["commitment"]  # kept only for logging

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

        def _crash_node(node_id: int):
            self.logger.warning("Simulating crash-stop right after CONFIRM")
            sys.stdout.flush()
            sys.stderr.flush()
            import os

            os._exit(0)

        if TEST_ONE_NODE_CRASH and int(self.node_id) == 1:
            _crash_node(int(self.node_id))
        elif TEST_TWO_NODE_CRASH and int(self.node_id) in (1, 2):
            _crash_node(int(self.node_id))

    def _handle_confirm_message(self, message: MVBAMessage):
        sender_id = message.sender_id
        commitment = message.data["commitment"]

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

    def _handle_coin_message(self, msg: MVBAMessage):
        if msg.msg_type == MessageType.COIN_COMMIT:
            self._handle_coin_commit(msg)
        else:
            self._handle_coin_reveal(msg)

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

    def _handle_coin_reveal(self, msg: MVBAMessage):
        r = msg.data["round"]
        s_hex = msg.data["share"]

        # validity checks
        commit_dict = self.coin_commit.get(r, {})
        if msg.sender_id not in commit_dict:  # commit missing
            return
        H_expected = commit_dict[msg.sender_id]
        if hashlib.sha256(bytes.fromhex(s_hex)).hexdigest() != H_expected:
            return  # bad reveal

        # store the reveal
        self.coin_reveal.setdefault(r, {})[msg.sender_id] = s_hex

        threshold = self.total_nodes - self.byzantine_threshold  # n − t
        commit_ids = sorted(commit_dict)  # IDs with COMMIT
        if len(commit_ids) < threshold:
            return  # not enough commits

        canonical = commit_ids[:threshold]  # fixed subset

        # wait until each canonical ID has revealed
        if any(i not in self.coin_reveal[r] for i in canonical):
            return

        # All REVEALs from the canonical subset are present – derive coin.
        concat = b"".join(bytes.fromhex(self.coin_reveal[r][sid]) for sid in canonical)
        coin_val = int.from_bytes(hashlib.sha256(concat).digest(), "big")
        l = coin_val % self.total_nodes

        # remainder identical to previous version
        prev = self.leader_round.get(r)
        if prev != l:  # first time or changed
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
        # Fetch latest flags directly from the ACIDʰ state machine (authoritative)
        acid_state = None
        if hasattr(self, "acid_handler") and getattr(self.acid_handler, "_state", None):
            acid_state = self.acid_handler._state  # type: ignore[attr-defined]
            a1 = acid_state.acidh_ready.get(leader_id, 0)
            a2 = acid_state.acidh_finish.get(leader_id, 0)
        else:
            a1 = self.acidh_ready.get(leader_id, 0)
            a2 = self.acidh_finish.get(leader_id, 0)

        # Debug: dump readiness/finish flags for tracing extra-round issue
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(
                "ABBBA start r=%s leader=%s a1=%s a2=%s | state_ready=%s state_finish=%s",
                self.current_round,
                leader_id,
                a1,
                a2,
                (acid_state.acidh_ready.get(leader_id) if acid_state else "n/a"),
                (acid_state.acidh_finish.get(leader_id) if acid_state else "n/a"),
            )

        if TEST_FORCE_EXTRA_ROUND and self.current_round == 1:
            a1 = a2 = 0

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

    def _maybe_finish_round(self) -> None:
        """
        Called after every ABBA decision.
        If  (i)  the common-coin has produced ≥1 leaders this round   AND
            (ii) every such leader's ABBA has a decision               AND
            (iii) all those decisions are 0
        then we advance to round r+1.
        """
        r = self.current_round
        if not self.coin_ready.get(r):
            return  # coin not finished yet

        leaders = {self.leader_round[r]}  # first coin value
        # In our implementation the leader can change once (updated) so track both
        leaders.update(l for rr, l in self.leader_round.items() if rr == r)

        # abort if any leader not decided yet
        if any(self.abba_state.get(l, {}).get("decided") is None for l in leaders):
            return

        # if any ABBA decided 1 the DRh path will finish MVBA – nothing to do
        if any(self.abba_state[l]["decided"] == 1 for l in leaders):
            return  # round already successful

        # otherwise every decision is 0  ➜  start next round
        self.logger.info(
            f"🔄 Round {r} complete – all ABBA decided 0; moving to round {r + 1}"
        )
        self._start_next_round()

    def _start_next_round(self) -> None:
        """Reset round-local state and bootstrap the next Election/Coin."""
        self.current_round += 1
        r = self.current_round

        # clear / re-initialise round-scoped variables
        self.election_started = False
        self.confirm_already_sent = False
        self.election_senders = set()
        self.confirm_senders = set()
        self.coin_commit[r] = {}
        self.coin_reveal[r] = {}
        self.coin_ready[r] = False

        self.abba_state = {}
        self.abbba_state = {}

        # Reset DRh handler state for new round
        self.drh_handler.reset_round_state()

        # kick off the new round immediately
        self._start_election_round()

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
                    self.logger.info(
                        f"💫 ABBA[{l}] decided 1 - starting DRh for leader {l}"
                    )
                    self.drh_handler.start_instance(l)

                self._maybe_finish_round()

        elif msg.msg_type == MessageType.ABBA_DECIDE:
            pass  # (not used – local decide)

    def _send_heartbeat(self):
        """Send heartbeat to all peers"""
        if not self.zmq_manager:
            return

        # Enhanced heartbeat with protocol state
        heartbeat_data = {
            "timestamp": time.time(),
            "round": self.current_round,
            "mvba_decided": self.mvba_decided,
        }

        # Add ABBA decisions only if any decided 1 (for debugging stuck states)
        if self.abba_state:
            decisions = {l: st.get("decided") for l, st in self.abba_state.items()}
            if any(v == 1 for v in decisions.values()):
                heartbeat_data["abba_decisions"] = decisions

        heartbeat_msg = MVBAMessage(
            msg_type=MessageType.HEARTBEAT,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="heartbeat",
            data=heartbeat_data,
        )

        self.zmq_manager.broadcast_message(heartbeat_msg)

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
        # Process our own SHARE through the new ACID handler/state machine
        self.acid_handler.handle(self_message)
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
                    self.acid_handler.handle(msg)
                elif msg.msg_type in [MessageType.ELECTION, MessageType.CONFIRM]:
                    self._handle_election_message(msg)
                elif msg.msg_type == MessageType.ECHOSHARE:
                    self.drh_handler.handle(msg)

            self.buffered_messages.clear()

    def run(self):
        """Main execution loop"""
        try:
            # Connect and initialize
            if not self.connect_to_bootstrap():
                return

            if not self.setup_p2p_communication():
                return

            # Start heartbeat thread
            self.heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, daemon=True
            )
            self.heartbeat_thread.start()
            self.logger.info("💓 Started heartbeat thread")

            # Start protocol
            if not self.start_communication():
                return

            # Keep running
            self.running = True
            while self.running:
                time.sleep(1)

        except KeyboardInterrupt:
            self.logger.info("Received interrupt signal")
        except Exception as e:
            self.logger.error(f"Runtime error: {e}")
        finally:
            self.stop()

    def _heartbeat_loop(self):
        """Background thread to send periodic heartbeats"""
        while self.running:
            time.sleep(10.0)  # Send heartbeat every 10 seconds (reduced frequency)
            if self.sync_complete:  # Only send heartbeats after protocol starts
                self._send_heartbeat()

    def stop(self):
        """Stop the node"""
        self.running = False
        if self.zmq_manager:
            self.zmq_manager.stop()
        self.logger.info("Node stopped")

    # ------------------------------------------------------------------
    # Router wiring (introduced during refactor)
    # ------------------------------------------------------------------
    def _register_router_handlers(self):
        """Register message-type → handler mapping so the MessageRouter can
        dispatch inbound packets directly to the correct local function.

        This keeps the network layer oblivious of protocol details and removes
        the huge if/elif chain that previously lived in `_handle_message`.
        """

        r = self.router

        # --- infrastructure ------------------------------------------------
        r.register(MessageType.HEARTBEAT, self._handle_heartbeat)
        r.register(MessageType.INPUT_READY, self._handle_input_ready)

        # --- ACIDh ---------------------------------------------------------
        r.register_many(
            [
                MessageType.SHARE,
                MessageType.VOTE,
                MessageType.LOCK,
                MessageType.READY,
                MessageType.FINISH,
            ],
            self.acid_handler.handle,
        )

        # --- Election / common-coin ---------------------------------------
        r.register(MessageType.ELECTION, self._handle_election_message)
        r.register(MessageType.CONFIRM, self._handle_confirm_message)
        r.register_many(
            [MessageType.COIN_COMMIT, MessageType.COIN_REVEAL],
            self._handle_coin_message,
        )

        # --- ABBBA / ABBA ---------------------------------------------------
        r.register_many(
            [
                MessageType.ABBBA_PREVOTE,
                MessageType.ABBBA_VOTE,
                MessageType.ABBBA_DECIDE,
            ],
            self._handle_abbba_message,
        )
        r.register_many(
            [
                MessageType.ABBA_PREVOTE,
                MessageType.ABBA_VOTE,
                MessageType.ABBA_DECIDE,
            ],
            self._handle_abba_message,
        )

        # --- DRh -----------------------------------------------------------
        r.register(MessageType.ECHOSHARE, self.drh_handler.handle)

        # Shutdown, etc. could be added here later.

    # ---------------------------------------------------------------
    # Wrapper that preserves the pre-synchronisation buffering logic.
    # ---------------------------------------------------------------
    def _route_acid_message(self, msg: MVBAMessage):
        """Either buffer the message (if sync not complete) or deliver it."""
        if self.sync_complete:
            self.acid_handler.handle(msg)
        else:
            if not hasattr(self, "buffered_messages"):
                self.buffered_messages = []
            self.buffered_messages.append(msg)

    def _route_drh_message(self, msg: MVBAMessage):
        """Either buffer the DRh message (if sync not complete) or deliver it."""
        if self.sync_complete:
            self.drh_handler.handle(msg)
        else:
            if not hasattr(self, "buffered_messages"):
                self.buffered_messages = []
            self.buffered_messages.append(msg)


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

    print(f"Starting MVBA Node...")

    # Start the main run loop (handles connection, setup, and protocol)
    node.run()

    return 0


if __name__ == "__main__":
    TEST_ONE_NODE_CRASH = False
    TEST_TWO_NODE_CRASH = False
    TEST_FORCE_EXTRA_ROUND = True
    sys.exit(main())
