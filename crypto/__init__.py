"""Cryptographic utilities for the Ocior-MVBAh protocol.

This module provides cryptographic primitives used by the MVBAh protocol:
- Reed-Solomon erasure coding for data redundancy
- Merkle tree-based vector commitments for integrity proofs

The implementation follows the paper's specification for OciorMVBAh's
cryptographic requirements.
"""

from .erasure_coding import erasure_decode, erasure_encode
from .vector_commitment import vc_commit, vc_open, vc_verify

__all__ = [
    # Erasure coding functions
    "erasure_encode",
    "erasure_decode",
    # Vector commitment functions
    "vc_commit",
    "vc_open",
    "vc_verify",
]

# Version info
__version__ = "1.0.0"
__author__ = "Ocior-MVBAh Implementation Team"
