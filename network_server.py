import asyncio
import json
from collections import defaultdict
from typing import Dict, List, Set


class NetworkServer:
    """
    Bootstrap server for distributed Byzantine Agreement protocol simulations.

    This server has a single responsibility: initialize the network and provide
    node discovery information. Once all nodes are connected and have received
    their peer information, this server can shut down - the nodes will communicate
    directly with each other from that point forward.

    Lifecycle:
    1. Wait for all expected nodes to connect
    2. Assign unique node IDs and listening ports to each node
    3. Send complete network topology to all nodes
    4. Signal "START" to begin the protocol
    5. Shutdown (nodes continue independently)
    """

    def __init__(
        self, host="127.0.0.1", port=8888, expected_nodes=3, node_port_start=9000
    ):
        """
        Initialize the bootstrap server.

        Args:
            host: IP address for the bootstrap server to listen on
            port: Port for the bootstrap server to listen on
            expected_nodes: Total number of nodes required before starting protocol
            node_port_start: Starting port number for node-to-node communication
                           Node 0 will listen on node_port_start,
                           Node 1 will listen on node_port_start + 1, etc.
        """
        # Bootstrap server configuration
        self.host = host
        self.port = port
        self.expected_nodes = expected_nodes

        # Port allocation for node-to-node communication
        # Each node will get its own unique port for listening to other nodes
        self.node_port_start = node_port_start

        # Track connected clients during bootstrap phase
        # Maps node_id -> (StreamWriter, assigned_port, connection_info)
        self.connected_nodes: Dict[int, Dict] = {}

        # Pool of available node IDs that can be assigned
        # Starts with {0, 1, 2, ..., expected_nodes-1}
        self.available_ids: Set[int] = set(range(expected_nodes))

        # Event that signals when all expected nodes have connected
        # and the network is ready to begin the protocol
        self.all_nodes_connected = asyncio.Event()

        # Flag to track if we've already sent the network topology
        # Prevents duplicate sends if multiple nodes connect simultaneously
        self.topology_sent = False

    async def start(self):
        """
        Start the bootstrap server and handle the complete bootstrap lifecycle.

        This method:
        1. Starts listening for incoming node connections
        2. Handles the bootstrap process for all nodes
        3. Sends network topology when all nodes are connected
        4. Gracefully shuts down after bootstrap is complete
        """
        print(f"[Bootstrap Server] Starting on {self.host}:{self.port}")
        print(f"[Bootstrap Server] Expecting {self.expected_nodes} nodes to connect")
        print(
            f"[Bootstrap Server] Node ports will be assigned starting from {self.node_port_start}"
        )
        print()

        # Create and start the server
        server = await asyncio.start_server(
            self.handle_node_connection, self.host, self.port
        )

        print(f"[Bootstrap Server] Listening for node connections...")

        try:
            async with server:
                # Wait for all nodes to connect and complete bootstrap
                await self.all_nodes_connected.wait()

                # Give a moment for final messages to be sent
                await asyncio.sleep(1.0)

                print()
                print(f"[Bootstrap Server] All nodes bootstrapped successfully!")
                print(
                    f"[Bootstrap Server] Nodes are now communicating directly with each other"
                )
                print(f"[Bootstrap Server] Bootstrap server shutting down...")

        except KeyboardInterrupt:
            print(f"\n[Bootstrap Server] Interrupted by user")
        finally:
            # Ensure all client connections are closed properly
            for node_info in self.connected_nodes.values():
                writer = node_info["writer"]
                if not writer.is_closing():
                    writer.close()
                    await writer.wait_closed()

    async def handle_node_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """
        Handle a single node connection throughout the bootstrap process.

        For each connecting node:
        1. Assign a unique node ID and listening port
        2. Send the assignment back to the node
        3. Wait for all nodes to connect
        4. Send complete network topology to the node
        5. Signal the node to start the protocol
        6. Close the bootstrap connection

        Args:
            reader: Stream for reading data from the connecting node
            writer: Stream for sending data to the connecting node
        """

        # Get connection information for logging
        peer_address = writer.get_extra_info("peername")
        print(f"[Bootstrap Server] New connection from {peer_address}")

        # === NODE ID AND PORT ASSIGNMENT ===

        # Check if we can accept this connection
        if self.topology_sent or not self.available_ids:
            # Either we've already completed bootstrap or we're at capacity
            print(
                f"[Bootstrap Server] Rejecting connection from {peer_address} - bootstrap complete or network full"
            )

            error_msg = {
                "type": "ERROR",
                "message": "Network is full or bootstrap already complete",
            }
            await self.send_json_message(writer, error_msg)

            # Close the connection
            writer.close()
            await writer.wait_closed()
            return

        # Assign the lowest available node ID
        node_id = min(self.available_ids)
        self.available_ids.remove(node_id)

        # Calculate the listening port for this node
        # Node 0 gets port 9000, Node 1 gets 9001, etc.
        assigned_port = self.node_port_start + node_id

        # Store information about this connected node
        self.connected_nodes[node_id] = {
            "writer": writer,
            "port": assigned_port,
            "address": peer_address,
            "connected_at": asyncio.get_event_loop().time(),
        }

        print(f"[Bootstrap Server] Assigned Node ID {node_id} to {peer_address}")
        print(f"[Bootstrap Server] Node {node_id} will listen on port {assigned_port}")

        # === SEND ASSIGNMENT TO NODE ===

        # Send the node its assigned ID and port
        assignment_msg = {
            "type": "ASSIGNMENT",
            "node_id": node_id,
            "listening_port": assigned_port,
            "bootstrap_host": self.host,
        }

        await self.send_json_message(writer, assignment_msg)
        print(f"[Bootstrap Server] Sent assignment to Node {node_id}")

        # === CHECK IF ALL NODES CONNECTED ===

        nodes_connected = len(self.connected_nodes)
        print(
            f"[Bootstrap Server] Progress: {nodes_connected}/{self.expected_nodes} nodes connected"
        )

        if nodes_connected == self.expected_nodes:
            print()
            print(f"[Bootstrap Server] All {self.expected_nodes} nodes connected!")
            print(f"[Bootstrap Server] Broadcasting network topology...")

            # Send network topology to all nodes
            await self.broadcast_network_topology()

            # Mark topology as sent to prevent duplicate broadcasts
            self.topology_sent = True

            # Signal that bootstrap is complete
            self.all_nodes_connected.set()

        # === WAIT FOR BOOTSTRAP COMPLETION ===

        try:
            # Wait until bootstrap is complete for all nodes
            await self.all_nodes_connected.wait()

            # Send final START signal to this node
            start_msg = {
                "type": "START",
                "message": "Bootstrap complete - begin protocol execution",
            }
            await self.send_json_message(writer, start_msg)

            print(f"[Bootstrap Server] Sent START signal to Node {node_id}")

        except Exception as e:
            print(f"[Bootstrap Server] Error during bootstrap for Node {node_id}: {e}")

        finally:
            # === CLEANUP CONNECTION ===

            print(f"[Bootstrap Server] Closing bootstrap connection to Node {node_id}")

            # Close the connection gracefully
            writer.close()
            await writer.wait_closed()

            # Remove from our tracking (if bootstrap failed before completion)
            if node_id in self.connected_nodes:
                del self.connected_nodes[node_id]

                # If bootstrap hasn't completed yet, make the ID available again
                if not self.topology_sent:
                    self.available_ids.add(node_id)
                    print(
                        f"[Bootstrap Server] Node ID {node_id} returned to available pool"
                    )

    async def broadcast_network_topology(self):
        """
        Send the complete network topology to all connected nodes.

        The topology includes information about all nodes in the network,
        including their IDs, listening ports, and hostnames. This allows
        each node to establish direct connections with all other nodes.
        """

        # Build the complete network topology
        network_topology = self.build_network_topology()

        print(f"[Bootstrap Server] Network topology:")
        for node_info in network_topology["nodes"]:
            print(
                f"[Bootstrap Server]   Node {node_info['id']}: {node_info['host']}:{node_info['port']}"
            )

        # Create the topology message
        topology_msg = {"type": "TOPOLOGY", "network": network_topology}

        # Send topology to all connected nodes
        successful_sends = 0
        for node_id, node_info in self.connected_nodes.items():
            try:
                await self.send_json_message(node_info["writer"], topology_msg)
                successful_sends += 1
                print(f"[Bootstrap Server] Sent topology to Node {node_id}")

            except Exception as e:
                print(
                    f"[Bootstrap Server] Failed to send topology to Node {node_id}: {e}"
                )

        print(
            f"[Bootstrap Server] Topology broadcast complete: {successful_sends}/{len(self.connected_nodes)} successful"
        )

    def build_network_topology(self) -> Dict:
        """
        Build a complete description of the network topology.

        Returns:
            Dictionary containing network information that nodes need
            to establish direct connections with each other
        """

        # Create list of all nodes with their connection information
        nodes = []
        for node_id, node_info in self.connected_nodes.items():
            nodes.append(
                {
                    "id": node_id,
                    "host": self.host,  # All nodes run on same host for simulation
                    "port": node_info["port"],
                }
            )

        # Sort by node ID for consistent ordering
        nodes.sort(key=lambda x: x["id"])

        # Build complete topology description
        topology = {
            "total_nodes": len(nodes),
            "nodes": nodes,
            "bootstrap_complete": True,
        }

        return topology

    async def send_json_message(self, writer: asyncio.StreamWriter, message: Dict):
        """
        Send a JSON message to a node over the bootstrap connection.

        Messages are sent as JSON strings followed by a newline.
        This provides a simple, readable protocol for the bootstrap phase.

        Args:
            writer: The stream writer for the node connection
            message: Dictionary to send as JSON
        """
        try:
            # Convert message to JSON and add newline
            json_data = json.dumps(message) + "\n"

            # Send the message
            writer.write(json_data.encode("utf-8"))
            await writer.drain()

        except Exception as e:
            print(f"[Bootstrap Server] Error sending message: {e}")
            raise


# Example usage and testing
async def main():
    """
    Example of how to start the bootstrap server.
    """
    # Create and start the bootstrap server
    # This will wait for 3 nodes to connect, assign them IDs 0,1,2
    # and ports 9000,9001,9002 respectively
    server = NetworkServer(
        host="127.0.0.1", port=8888, expected_nodes=4, node_port_start=9000
    )

    await server.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBootstrap server stopped by user")
