import random


def common_coin(round_id: int, context_id: str, choices: list[int]) -> int:
    seed = f"{context_id}-{round_id}"
    random.seed(seed)
    return random.choice(choices)


def main():
    print(common_coin(1, "test"))


if __name__ == "__main__":
    main()
