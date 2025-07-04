import asyncio
import json
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class BootstrapInfo:
    node_id: int
    listening_port: int
    peers: List[Dict]  # list of dicts with id, host, port


class BootstrapClient:
    """Connects to the bootstrap server and retrieves network topology."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8888):
        self.host = host
        self.port = port

    async def bootstrap(self) -> BootstrapInfo:
        reader, writer = await self._connect_with_retry()

        node_id = None
        listen_port = None
        peers: List[Dict] = []

        # receive messages until START
        while True:
            line = await reader.readline()
            if not line:
                raise RuntimeError("bootstrap server closed connection early")
            try:
                msg = json.loads(line.decode().strip())
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "ASSIGNMENT":
                node_id = msg["node_id"]
                listen_port = msg["listening_port"]
            elif mtype == "TOPOLOGY":
                topo = msg["network"]
                peers = [n for n in topo["nodes"] if n["id"] != node_id]
            elif mtype == "START":
                break
            elif mtype == "ERROR":
                raise RuntimeError(f"Bootstrap error: {msg.get('message')}")

        writer.close()
        await writer.wait_closed()
        if node_id is None or listen_port is None:
            raise RuntimeError("bootstrap did not provide assignment")
        return BootstrapInfo(node_id, listen_port, peers)

    async def _connect_with_retry(self):
        for attempt in range(10):
            try:
                return await asyncio.open_connection(self.host, self.port)
            except ConnectionRefusedError:
                if attempt == 9:
                    raise
                await asyncio.sleep(0.3)
