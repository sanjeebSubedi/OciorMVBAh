import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict


class MessageType(str, Enum):
    """Enumeration of all message types exchanged by the Ocior-MVBAh protocol."""

    # ---- ACIDh ----
    SHARE = "SHARE"
    VOTE = "VOTE"
    LOCK = "LOCK"
    READY = "READY"
    FINISH = "FINISH"
    ELECTION = "ELECTION"
    CONFIRM = "CONFIRM"

    # ---- Common-coin ----
    COIN_COMMIT = "COIN_COMMIT"
    COIN_REVEAL = "COIN_REVEAL"

    # ---- DRh ----
    ECHOSHARE = "ECHOSHARE"

    # ---- ABBBA / ABBA ----
    ABBBA_PREVOTE = "ABBBA_PREVOTE"
    ABBBA_VOTE = "ABBBA_VOTE"
    ABBBA_DECIDE = "ABBBA_DECIDE"
    ABBA_PREVOTE = "ABBA_PREVOTE"
    ABBA_VOTE = "ABBA_VOTE"
    ABBA_DECIDE = "ABBA_DECIDE"

    # ---- Infrastructure ----
    HEARTBEAT = "HEARTBEAT"
    INPUT_READY = "INPUT_READY"
    SHUTDOWN = "SHUTDOWN"

    def __str__(self) -> str:
        return self.value


@dataclass
class MVBAMessage:
    """Canonical serialisable message wrapper used across all sub-protocols."""

    msg_type: MessageType
    sender_id: int
    session_id: int
    protocol_id: str  # free-form tag so multiple instances can coexist
    data: Dict[str, Any]
    timestamp: float | None = None

    # --- serialisation helpers ---------------------------------------

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = time.time()

    # The default json.dumps cannot handle Enum values → convert explicitly
    def to_json(self) -> str:
        d = {
            "msg_type": self.msg_type.value,
            "sender_id": self.sender_id,
            "session_id": self.session_id,
            "protocol_id": self.protocol_id,
            "data": self.data,
            "timestamp": self.timestamp,
        }
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "MVBAMessage":
        d = json.loads(raw)
        d["msg_type"] = MessageType(d["msg_type"])
        return cls(**d)  # type: ignore[arg-type]
