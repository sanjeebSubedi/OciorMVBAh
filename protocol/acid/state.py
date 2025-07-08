"""Pure ACIDh state machine.

This module contains a *framework-agnostic* implementation of the ACIDʰ
sub-protocol used inside Ocior-MVBAh.  It maintains all per-instance
book-keeping and, upon processing each inbound message, returns a list of
*actions* (outbound messages) that the caller should deliver to peers.

The goal is to detach the core algorithm from the surrounding networking /
node orchestration so it can be unit-tested in isolation or embedded into
other runtimes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Set, Tuple

from protocol.common.message import MessageType, MVBAMessage
from vector_commitment import vc_verify

__all__ = ["Outbound", "ACIDState"]


@dataclass
class Outbound:
    """Descriptor for a message that should be sent by the caller.

    If *target* is *None* the message must be **broadcast** to all peers,
    otherwise it should be delivered only to the specified *target* node.
    """

    msg: MVBAMessage
    target: int | None = None  # None = broadcast


class ACIDState:
    """State machine replicating Algorithm 9 (ACIDʰ) from the MVBAh paper.

    The implementation closely follows the logic previously embedded in
    ``node.py`` but no longer has any dependency on the surrounding *Node*
    class: everything it needs is passed via the constructor or contained in
    the inbound :class:`~protocol.common.message.MVBAMessage` objects.
    """

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def __init__(
        self,
        node_id: int,
        total_nodes: int,
        byzantine_threshold: int,
        session_id: int,
        logger: logging.Logger | None = None,
    ):
        self.node_id = node_id
        self.total_nodes = total_nodes
        self.byzantine_threshold = byzantine_threshold
        self.session_id = session_id

        self.log = logger or logging.getLogger(f"ACIDState-{node_id}")

        # ---------------- persistent per-instance state ----------------

        # Sets and maps initialised exactly like in the legacy Node
        self.acidh_locks: Dict[int, int] = {}
        self.acidh_ready: Dict[int, int] = {}
        self.acidh_finish: Dict[int, int] = {}
        self.acidh_shares: Dict[int, Tuple[str, bytes, Any]] = {}
        self.acidh_hash: Dict[str, int] = {}
        self.received_shares: Set[Tuple[int, int, str]] = set()

        # Lazy-initialised on first use in the legacy code – we allocate
        # them eagerly here for simplicity.
        self.vote_counts: Dict[str, Set[int]] = {}
        self.lock_counts: Dict[str, Set[int]] = {}
        self.ready_counts: Dict[str, Set[int]] = {}
        self.finish_counts: Dict[str, Set[int]] = {}

        self.ready_sent: Set[str] = set()
        self.finish_sent: Set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle_message(self, msg: MVBAMessage) -> List[Outbound]:
        """Main entry-point called by :class:`protocol.acid.handler.ACIDHandler`."""
        if msg.msg_type == MessageType.SHARE:
            return self._handle_share(msg)
        if msg.msg_type == MessageType.VOTE:
            return self._handle_vote(msg)
        if msg.msg_type == MessageType.LOCK:
            return self._handle_lock(msg)
        if msg.msg_type == MessageType.READY:
            return self._handle_ready(msg)
        if msg.msg_type == MessageType.FINISH:
            return self._handle_finish(msg)

        self.log.warning("Unhandled ACIDʰ message type: %s", msg.msg_type)
        return []

    # ------------------------------------------------------------------
    # --- Internal helpers -------------------------------------------------
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_bytes_from_string(shard_str: str) -> bytes:
        """Convert the JSON-serialised shard back to `bytes`.

        The node has seen three different formats during evolution – we keep
        compatibility by supporting all of them here.
        """
        # 1. current format – plain hex
        try:
            return bytes.fromhex(shard_str)
        except ValueError:
            pass

        # 2. legacy formats
        if shard_str.startswith("bytearray(b'") and shard_str.endswith("')"):
            return shard_str[12:-2].encode("latin-1")
        if shard_str.startswith("b'") and shard_str.endswith("'"):
            return shard_str[2:-1].encode("latin-1")

        # 3. last resort – treat it as UTF-8 literal
        return shard_str.encode("utf-8")

    # ------------------------------------------------------------------
    # Message-specific handlers – each returns a list of outbound messages
    # ------------------------------------------------------------------

    def _send_vote(self, commitment: str) -> Outbound:
        vote = MVBAMessage(
            msg_type=MessageType.VOTE,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="vote",
            data={"commitment": commitment},
        )
        self.log.info("📤 VOTE for commitment %s…", commitment[:16])
        return Outbound(vote, None)  # broadcast

    def _send_lock(self, commitment: str) -> Outbound:
        lock = MVBAMessage(
            msg_type=MessageType.LOCK,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="lock",
            data={"commitment": commitment},
        )
        self.log.info("📤 LOCK for commitment %s…", commitment[:16])
        return Outbound(lock, None)

    def _send_ready(self, commitment: str) -> Outbound:
        ready = MVBAMessage(
            msg_type=MessageType.READY,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="ready",
            data={"commitment": commitment},
        )
        self.log.info("📤 READY for commitment %s…", commitment[:16])
        return Outbound(ready, None)

    def _send_finish(self, commitment: str) -> Outbound:
        finish = MVBAMessage(
            msg_type=MessageType.FINISH,
            sender_id=self.node_id,
            session_id=self.session_id,
            protocol_id="finish",
            data={"commitment": commitment},
        )
        # We follow the *fixed* behaviour of the legacy code and broadcast
        # FINISH to **all** peers (instead of only the proposer).
        self.log.info("📤 FINISH for commitment %s…", commitment[:16])
        # Update local state immediately – ensures symmetric view.
        self._handle_finish(finish)
        return Outbound(finish, None)

    # -------------------------- SHARE -----------------------------------

    def _handle_share(self, msg: MVBAMessage) -> List[Outbound]:
        j = msg.sender_id
        share_key = (j, msg.session_id, msg.protocol_id)

        if share_key in self.received_shares:
            self.log.debug("Duplicate SHARE from node %s ignored", j)
            return []
        self.received_shares.add(share_key)

        C = msg.data["commitment"]
        y_str = msg.data["shard"]
        omega = msg.data["proof"]
        y = self._extract_bytes_from_string(y_str)

        self.log.info("📦 SHARE from node %s", j)

        actions: List[Outbound] = []

        if vc_verify(self.node_id, C, y, omega):
            # Lines 12–13: store references
            self.acidh_hash.setdefault(C, j)
            self.acidh_hash[C] = min(self.acidh_hash[C], j)
            self.acidh_shares[j] = (C, y, omega)

            # Line 14 – send VOTE
            actions.append(self._send_vote(C))

            # Check any votes we already gathered for this commitment
            actions.extend(self._check_pending_votes())
        else:
            self.log.warning("❌ SHARE verification failed for node %s", j)

        return actions

    # -------------------------- VOTE ------------------------------------

    def _handle_vote(self, msg: MVBAMessage) -> List[Outbound]:
        sender = msg.sender_id
        C = msg.data["commitment"]

        voters = self.vote_counts.setdefault(C, set())
        voters.add(sender)

        vote_cnt = len(voters)
        threshold = self.total_nodes - self.byzantine_threshold
        self.log.debug("📊 VOTE %s/%s for %s", vote_cnt, threshold, C[:16])

        actions: List[Outbound] = []

        if vote_cnt >= threshold:
            if C in self.acidh_hash:
                j_star = self.acidh_hash[C]
                if self.acidh_locks.get(j_star, 0) != 1:
                    self.acidh_locks[j_star] = 1
                    self.log.info("🔒 Locked proposal from node %s", j_star)
                    actions.append(self._send_lock(C))
                # Clear to avoid re-processing
                self.vote_counts[C] = set()
            else:
                self.log.debug("Votes reached threshold but SHARE unknown – waiting")
        return actions

    # -------------------------- LOCK ------------------------------------

    def _handle_lock(self, msg: MVBAMessage) -> List[Outbound]:
        sender = msg.sender_id
        C = msg.data["commitment"]

        locks = self.lock_counts.setdefault(C, set())
        locks.add(sender)

        lock_cnt = len(locks)
        threshold = self.total_nodes - self.byzantine_threshold

        actions: List[Outbound] = []

        if lock_cnt >= threshold and C in self.acidh_hash:
            j_star = self.acidh_hash[C]
            self.acidh_ready[j_star] = 1
            if C not in self.ready_sent:
                self.ready_sent.add(C)
                actions.append(self._send_ready(C))
        return actions

    # -------------------------- READY -----------------------------------

    def _handle_ready(self, msg: MVBAMessage) -> List[Outbound]:
        sender = msg.sender_id
        C = msg.data["commitment"]

        rds = self.ready_counts.setdefault(C, set())
        rds.add(sender)

        ready_cnt = len(rds)
        threshold = self.total_nodes - self.byzantine_threshold

        actions: List[Outbound] = []

        if ready_cnt >= threshold and C in self.acidh_hash:
            j_star = self.acidh_hash[C]
            if self.acidh_finish.get(j_star, 0) == 0:
                self.acidh_finish[j_star] = 1
                if C not in self.finish_sent:
                    self.finish_sent.add(C)
                    actions.append(self._send_finish(C))
            # Clear to avoid re-triggering
            self.ready_counts[C] = set()
        return actions

    # -------------------------- FINISH ----------------------------------

    def _handle_finish(self, msg: MVBAMessage) -> List[Outbound]:
        sender = msg.sender_id
        C = msg.data["commitment"]

        fset = self.finish_counts.setdefault(C, set())
        fset.add(sender)

        finish_cnt = len(fset)
        threshold = self.total_nodes - self.byzantine_threshold

        # Election phase is handled by the outer Node – we just broadcast FINISH.
        if finish_cnt >= threshold:
            # Clear counts so we don’t trigger repeatedly
            self.finish_counts[C] = set()
        return []

    # ------------------------- helpers ----------------------------------

    def _check_pending_votes(self) -> List[Outbound]:
        actions: List[Outbound] = []
        threshold = self.total_nodes - self.byzantine_threshold
        for C, voters in list(self.vote_counts.items()):
            if len(voters) >= threshold and C in self.acidh_hash:
                j_star = self.acidh_hash[C]
                if self.acidh_locks.get(j_star, 0) != 1:
                    self.acidh_locks[j_star] = 1
                    self.log.info("🔒 Locked proposal from node %s (delayed)", j_star)
                    actions.append(self._send_lock(C))
                    self.vote_counts[C] = set()
        return actions
