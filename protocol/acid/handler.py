"""ACIDh message handler wrapper.

At this point of the refactor all ACIDh logic still lives in `core.node`.
This class merely forwards incoming messages to those original methods so we
can decouple the router and progressively move the real state machine here in
future commits.
"""

from __future__ import annotations

from typing import Callable, Dict, List

from protocol.common.message import MessageType, MVBAMessage


class ACIDHandler:
    """Thin proxy that delegates to the legacy methods on *node*."""

    _ACID_TYPES: List[MessageType] = [
        MessageType.SHARE,
        MessageType.VOTE,
        MessageType.LOCK,
        MessageType.READY,
        MessageType.FINISH,
    ]

    def __init__(self, node):
        self.node = node
        # Build a mini dispatch table pointing to the node's original methods
        self._dispatch: Dict[MessageType, Callable[[MVBAMessage], None]] = {
            MessageType.SHARE: node._handle_share_message,  # type: ignore[attr-defined]
            MessageType.VOTE: node._handle_vote_message,
            MessageType.LOCK: node._handle_lock_message,
            MessageType.READY: node._handle_ready_message,
            MessageType.FINISH: node._handle_finish_message,
        }

    # ------------------------------------------------------------------
    # Public API used by MessageRouter
    # ------------------------------------------------------------------

    def handle(self, msg: MVBAMessage) -> None:  # pragma: no cover
        if not self.node.sync_complete:
            # preserve old buffering behaviour via Node helper
            self.node._route_acid_message(msg)  # type: ignore[attr-defined]
            return

        # Normalise Enum class (node still defines its own MessageType)
        key = msg.msg_type
        if key not in self._dispatch:  # different enum class – map by value
            key = MessageType(key.value)
        self._dispatch[key](msg)
