#!/usr/bin/env python3
"""
TCP Bootstrap Server for MVBA Protocol
=====================================

This server handles the initial setup phase:
1. Waits for n nodes to connect
2. Assigns unique node IDs
3. Distributes initialization data (peer list, ports, etc.)
4. Shuts down after all nodes are ready

Usage: python bootstrap_server.py [--nodes N] [--port PORT]
"""

import argparse
import json
import logging
import socket
import threading
import time
from typing import Dict, List, Optional, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("BootstrapServer")


class BootstrapServer:
    def __init__(self, port: int = 8080, n_nodes: int = 4):
        self.port = port
        self.n_nodes = n_nodes
        self.t = (n_nodes - 1) // 3  # Byzantine threshold: n >= 3t + 1

        # Track connected nodes
        self.connected_nodes: Dict[int, socket.socket] = {}
        self.node_counter = 0
        self.initialization_complete = False

        # ZeroMQ port configuration for P2P communication
        self.base_zmq_port = 5555
        self.zmq_ports = self._generate_port_assignments()

        # Thread safety
        self.lock = threading.Lock()

        logger.info(f"Bootstrap server configured for {n_nodes} nodes (t={self.t})")
        logger.info(f"ZeroMQ ports: {self.zmq_ports}")

    def _generate_port_assignments(self) -> Dict[int, int]:
        """Generate ZeroMQ port assignments for each node"""
        return {i: self.base_zmq_port + i for i in range(self.n_nodes)}

    def _create_initialization_data(self, node_id: int) -> Dict:
        """Create initialization data for a specific node"""
        # Create peer list (all nodes except self)
        peer_list = []
        for i in range(self.n_nodes):
            if i != node_id:
                peer_list.append(
                    {
                        "node_id": str(i),
                        "zmq_port": self.zmq_ports[i],
                        "address": "localhost",  # For local development
                    }
                )

        return {
            "node_id": node_id,
            "total_nodes": self.n_nodes,
            "byzantine_threshold": self.t,
            "my_zmq_port": self.zmq_ports[node_id],
            "peers": peer_list,
            "protocol_config": {
                "session_id": int(time.time()),  # Unique session identifier
                "erasure_code_threshold": self.t + 1,  # For (n, t+1) erasure coding
                "timeout_seconds": 30,  # Protocol timeouts
            },
        }

    def _handle_node_connection(
        self, client_socket: socket.socket, address: Tuple[str, int]
    ) -> None:
        """Handle a single node connection"""
        try:
            logger.info(f"Node connected from {address}")

            # Assign node ID
            with self.lock:
                if self.node_counter >= self.n_nodes:
                    logger.warning(
                        f"Rejecting connection from {address} - all nodes already connected"
                    )
                    client_socket.close()
                    return

                node_id = self.node_counter
                self.connected_nodes[node_id] = client_socket
                self.node_counter += 1

                logger.info(f"Assigned node_id={node_id} to {address}")

                # Send immediate acknowledgment with node ID
                ack_msg = json.dumps(
                    {
                        "status": "connected",
                        "node_id": node_id,
                        "waiting_for": self.n_nodes - self.node_counter,
                    }
                )
                client_socket.send(f"{ack_msg}\n".encode())

                # Check if all nodes are connected
                if self.node_counter == self.n_nodes:
                    logger.info("All nodes connected! Starting initialization...")
                    self._initialize_all_nodes()

        except Exception as e:
            logger.error(f"Error handling connection from {address}: {e}")
            if client_socket:
                client_socket.close()

    def _initialize_all_nodes(self) -> None:
        """Send initialization data to all connected nodes"""
        try:
            # Send initialization data to each node
            for node_id, client_socket in self.connected_nodes.items():
                init_data = self._create_initialization_data(node_id)
                init_msg = json.dumps({"status": "initialize", "data": init_data})

                client_socket.send(f"{init_msg}\n".encode())
                logger.info(f"Sent initialization data to node {node_id}")

            # Wait for acknowledgments
            self._wait_for_acknowledgments()

            # Send final "start" signal
            for node_id, client_socket in self.connected_nodes.items():
                start_msg = json.dumps({"status": "start"})
                client_socket.send(f"{start_msg}\n".encode())
                logger.info(f"Sent start signal to node {node_id}")

            # Close all connections
            self._cleanup_connections()

            self.initialization_complete = True
            logger.info(
                "🎉 All nodes initialized successfully! Bootstrap server shutting down."
            )

        except Exception as e:
            logger.error(f"Error during initialization: {e}")
            self._cleanup_connections()

    def _wait_for_acknowledgments(self) -> None:
        """Wait for all nodes to acknowledge initialization"""
        ack_count = 0
        timeout = 10  # seconds

        for node_id, client_socket in self.connected_nodes.items():
            try:
                client_socket.settimeout(timeout)
                response = client_socket.recv(1024).decode().strip()

                if response:
                    ack_data = json.loads(response)
                    if ack_data.get("status") == "ready":
                        ack_count += 1
                        logger.info(f"Node {node_id} acknowledged initialization")

            except socket.timeout:
                logger.warning(f"Node {node_id} did not acknowledge within {timeout}s")
            except Exception as e:
                logger.error(f"Error receiving acknowledgment from node {node_id}: {e}")

        logger.info(f"Received {ack_count}/{self.n_nodes} acknowledgments")

    def _cleanup_connections(self) -> None:
        """Close all client connections"""
        for node_id, client_socket in self.connected_nodes.items():
            try:
                client_socket.close()
            except:
                pass
        self.connected_nodes.clear()

    def start(self) -> None:
        """Start the bootstrap server"""
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            server_socket.bind(("localhost", self.port))
            server_socket.listen(self.n_nodes)

            logger.info(f"🚀 Bootstrap server started on localhost:{self.port}")
            logger.info(f"Waiting for {self.n_nodes} nodes to connect...")

            while not self.initialization_complete:
                try:
                    client_socket, address = server_socket.accept()

                    # Handle each connection in a separate thread
                    thread = threading.Thread(
                        target=self._handle_node_connection,
                        args=(client_socket, address),
                    )
                    thread.daemon = True
                    thread.start()

                except socket.error as e:
                    if not self.initialization_complete:
                        logger.error(f"Socket error: {e}")
                        break

        except Exception as e:
            logger.error(f"Server error: {e}")

        finally:
            server_socket.close()
            logger.info("Bootstrap server stopped")


def main():
    parser = argparse.ArgumentParser(description="MVBA Bootstrap Server")
    parser.add_argument(
        "--nodes",
        "-n",
        type=int,
        default=4,
        help="Number of nodes to wait for (default: 4)",
    )
    parser.add_argument(
        "--port", "-p", type=int, default=8080, help="Server port (default: 8080)"
    )

    args = parser.parse_args()

    # Validate node count for Byzantine fault tolerance
    if args.nodes < 4:
        logger.error(
            "Need at least 4 nodes for Byzantine fault tolerance (n >= 3t + 1)"
        )
        return

    server = BootstrapServer(port=args.port, n_nodes=args.nodes)

    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server failed: {e}")


if __name__ == "__main__":
    main()
