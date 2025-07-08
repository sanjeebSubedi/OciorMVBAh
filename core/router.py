"""Simple event-driven message router.

Each node creates one instance and registers local handler functions for every
:class:`protocol.common.message.MessageType` it wants to handle.  The network
layer feeds inbound messages directly into :py:meth:`dispatch`.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict

from protocol.common.message import MessageType, MVBAMessage


class MessageRouter:
    """Maps *message type* → *callable* and invokes it.

    The router deliberately knows *nothing* about node internals – the concrete
    handler functions are supplied by :pyclass:`core.node.Node` (or tests).
    """

    def __init__(self, logger: logging.Logger | None = None):
        self._handlers: Dict[MessageType, Callable[[MVBAMessage], None]] = {}
        self._log = logger or logging.getLogger("MessageRouter")

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------

    def register(
        self, msg_type: MessageType, handler: Callable[[MVBAMessage], None]
    ) -> None:
        self._handlers[msg_type] = handler

    def register_many(
        self, types: list[MessageType], handler: Callable[[MVBAMessage], None]
    ) -> None:
        for t in types:
            self.register(t, handler)

    # ------------------------------------------------------------------
    # Dispatch entry-point
    # ------------------------------------------------------------------

    def dispatch(self, msg: MVBAMessage) -> None:  # pragma: no cover – thin wrapper
        h = self._handlers.get(msg.msg_type)
        if h is None:
            self._log.warning("No handler registered for %s", msg.msg_type)
            return
        h(msg)
