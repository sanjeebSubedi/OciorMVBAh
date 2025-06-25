import hashlib


def hash_value(w: str) -> str:
    """
    Compute the SHA-256 hash of a string and return it as a hex string.

    Args:
        w (str): Input message to be hashed.

    Returns:
        str: SHA-256 digest of the message in hexadecimal format.
    """
    return hashlib.sha256(w.encode("utf-8")).hexdigest()


def main():
    msg = "hello"
    hash = hash_value(msg)
    print(f"Hash of {msg}: {hash}")


if __name__ == "__main__":
    main()
