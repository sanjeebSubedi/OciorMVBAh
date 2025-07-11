"""DRh (Data Retrieval) Protocol Module.

This module provides a modular implementation of the DRʰ sub-protocol
used in Ocior-MVBAh. The DRh protocol allows nodes to retrieve and validate
data shares after ABBA decides 1, potentially leading to MVBA termination.

Main Components:
- DRhHandler: Message handler that coordinates with the Node class
- DRhState: Pure state machine implementing the core DRh algorithm
- DRhResult: Data structure for successful decode results

Usage:
    from protocol.drh import DRhHandler

    # In Node.__init__:
    self.drh_handler = DRhHandler(self)

    # When ABBA decides 1:
    self.drh_handler.start_instance(leader_id)
"""

from .handler import DRhHandler
from .state import DRhResult, DRhState, Outbound

__all__ = [
    "DRhHandler",
    "DRhState",
    "DRhResult",
    "Outbound",
]
