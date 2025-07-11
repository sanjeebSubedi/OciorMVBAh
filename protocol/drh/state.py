"""Pure DRh (Data Retrieval) state machine.

This module contains a *framework-agnostic* implementation of the DRʰ
sub-protocol used inside Ocior-MVBAh. It maintains all per-instance
book-keeping and, upon processing each inbound message, returns a list of
*actions* (outbound messages) that the caller should deliver to peers.

The goal is to detach the core algorithm from the surrounding networking /
node orchestration so it can be unit-tested in isolation or embedded into
other runtimes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Set, Tuple

from crypto import erasure_decode, erasure_encode, vc_commit, vc_verify
from protocol.common.message import MessageType, MVBAMessage

__all__ = ["Outbound", "DRhState", "DRhResult"]


@dataclass
class Outbound:
    """Descriptor for a message that should be sent by the caller.

    If *target* is *None* the message must be **broadcast** to all peers,
    otherwise it should be delivered only to the specified *target* node.
    """

    msg: MVBAMessage
    target: int | None = None  # None = broadcast


@dataclass
class DRhResult:
    """Result of a successful DRh decode operation."""

    leader_id: int
    commitment: str
    decoded_value: str


class DRhState:
    """State machine implementing the DRʰ (Data Retrieval) sub-protocol.

    The DRh protocol allows nodes to retrieve and validate data shares
    after ABBA decides 1, potentially leading to MVBA termination.
    """

    # ------------------------------------------------------------------
    # Construction
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

        self.log = logger or logging.getLogger(f"DRhState-{node_id}")

        # ---------------- persistent per-instance state ----------------

        # Per-leader DRh state: leader_id → {"Y": {commitment → {node_id → share}}, "done": bool}
        self.dr_state: Dict[int, Dict] = {}

        # Track which leaders have started DRh instances
        self.dr_started: Set[int] = set()

        # Deduplication for ECHOSHARE messages: (leader_id, commitment, sender_id)
        self.echoshare_seen: Set[Tuple[int, str, int]] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle_message(self, msg: MVBAMessage) -> List[Outbound]:
        """Main entry-point called by :class:`protocol.drh.handler.DRhHandler`."""
        if msg.msg_type == MessageType.ECHOSHARE:
            return self._handle_echoshare(msg)

        self.log.warning("Unhandled DRʰ message type: %s", msg.msg_type)
        return []

    def start_instance(
        self,
        leader_id: int,
        acid_locks: Dict[int, int],
        acid_shares: Dict[int, Tuple[str, bytes, Any]],
    ) -> List[Outbound]:
        """Start DRh instance for the given leader.

        Args:
            leader_id: The leader to start DRh for
            acid_locks: ACIDh locks state {leader_id → lock_indicator}
            acid_shares: ACIDh shares state {leader_id → (commitment, shard, proof)}

        Returns:
            List of outbound messages to send (ECHOSHARE if conditions met)
        """
        if leader_id in self.dr_started:
            self.log.debug("DRh instance for leader %s already started", leader_id)
            return []

        self.log.info("🚀 Starting DRh instance for leader %s", leader_id)
        self.dr_started.add(leader_id)
        self.dr_state.setdefault(leader_id, {"Y": {}, "done": False})

        # Check if we can send ECHOSHARE (have lock and share for this leader)
        lock_ind = acid_locks.get(leader_id, 0)
        share_data = acid_shares.get(leader_id, (-1, -1, -1))
        C, y, omega = share_data

        if lock_ind == 1 and C != -1:
            self.log.info("📤 DRh[%s] sending ECHOSHARE", leader_id)
            return [self._send_echoshare_message(leader_id, C, y, omega)]
        else:
            self.log.warning(
                "⚠️ DRh[%s] cannot send ECHOSHARE: lock_ind=%s, has_share=%s",
                leader_id,
                lock_ind,
                C != -1,
            )
            return []

    def attempt_output(
        self,
        leader_id: int,
        commitment: str,
        predicate_fn: Callable[[str, Any], bool],
        predicate_context: Any,
    ) -> Tuple[List[Outbound], DRhResult | None]:
        """Attempt to decode and validate data for the given leader and commitment.

        Args:
            leader_id: The leader whose data to decode
            commitment: The commitment to decode
            predicate_fn: Function to validate decoded value
            predicate_context: Context object passed to predicate function

        Returns:
            Tuple of (outbound_messages, result_or_none)
        """
        st = self.dr_state.get(leader_id)
        if not st or st["done"]:
            return [], None

        shares = st["Y"].get(commitment, {})
        k = self.byzantine_threshold + 1

        if len(shares) < k:
            self.log.debug(
                "DRh[%s] insufficient shares: %s/%s for %s",
                leader_id,
                len(shares),
                k,
                commitment[:16],
            )
            return [], None

        self.log.info(
            "🔍 DRh[%s] attempting decode with %s shares", leader_id, len(shares)
        )

        try:
            # Decode the erasure-coded data using the leader's proposer ID
            w_bytes = erasure_decode(
                self.total_nodes, self.byzantine_threshold, shares, leader_id
            )
        except Exception as e:
            self.log.warning("❌ DRh[%s] decode error: %s", leader_id, e)
            return [], None

        # Verify commitment consistency
        expected_shards = erasure_encode(
            self.total_nodes, self.byzantine_threshold, w_bytes, leader_id
        )
        if vc_commit(expected_shards) != commitment:
            self.log.warning("❌ DRh[%s] commitment mismatch", leader_id)
            return [], None

        # Validate with predicate
        w_hat = w_bytes.decode("utf-8", errors="ignore")
        if predicate_fn(w_hat, predicate_context):
            st["done"] = True
            self.log.info("🎉 DRh[%s] DECODE SUCCESS for value: %s", leader_id, w_hat)
            return [], DRhResult(leader_id, commitment, w_hat)
        else:
            self.log.warning(
                "❌ DRh[%s] predicate failed for value: %s", leader_id, w_hat
            )
            return [], None

    def reset_round_state(self) -> None:
        """Reset all DRh state for a new round."""
        self.dr_state.clear()
        self.dr_started.clear()
        # Keep echoshare_seen across rounds for global deduplication

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_echoshare_message(
        self, leader_id: int, C: str, y: bytes, omega: Any
    ) -> Outbound:
        """Create ECHOSHARE message for broadcast."""
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
                "index": self.node_id,
            },
        )
        # Handle local delivery through the returned Outbound
        return Outbound(msg, None)  # None = broadcast

    def _handle_echoshare(self, msg: MVBAMessage) -> List[Outbound]:
        """Process incoming ECHOSHARE message."""
        l = msg.data["leader"]
        C = msg.data["commitment"]
        y_hex = msg.data["shard"]
        omega = msg.data["proof"]
        idx = msg.data.get("index", msg.sender_id)

        # Convert hex back to bytes
        try:
            y = bytes.fromhex(y_hex)
        except ValueError:
            self.log.warning("❌ DRh ECHOSHARE invalid hex from node %s", msg.sender_id)
            return []

        # Deduplicate
        key = (l, C, idx)
        if key in self.echoshare_seen:
            self.log.debug("Duplicate ECHOSHARE ignored: %s", key)
            return []
        self.echoshare_seen.add(key)

        # Verify the vector commitment proof
        if not vc_verify(idx, C, y, omega):
            self.log.warning(
                "❌ DRh ECHOSHARE verification failed for leader %s, index %s", l, idx
            )
            return []

        # Store the share
        leader_st = self.dr_state.setdefault(l, {"Y": {}, "done": False})
        Ys = leader_st["Y"].setdefault(C, {})
        Ys[idx] = y

        self.log.info(
            "✅ DRh[%s] received share from index %s (%s/%s needed)",
            l,
            idx,
            len(Ys),
            self.byzantine_threshold + 1,
        )

        # No automatic attempt_output here - the handler will coordinate that
        return []
