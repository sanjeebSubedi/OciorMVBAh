async def predicate(w: str) -> bool:
    return w.startswith("value_from_")


def main():
    print(predicate("value_from_node_1"))
    print(predicate("node_2"))


if __name__ == "__main__":
    main()
