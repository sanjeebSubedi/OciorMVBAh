# rebuilt RS helper — any t+1 of n shares reconstruct
import logging
import pprint
from typing import Dict, List

import reedsolo

# ---------- Galois-field helpers (exposed by reedsolo) ----------
reedsolo.init_tables(0x11D)
gf_mul = reedsolo.gf_mul
gf_inv = reedsolo.gf_inverse
# show the sub-matrix used for decoding even on the default log level


def _vandermonde(n: int, k: int, node_id: int = 0) -> List[List[int]]:
    """
    n × k Vandermonde matrix over GF(256) with a node-specific offset.
    Row i (0-based) is evaluated at α = (i + 1 + node_id) mod 256   (α ≠ 0).
    """
    offset = node_id + 1
    rows = []
    for i in range(1, n + 1):
        α = (i + offset) & 0xFF  # keep inside 0–255
        if α == 0:  # 0 is not allowed – bump to 1
            α = 1
        row = [1]
        for _ in range(1, k):
            row.append(gf_mul(row[-1], α))  # next power via GF mult
        rows.append(row)
    return rows


def _matrix_invert_gf256(matrix: List[List[int]]) -> List[List[int]]:
    """
    Invert a square matrix over GF(256) using Gaussian elimination.

    Fixed:
    • Only search for another pivot row when the current diagonal element is
      actually zero.
    • Added clearer comments.
    """
    n = len(matrix)

    # ------------------------------------------------------------------
    # build the augmented matrix  [ A | I ]
    # ------------------------------------------------------------------
    aug = []
    for i in range(n):
        row = matrix[i][:] + [0] * n
        row[n + i] = 1
        aug.append(row)

    # ------------------------------------------------------------------
    # forward elimination
    # ------------------------------------------------------------------
    for i in range(n):
        # make sure aug[i][i] is non-zero; swap with a lower row if needed
        if aug[i][i] == 0:
            for j in range(i + 1, n):
                if aug[j][i] != 0:
                    aug[i], aug[j] = aug[j], aug[i]
                    break
            else:  # ⇐ no non-zero pivot in this column
                raise ValueError("Matrix is not invertible")

        # scale the pivot row so that the pivot becomes 1
        pivot = aug[i][i]
        pivot_inv = gf_inv(pivot)
        for j in range(2 * n):
            aug[i][j] = gf_mul(aug[i][j], pivot_inv)

        # eliminate this column from the other rows
        for j in range(n):
            if j != i and aug[j][i] != 0:
                factor = aug[j][i]
                for k in range(2 * n):
                    aug[j][k] ^= gf_mul(factor, aug[i][k])

    # ------------------------------------------------------------------
    # extract the inverse  (right-hand side of the augmented matrix)
    # ------------------------------------------------------------------
    return [row[n:] for row in aug]


# =================================================================
#                           public API
# =================================================================
def erasure_encode(n: int, t: int, value, node_id: int = 0) -> List[bytes]:
    """
    Return *n* Reed-Solomon shares; **any** (t+1) are enough to decode.
    Each node uses a different Vandermonde matrix to ensure unique commitments.
    """
    k = t + 1
    data = value.encode() if isinstance(value, str) else bytes(value)

    # pad so len(data) is a multiple of k
    if len(data) % k:
        data += b"\x00" * (k - len(data) % k)

    blocks = [data[i : i + k] for i in range(0, len(data), k)]
    V = _vandermonde(n, k, node_id)  # node-specific matrix

    shares = [bytearray() for _ in range(n)]
    for blk in blocks:
        for row, out in zip(V, shares):
            acc = 0
            for coef, byte in zip(row, blk):
                acc ^= gf_mul(coef, byte)
            out.append(acc)

    return [bytes(s) for s in shares]


def erasure_decode(
    n: int, t: int, shares: Dict[int, bytes], proposer_node_id: int = 0
) -> bytes:
    """
    Reconstruct original bytes from ≥ t+1 *indexed* shares.
    `shares` is a dict {index (0-based) → share-bytes}.
    Must use the same node_id that was used for encoding (the proposer's ID).
    """
    k = t + 1
    if len(shares) < k:
        raise ValueError("need at least t+1 shares to decode")

    share_len = len(next(iter(shares.values())))
    if any(len(s) != share_len for s in shares.values()):
        raise ValueError("all shares must be equal length")

    idxs = sorted(shares)[:k]
    V = _vandermonde(n, k, proposer_node_id)
    M = [[V[i][j] for j in range(k)] for i in idxs]

    # --- new: always visible ---
    import logging
    import pprint

    logging.getLogger("MVBANode").warning(
        "DRh-decode  proposer=%d  idxs=%s  sub-matrix=\n%s",
        proposer_node_id,
        idxs,
        pprint.pformat(M),
    )

    Minv = _matrix_invert_gf256(M)

    out = bytearray()
    for pos in range(share_len):
        col = [shares[i][pos] for i in idxs]
        for row in Minv:
            acc = 0
            for a, b in zip(row, col):
                acc ^= gf_mul(a, b)
            out.append(acc)

    return bytes(out).rstrip(b"\x00")  # strip padding
