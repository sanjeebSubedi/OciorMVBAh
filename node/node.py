import asyncio
import json
from typing import Dict, Optional

from network.bootstrap import BootstrapClient
from network.peer_manager import PeerConnectionManager
from utilities.predicate import predicate


class DistributedNode:
    """High-level node orchestrator that wires together bootstrap + peer manager."""

    def __init__(self, bootstrap_host: str = "127.0.0.1", bootstrap_port: int = 8888):
        self.bootstrap_host = bootstrap_host
        self.bootstrap_port = bootstrap_port

        # filled after bootstrap
        self.node_id: Optional[int] = None
        self.input_value: Optional[str] = None
        self.peer_mgr: Optional[PeerConnectionManager] = None

    async def start(self):
        # 1. bootstrap phase
        info = await BootstrapClient(
            self.bootstrap_host, self.bootstrap_port
        ).bootstrap()
        self.node_id = info.node_id

        # 2. network setup phase
        self.peer_mgr = PeerConnectionManager(
            info.node_id, info.listening_port, info.peers
        )
        await self.peer_mgr.start()

        # start background printer
        asyncio.create_task(self._printer())

        # 3. protocol init – ask for user input validated by predicate
        await self._collect_user_input()

        # 4. demo CLI
        await self._cli_loop()

    async def _collect_user_input(self):
        loop = asyncio.get_event_loop()
        while True:
            prompt = f"Node {self.node_id} - Enter your input value: "
            val = await loop.run_in_executor(None, lambda: input(prompt))
            if predicate(val, self):
                self.input_value = val
                print(f"[Node {self.node_id}] Predicate check passed.")
                break
            else:
                print(f"[Node {self.node_id}] Predicate check failed. Try again.")

    # Expose node_id list for predicate helper
    @property
    def peer_nodes(self):
        return [
            {"id": pid} for pid in list(self.peer_mgr.peer_writers.keys())
        ]  # quick proxy

    async def _cli_loop(self):
        loop = asyncio.get_event_loop()
        print(
            f"[Node {self.node_id}] Ready. Type 'send <peer_id/all> <msg>' or 'quit'."
        )
        while True:
            cmd = await loop.run_in_executor(
                None, lambda: input(f"Node {self.node_id}> ")
            )
            if cmd in ("quit", "exit"):
                break
            if cmd.startswith("send "):
                parts = cmd.split(" ", 2)
                if len(parts) < 3:
                    print("usage: send <peer_id/all> <msg>")
                    continue
                target, text = parts[1], parts[2]
                payload = {"type": "CHAT", "text": text, "sender": self.node_id}
                if target == "all":
                    await self.peer_mgr.broadcast(payload)
                else:
                    try:
                        await self.peer_mgr.send(int(target), payload)
                    except Exception as e:
                        print("send failed", e)
            # printer task handles incoming display

    async def _printer(self):
        """Continuously prints incoming messages as soon as they arrive."""
        while True:
            pid, msg = await self.peer_mgr.recv()
            mtype = msg.get("type")
            if mtype == "CHAT":
                out = msg.get("text")
            else:
                out = json.dumps(msg)
            print(
                f"\n[Node {self.node_id}] <== from {pid}: {out}\nNode {self.node_id}> ",
                end="",
                flush=True,
            )
