# rebuilt RS helper — any t+1 of n shares reconstruct
from typing import Dict, List

import reedsolo

# ---------- Galois-field helpers (exposed by reedsolo) ----------
gf_mul = reedsolo.gf_mul
gf_inv = reedsolo.gf_inverse


def _vandermonde(n: int, k: int, node_id: int = 0) -> List[List[int]]:
    """n × k Vandermonde matrix with node-specific offset using proper GF(256) arithmetic"""
    offset = node_id + 1  # ensure uniqueness
    result = []
    for i in range(1, n + 1):
        row = []
        base = (i + offset) % 256  # Keep in GF(256)
        if base == 0:  # 0 is not a generator, use 1 instead
            base = 1

        current = 1  # base^0 = 1
        for j in range(k):
            row.append(current)
            if j < k - 1:  # Don't compute one extra
                current = gf_mul(current, base)  # Proper GF(256) multiplication
        result.append(row)
    return result


def _matrix_invert_gf256(matrix: List[List[int]]) -> List[List[int]]:
    """
    Invert a square matrix over GF(256) using Gaussian elimination.
    """
    n = len(matrix)
    # Create augmented matrix [A | I]
    aug = []
    for i in range(n):
        row = matrix[i][:] + [0] * n  # copy row and append zeros
        row[n + i] = 1  # identity matrix
        aug.append(row)

    # Forward elimination
    for i in range(n):
        # Find pivot (non-zero element)
        pivot_row = i
        for j in range(i + 1, n):
            if aug[j][i] != 0:
                pivot_row = j
                break

        if aug[pivot_row][i] == 0:
            raise ValueError("Matrix is not invertible")

        # Swap rows if needed
        if pivot_row != i:
            aug[i], aug[pivot_row] = aug[pivot_row], aug[i]

        # Scale row to make pivot = 1
        pivot = aug[i][i]
        pivot_inv = gf_inv(pivot)
        for j in range(2 * n):
            aug[i][j] = gf_mul(aug[i][j], pivot_inv)

        # Eliminate column
        for j in range(n):
            if i != j and aug[j][i] != 0:
                factor = aug[j][i]
                for k in range(2 * n):
                    aug[j][k] ^= gf_mul(factor, aug[i][k])

    # Extract inverse matrix from right half
    inverse = []
    for i in range(n):
        inverse.append(aug[i][n:])

    return inverse


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

    idxs = sorted(shares)[:k]  # pick first k indices
    V = _vandermonde(n, k, proposer_node_id)  # use proposer's node_id
    M = [[V[i][j] for j in range(k)] for i in idxs]  # k×k sub-matrix
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
