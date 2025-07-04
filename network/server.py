import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="[Server] %(message)s")
log = logging.getLogger(__name__)


@dataclass
class NodeRecord:
    writer: asyncio.StreamWriter
    port: int
    address: str


class NetworkServer:
    """Bootstrap/discovery server.

    Workflow:
        1. Wait for expected_nodes TCP connections.
        2. Assign node_id & listening_port, send ASSIGNMENT.
        3. When all nodes are in, broadcast TOPOLOGY then START.
        4. Close all sockets and exit.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8888,
        *,
        expected_nodes: int = 3,
        node_port_start: int = 9000,
    ):
        self.host = host
        self.port = port
        self.expected_nodes = expected_nodes
        self.node_port_start = node_port_start

        self.connected: Dict[int, NodeRecord] = {}
        self.available_ids = list(range(expected_nodes))
        self.all_connected = asyncio.Event()

    # ---------------------------------------------------------------------
    async def start(self):
        log.info(
            f"Listening on {self.host}:{self.port} (expecting {self.expected_nodes} nodes)"
        )
        server = await asyncio.start_server(self._handle_client, self.host, self.port)
        async with server:
            await self.all_connected.wait()
            await self._broadcast_topology_and_start()
        log.info("Bootstrap done; shutting down")

    # ---------------------------------------------------------------------
    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        if self.all_connected.is_set() or not self.available_ids:
            writer.close()
            await writer.wait_closed()
            return

        node_id = self.available_ids.pop(0)
        port = self.node_port_start + node_id
        addr = writer.get_extra_info("peername")
        self.connected[node_id] = NodeRecord(writer, port, addr[0])
        log.info(f"Node {node_id} connected from {addr}")

        await self._send(
            writer,
            {
                "type": "ASSIGNMENT",
                "node_id": node_id,
                "listening_port": port,
                "bootstrap_host": self.host,
            },
        )

        if len(self.connected) == self.expected_nodes:
            self.all_connected.set()

        # keep the socket open until shutdown
        try:
            await reader.read()  # read until client closes (after START)
        except ConnectionResetError:
            pass

    # ------------------------------------------------------------------
    async def _broadcast_topology_and_start(self):
        topo = {
            "total_nodes": len(self.connected),
            "nodes": [
                {"id": nid, "host": rec.address, "port": rec.port}
                for nid, rec in sorted(self.connected.items())
            ],
            "bootstrap_complete": True,
        }
        msg_topo = {"type": "TOPOLOGY", "network": topo}
        msg_start = {"type": "START"}

        await asyncio.gather(
            *(self._send(rec.writer, msg_topo) for rec in self.connected.values())
        )
        await asyncio.gather(
            *(self._send(rec.writer, msg_start) for rec in self.connected.values())
        )
        # allow some flush time
        await asyncio.sleep(0.1)
        for rec in self.connected.values():
            rec.writer.close()
            await rec.writer.wait_closed()

    # ------------------------------------------------------------------
    async def _send(self, writer: asyncio.StreamWriter, payload: dict):
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
