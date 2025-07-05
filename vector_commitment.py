import hashlib
from typing import List, Optional, Tuple


def hash_shard(shard: bytes) -> bytes:
    return hashlib.sha256(shard).digest()


def build_merkle_tree(hashes):
    tree = [hashes]
    while len(tree[-1]) > 1:
        level = []
        current = tree[-1]
        for i in range(0, len(current), 2):
            left = current[i]
            right = (
                current[i + 1] if i + 1 < len(current) else current[i]
            )  # duplicate last if odd
            combined = hashlib.sha256(left + right).digest()
            level.append(combined)
        tree.append(level)
    return tree


def vector_commitment(shards):
    leaf_hashes = [hash_shard(s) for s in shards]
    tree = build_merkle_tree(leaf_hashes)
    root = tree[-1][0]
    return root, tree


def generate_proof(tree, index: int) -> List[bytes]:
    """Generate Merkle proof for shard at given index"""
    proof = []

    for level in range(len(tree) - 1):  # Skip root level
        level_size = len(tree[level])

        if index % 2 == 0:
            # Left child - need right sibling
            sibling_index = index + 1
        else:
            # Right child - need left sibling
            sibling_index = index - 1

        if sibling_index < level_size:
            proof.append(tree[level][sibling_index])
        else:
            # No sibling (odd number of nodes)
            proof.append(tree[level][index])

        index = index // 2

    return proof


def verify_proof(root: bytes, shard: bytes, index: int, proof: List[bytes]) -> bool:
    """Verify that shard belongs to the tree with given root"""
    current_hash = hash_shard(shard)

    for sibling_hash in proof:
        if index % 2 == 0:
            # We're left child
            current_hash = hashlib.sha256(current_hash + sibling_hash).digest()
        else:
            # We're right child
            current_hash = hashlib.sha256(sibling_hash + current_hash).digest()

        index = index // 2

    return current_hash == root


# MVBA Protocol Interface
def vc_commit(shards: List[bytes]) -> str:
    """VcCom() - Create vector commitment"""
    root, _ = vector_commitment(shards)
    return root.hex()


def vc_open(shards: List[bytes], index: int) -> Tuple[bytes, List[str]]:
    """VcOpen() - Create opening proof"""
    root, tree = vector_commitment(shards)
    proof = generate_proof(tree, index)
    return shards[index], [p.hex() for p in proof]


def vc_verify(index: int, commitment: str, shard: bytes, proof: List[str]) -> bool:
    """VcVerify() - Verify opening proof"""
    root = bytes.fromhex(commitment)
    proof_bytes = [bytes.fromhex(p) for p in proof]
    return verify_proof(root, shard, index, proof_bytes)


# Demo
if __name__ == "__main__":
    # Test with erasure coding
    from erasure_coding import erasure_encode

    shards = erasure_encode(4, 1, "value_from_1")

    # Create commitment
    commitment = vc_commit(shards)
    print(f"Commitment: {commitment}")

    # Test proof for each shard
    for i in range(len(shards)):
        shard, proof = vc_open(shards, i)
        is_valid = vc_verify(i, commitment, shard, proof)
        print(f"Shard {i}: {is_valid}")
