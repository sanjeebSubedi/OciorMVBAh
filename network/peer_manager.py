import asyncio
import json
from typing import Dict, List, Optional


class PeerConnectionManager:
    """Handles all P2P TCP connections for a node."""

    def __init__(self, node_id: int, listen_port: int, peers: List[Dict]):
        self.node_id = node_id
        self.listen_port = listen_port
        self.peers = peers  # list of dicts with id, host, port

        self.server: Optional[asyncio.Server] = None
        self.peer_writers: Dict[int, asyncio.StreamWriter] = {}
        self.peer_readers: Dict[int, asyncio.StreamReader] = {}
        self.incoming: asyncio.Queue = asyncio.Queue()
        self._maintainer_task: Optional[asyncio.Task] = None

    async def start(self):
        self.server = await asyncio.start_server(
            self._handle_inbound, host="127.0.0.1", port=self.listen_port
        )
        # initial dial + maintainer
        await self._initial_connect()
        self._maintainer_task = asyncio.create_task(self._maintain())

    async def stop(self):
        if self._maintainer_task:
            self._maintainer_task.cancel()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for w in self.peer_writers.values():
            w.close()
            try:
                await w.wait_closed()
            except:
                pass

    # ----------------------- sending -------------------
    async def send(self, peer_id: int, payload: dict):
        if peer_id not in self.peer_writers:
            raise ValueError("no connection to peer")
        writer = self.peer_writers[peer_id]
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()

    async def broadcast(self, payload: dict):
        for pid in list(self.peer_writers.keys()):
            try:
                await self.send(pid, payload)
            except Exception:
                pass

    # ----------------------- receiving -----------------
    async def recv(self):
        return await self.incoming.get()

    # ----------------------- internals -----------------
    async def _initial_connect(self):
        for peer in self.peers:
            await self._try_connect(peer)

    async def _maintain(self):
        try:
            while True:
                for peer in self.peers:
                    if peer["id"] in self.peer_writers:
                        continue
                    if self.node_id < peer["id"]:
                        await self._try_connect(peer)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def _try_connect(self, peer):
        pid = peer["id"]
        host, port = peer["host"], peer["port"]
        try:
            reader, writer = await asyncio.open_connection(host, port)
            handshake = json.dumps({"type": "IDENTIFY", "sender": self.node_id}) + "\n"
            writer.write(handshake.encode())
            await writer.drain()
            self._register_peer(pid, reader, writer)
        except (ConnectionRefusedError, OSError):
            pass

    async def _handle_inbound(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
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

        if msg.get("type") != "IDENTIFY":
            writer.close()
            await writer.wait_closed()
            return
        peer_id = msg.get("sender")

        # duplicate handling
        if peer_id in self.peer_writers:
            if self.node_id < peer_id:
                writer.close()
                await writer.wait_closed()
                return
            else:
                # replace existing
                try:
                    self.peer_writers[peer_id].close()
                    await self.peer_writers[peer_id].wait_closed()
                except:
                    pass
        self._register_peer(peer_id, reader, writer)

    def _register_peer(self, pid, reader, writer):
        self.peer_writers[pid] = writer
        self.peer_readers[pid] = reader
        asyncio.create_task(self._listen(pid, reader))

    async def _listen(self, pid, reader):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode().strip())
                    await self.incoming.put((pid, msg))
                except json.JSONDecodeError:
                    continue
        finally:
            self.peer_writers.pop(pid, None)
            self.peer_readers.pop(pid, None)
