def predicate(input_value: str, node) -> bool:
    # Extract node_ids from peers list
    peer_node_ids = [peer["node_id"] for peer in node.peers]
    peer_node_ids.append(node.node_id)
    # Check if input_value starts with "input_from_"
    if not input_value.startswith("input_from_"):
        return False

    suffix = input_value[len("input_from_") :]

    return suffix in [str(node_id) for node_id in peer_node_ids]


if __name__ == "__main__":
    print(predicate("input_fromx_1", None))
