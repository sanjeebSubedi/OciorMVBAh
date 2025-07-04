import asyncio
import json
import sys
from typing import Dict, List, Optional

from utilities.predicate import predicate  # new import


class DistributedNode:
    """
    A minimal distributed node for Byzantine Agreement protocol simulations.

    This version only handles:
    1. Bootstrap Phase: Connect to bootstrap server, get network topology
    2. Basic Setup: Start listening for peer connections
    3. Ready State: Signal that the node is ready for protocol implementation

    Message sending/receiving will be implemented in later iterations.
    """

    def __init__(self, bootstrap_host="127.0.0.1", bootstrap_port=8888):
        """
        Initialize a new distributed node.

        Args:
            bootstrap_host: IP address of the bootstrap server
            bootstrap_port: Port of the bootstrap server
        """
        # Bootstrap server connection details
        self.bootstrap_host = bootstrap_host
        self.bootstrap_port = bootstrap_port

        # Node identity - set during bootstrap phase
        self.node_id: Optional[int] = None
        self.listening_port: Optional[int] = None

        # Network topology - received from bootstrap server
        self.peer_nodes: List[Dict] = []  # List of other nodes in the network

        # Peer connection maps (outgoing and incoming)
        self.peer_writers: Dict[int, asyncio.StreamWriter] = {}
        self.peer_readers: Dict[int, asyncio.StreamReader] = {}

        # Queue for incoming protocol messages
        self.incoming_messages: asyncio.Queue = asyncio.Queue()

        # Node server for accepting peer connections
        self.node_server: Optional[asyncio.Server] = None

        # State flags
        self.bootstrap_complete = False
        self.ready_for_protocol = False

        # Input value for the protocol
        self.input_value: Optional[str] = None

        # background maintainer task placeholder (set later)
        self._maintainer_task: Optional[asyncio.Task] = None

    async def start(self):
        """
        Start the node and complete basic setup:
        1. Bootstrap phase: Connect to server, get topology
        2. Setup phase: Start listening for peer connections
        3. Protocol phase: Get input value and begin consensus
        """
        try:
            # === BOOTSTRAP PHASE ===
            await self.bootstrap_with_server()

            # === BASIC SETUP PHASE ===
            await self.start_node_server()

            # Connect to peers (initial attempt)
            await self.connect_to_peers()

            # Start background connection maintainer
            self._maintainer_task = asyncio.create_task(self.maintain_connections())

            # === READY STATE ===
            self.ready_for_protocol = True

            # === PROTOCOL INITIALIZATION ===
            await self.initialize_protocol()

            # Keep the node running
            await self.wait_for_shutdown()

        except KeyboardInterrupt:
            print(f"\n[Node {self.node_id}] Shutting down by user request...")
        except Exception as e:
            print(f"[Node {self.node_id}] Error during startup: {e}")
        finally:
            await self.cleanup()

    async def bootstrap_with_server(self):
        """
        Complete the bootstrap phase with the bootstrap server.

        This phase:
        1. Connects to the bootstrap server
        2. Receives node ID and listening port assignment
        3. Receives network topology (list of peer nodes)
        4. Waits for START signal
        5. Disconnects from bootstrap server
        """

        # === CONNECT TO BOOTSTRAP SERVER ===

        # Retry connection in case bootstrap server is starting up
        for attempt in range(10):
            try:
                reader, writer = await asyncio.open_connection(
                    self.bootstrap_host, self.bootstrap_port
                )
                break
            except ConnectionRefusedError:
                if attempt == 9:
                    raise Exception(
                        "Failed to connect to bootstrap server after 10 attempts"
                    )
                await asyncio.sleep(0.3)

        # === PROCESS BOOTSTRAP MESSAGES ===

        while True:
            # Read one message from bootstrap server
            line = await reader.readline()
            if not line:
                raise Exception("Bootstrap server closed connection unexpectedly")

            # Parse JSON message
            try:
                message = json.loads(line.decode().strip())
            except json.JSONDecodeError as e:
                continue

            message_type = message.get("type")

            if message_type == "ASSIGNMENT":
                # Received our node ID and port assignment
                self.node_id = message["node_id"]
                self.listening_port = message["listening_port"]

            elif message_type == "TOPOLOGY":
                # Received complete network topology
                network_topology = message["network"]
                self.peer_nodes = [
                    node
                    for node in network_topology["nodes"]
                    if node["id"] != self.node_id  # Exclude ourselves
                ]

            elif message_type == "START":
                # Bootstrap complete, can begin protocol setup
                self.bootstrap_complete = True
                break

            elif message_type == "ERROR":
                # Bootstrap server rejected us
                error_msg = message.get("message", "Unknown error")
                raise Exception(f"Bootstrap server error: {error_msg}")

        # === DISCONNECT FROM BOOTSTRAP SERVER ===

        writer.close()
        await writer.wait_closed()

    async def start_node_server(self):
        """
        Start the node server to accept connections from peer nodes.

        This creates a TCP server that other nodes can connect to for
        direct peer-to-peer communication during the protocol phase.
        """

        # Start server to accept connections from other nodes
        self.node_server = await asyncio.start_server(
            self.handle_peer_connection,
            host="127.0.0.1",  # Use same host as bootstrap for simplicity
            port=self.listening_port,
        )

        print(
            f"[Node {self.node_id}] Ready to accept connections from {len(self.peer_nodes)} peer nodes"
        )

    async def handle_peer_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Accept incoming connection and register peer after IDENTIFY handshake."""
        # Expect first message to be IDENTIFY
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        except asyncio.TimeoutError:
            writer.close()
            await writer.wait_closed()
            return
        try:
            msg = json.loads(line.decode().strip())
        except json.JSONDecodeError:
            writer.close()
            await writer.wait_closed()
            return

        if msg.get("type") != "IDENTIFY" or "sender" not in msg:
            writer.close()
            await writer.wait_closed()
            return

        peer_id = msg["sender"]
        if peer_id in self.peer_writers:
            # Duplicate connection: decide on deterministic close rule (keep lower initiated)
            if self.node_id < peer_id:
                # We are supposed to be initiator; close inbound dup
                writer.close()
                await writer.wait_closed()
                return
            else:
                # Keep inbound, close existing outbound
                try:
                    self.peer_writers[peer_id].close()
                    await self.peer_writers[peer_id].wait_closed()
                except:
                    pass
        self.peer_writers[peer_id] = writer
        self.peer_readers[peer_id] = reader
        # Start listener task for further messages
        asyncio.create_task(self.listen_to_peer(peer_id, reader))

    async def listen_to_peer(self, peer_id: int, reader: asyncio.StreamReader):
        """Continuously read JSON messages from a peer and put them into the incoming queue."""
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line.decode().strip())
                    # Immediate console output for demo
                    if message.get("type") == "CHAT":
                        print(
                            f"\n[Node {self.node_id}] <== CHAT from {peer_id}: {message.get('text')}\nNode {self.node_id}> ",
                            end="",
                            flush=True,
                        )
                    else:
                        print(
                            f"\n[Node {self.node_id}] <== {message.get('type')} from {peer_id}: {message}\nNode {self.node_id}> ",
                            end="",
                            flush=True,
                        )
                    await self.incoming_messages.put((peer_id, message))
                except json.JSONDecodeError:
                    continue
        finally:
            # Peer disconnected
            if peer_id in self.peer_writers:
                del self.peer_writers[peer_id]
            if peer_id in self.peer_readers:
                del self.peer_readers[peer_id]

    async def send_to_peer(self, peer_id: int, payload: dict):
        """Send a JSON-serializable payload to a specific peer."""
        if peer_id not in self.peer_writers:
            return False
        writer = self.peer_writers[peer_id]
        try:
            writer.write((json.dumps(payload) + "\n").encode())
            await writer.drain()
            return True
        except Exception:
            return False

    async def broadcast(self, payload: dict):
        """Broadcast a payload to all connected peers."""
        for peer_id in list(self.peer_writers.keys()):
            await self.send_to_peer(peer_id, payload)

    async def initialize_protocol(self):
        """
        Initialize the protocol by getting input value from the user and validating it
        using the predicate defined in utilities/predicate.py.
        """
        print(
            f"[Node {self.node_id}] Network established. Please provide your input value."
        )

        loop = asyncio.get_event_loop()
        while True:
            prompt = f"Node {self.node_id} - Enter your input value: "
            user_input = await loop.run_in_executor(None, lambda: input(prompt))

            # Validate using predicate
            if predicate(user_input, self):
                self.input_value = user_input
                print(f"[Node {self.node_id}] Predicate check passed.")
                break
            else:
                print(
                    f"[Node {self.node_id}] Predicate check failed. Please enter a valid value."
                )

        print(f"[Node {self.node_id}] Input value set to: {self.input_value}")
        print(f"[Node {self.node_id}] Ready to begin protocol execution")

    async def connect_to_peers(self):
        """Establish outgoing TCP connections to all peer nodes."""
        for peer in self.peer_nodes:
            peer_id = peer["id"]
            host, port = peer["host"], peer["port"]

            if peer_id in self.peer_writers:  # already connected (maybe inbound first)
                continue

            for attempt in range(5):  # retry logic
                try:
                    reader, writer = await asyncio.open_connection(host, port)

                    # IDENTIFY handshake so the remote side knows who just connected
                    handshake = (
                        json.dumps({"type": "IDENTIFY", "sender": self.node_id}) + "\n"
                    )
                    writer.write(handshake.encode())
                    await writer.drain()

                    # register streams
                    self.peer_writers[peer_id] = writer
                    self.peer_readers[peer_id] = reader

                    # background listener task
                    asyncio.create_task(self.listen_to_peer(peer_id, reader))
                    break  # success → go to next peer
                except (ConnectionRefusedError, OSError):
                    await asyncio.sleep(0.2 * (attempt + 1))

    async def send(self, payload: dict, peer_id: Optional[int] = None):
        """High-level helper to send a JSON payload.

        Args:
            payload: JSON-serialisable dict.
            peer_id: If None, broadcast to all peers; otherwise send only to that peer.
        """
        if peer_id is None:
            await self.broadcast(payload)
        else:
            await self.send_to_peer(peer_id, payload)

    async def receive(self, msg_type: str, timeout: Optional[float] = None):
        """Wait for and return the next incoming message of a given type.

        Args:
            msg_type: The 'type' field expected in the JSON payload.
            timeout: Optional timeout in seconds; None means wait forever.

        Returns:
            Tuple[int, dict] – (sender_id, message_dict) when a matching
            message arrives. Returns None if timeout occurs.
        """
        try:
            while True:
                peer_id, message = await asyncio.wait_for(
                    self.incoming_messages.get(), timeout
                )
                if message.get("type") == msg_type:
                    return peer_id, message
                # If message type doesn't match, you may choose to stash it for later.
                # For simplicity we just drop non-matching messages here.
        except asyncio.TimeoutError:
            return None

    def get_node_info(self) -> Dict:
        """
        Get basic information about this node.

        Returns:
            Dictionary with node ID, port, and peer information
        """
        return {
            "node_id": self.node_id,
            "listening_port": self.listening_port,
            "peer_count": len(self.peer_nodes),
            "peers": [peer["id"] for peer in self.peer_nodes],
            "bootstrap_complete": self.bootstrap_complete,
            "ready_for_protocol": self.ready_for_protocol,
            "input_value": self.input_value,
        }

    def print_status(self):
        """
        Print current node status for debugging/monitoring.
        """
        info = self.get_node_info()
        print(f"[Node {self.node_id}] === NODE STATUS ===")
        print(f"[Node {self.node_id}] Node ID: {info['node_id']}")
        print(f"[Node {self.node_id}] Listening Port: {info['listening_port']}")
        print(f"[Node {self.node_id}] Peer Nodes: {info['peers']}")
        print(f"[Node {self.node_id}] Bootstrap Complete: {info['bootstrap_complete']}")
        print(f"[Node {self.node_id}] Ready for Protocol: {info['ready_for_protocol']}")
        print(f"[Node {self.node_id}] Input Value: {info['input_value']}")
        print(f"[Node {self.node_id}] ==================")

    async def wait_for_shutdown(self):
        """
        Keep the node running until shutdown signal.

        For now, this just waits for Ctrl+C or provides a simple status interface.
        In future iterations, this will be replaced with protocol execution.
        """

        # Start the node server in the background
        server_task = asyncio.create_task(self.node_server.serve_forever())

        # Simple status interface
        loop = asyncio.get_event_loop()

        try:
            while True:
                # Get user input asynchronously
                prompt = f"Node {self.node_id}> "
                user_input = await loop.run_in_executor(None, lambda: input(prompt))

                command = user_input.strip().lower()

                if command == "status":
                    self.print_status()
                elif command == "quit" or command == "exit":
                    break
                elif command == "help":
                    print(
                        f"[Node {self.node_id}] Available commands: status, quit, help, send <peer_id/all> <message>"
                    )
                elif command.startswith("send "):
                    parts = user_input.split(" ", 2)
                    if len(parts) < 3:
                        print(
                            f"[Node {self.node_id}] Usage: send <peer_id/all> <message>"
                        )
                        continue
                    target, text = parts[1], parts[2]
                    payload = {"type": "CHAT", "text": text, "sender": self.node_id}
                    if target == "all":
                        await self.broadcast(payload)
                    else:
                        try:
                            tid = int(target)
                        except ValueError:
                            print(f"[Node {self.node_id}] Invalid peer id")
                            continue
                        await self.send_to_peer(tid, payload)
                    print(f"[Node {self.node_id}] ==> sent to {target}: {text}")
                elif command:
                    print(f"[Node {self.node_id}] Unknown command: {command}")

        except EOFError:
            print(f"\n[Node {self.node_id}] EOF received, shutting down...")
        finally:
            server_task.cancel()

    async def cleanup(self):
        """
        Clean up all connections and resources.
        """

        # Close node server
        if self.node_server:
            self.node_server.close()
            await self.node_server.wait_closed()

    async def maintain_connections(self):
        """Background task: periodically try to connect to missing peers (smaller-ID initiator rule)."""
        try:
            while True:
                for peer in self.peer_nodes:
                    peer_id = peer["id"]
                    if peer_id in self.peer_writers:
                        continue  # already connected
                    # Deterministic initiator: smaller node_id dials the connection
                    if self.node_id < peer_id:
                        await self._try_connect(peer)
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def _try_connect(self, peer):
        peer_id = peer["id"]
        host, port = peer["host"], peer["port"]
        try:
            reader, writer = await asyncio.open_connection(host, port)
            handshake = json.dumps({"type": "IDENTIFY", "sender": self.node_id}) + "\n"
            writer.write(handshake.encode())
            await writer.drain()
            self.peer_writers[peer_id] = writer
            self.peer_readers[peer_id] = reader
            asyncio.create_task(self.listen_to_peer(peer_id, reader))
        except (ConnectionRefusedError, OSError):
            pass


# Example usage
async def main():
    """
    Example of how to start a distributed node.
    """
    node = DistributedNode(bootstrap_host="127.0.0.1", bootstrap_port=8888)

    await node.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nNode stopped by user")
