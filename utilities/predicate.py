def predicate(input_value: str, node_instance) -> bool:
    """
    Check if the input value is one of the participating node IDs.

    Args:
        input_value: The input value provided by the user (as string)
        node_instance: Instance of DistributedNode to get node IDs from

    Returns:
        bool: True if input_value is a valid node ID, False otherwise
    """
    try:
        # Get all participating node IDs from the node instance
        # Include the node's own ID and all peer node IDs
        all_node_ids = [node_instance.node_id]  # Add own ID
        all_node_ids.extend(
            [peer["id"] for peer in node_instance.peer_nodes]
        )  # Add peer IDs

        # Check if the input value is one of the valid node IDs
        return int(input_value) in all_node_ids
    except (ValueError, AttributeError):
        return False
