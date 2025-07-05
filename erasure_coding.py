import reedsolo


def erasure_encode(n: int, t: int, input_value) -> list:
    """MVBA Reed-Solomon encoding: n shares, any t+1 can reconstruct"""
    k = t + 1  # Minimum shares needed for reconstruction

    # Convert to bytes
    data = input_value.encode("utf-8") if isinstance(input_value, str) else input_value

    # Encode with Reed-Solomon
    rs = reedsolo.RSCodec(n - k)
    encoded = rs.encode(data)

    # Split into n shares
    share_size = len(encoded) // n
    shares = [encoded[i * share_size : (i + 1) * share_size] for i in range(n)]
    return shares


def erasure_decode(n: int, t: int, shares: dict):
    """MVBA Reed-Solomon decoding from available shares"""
    k = t + 1

    # Reconstruct encoded data
    share_size = len(next(iter(shares.values())))
    encoded = bytearray(share_size * n)

    for idx, share in shares.items():
        encoded[idx * share_size : (idx + 1) * share_size] = share

    # Decode
    rs = reedsolo.RSCodec(n - k)
    return rs.decode(bytes(encoded))


if __name__ == "__main__":
    print(erasure_encode(4, 1, "input_fromx_1"))
