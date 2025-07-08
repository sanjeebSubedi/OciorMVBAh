"""ZeroMQ networking layer used by Ocior-MVBAh nodes.
This file was extracted from the original monolithic `node.py` so that all
network/transport concerns live in one place.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List

import zmq

from protocol.common.message import MVBAMessage

__all__ = ["ZeroMQManager"]


class ZeroMQManager:
    """Manages ZeroMQ ROUTER/DEALER sockets and delivers raw messages.

    The class is intentionally *dumb*: it does **no** protocol validation – it
    simply (de)serialises :class:`~protocol.common.message.MVBAMessage` objects
    and invokes the callback supplied by the core :pyclass:`~core.node.Node`.
    """

    def __init__(self, node_id: int, my_port: int, peers: List[Dict]):
        self.node_id = node_id
        self.my_port = my_port
        self.peers = peers

        self.context = zmq.Context()
        self.poller = zmq.Poller()

        # Sockets for communication
        self.router_socket: zmq.Socket | None = None  # receive
        self.dealer_sockets: Dict[int | str, zmq.Socket] = {}

        self.running = False
        self._message_handler = None

        self.logger = logging.getLogger(f"ZMQ-Node{node_id}")

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def setup_connections(self) -> bool:
        """Create ROUTER socket + one DEALER per peer."""
        try:
            # ROUTER for inbound
            self.router_socket = self.context.socket(zmq.ROUTER)
            self.router_socket.bind(f"tcp://*:{self.my_port}")
            self.poller.register(self.router_socket, zmq.POLLIN)
            self.logger.info("Listening on port %s", self.my_port)

            # DEALERs for outbound
            for peer in self.peers:
                peer_id_str = peer["node_id"]
                peer_id_int = int(peer_id_str)
                dealer = self.context.socket(zmq.DEALER)
                dealer.setsockopt(zmq.IDENTITY, str(self.node_id).encode())
                dealer.connect(f"tcp://{peer['address']}:{peer['zmq_port']}")
                # store under both int and str
                self.dealer_sockets[peer_id_int] = dealer
                self.dealer_sockets[peer_id_str] = dealer
                self.logger.info(
                    "Connected to peer %s on %s:%s",
                    peer_id_int,
                    peer["address"],
                    peer["zmq_port"],
                )
            return True
        except Exception as exc:  # pragma: no cover – network errors at runtime
            self.logger.error("Failed to set up connections: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Sending helpers
    # ------------------------------------------------------------------

    def send_message(self, target_node_id: int | str, msg: MVBAMessage) -> bool:
        try:
            if target_node_id not in self.dealer_sockets:
                alt = (
                    str(target_node_id)
                    if isinstance(target_node_id, int)
                    else int(target_node_id)
                )
                if alt not in self.dealer_sockets:
                    self.logger.error("No connection to node %s", target_node_id)
                    return False
                target_node_id = alt
            self.dealer_sockets[target_node_id].send_string(msg.to_json())
            self.logger.debug("Sent %s to node %s", msg.msg_type, target_node_id)
            return True
        except Exception as exc:
            self.logger.error("Error sending to node %s: %s", target_node_id, exc)
            return False

    def broadcast_message(self, msg: MVBAMessage) -> int:
        ok = 0
        for pid in self.dealer_sockets:
            ok += self.send_message(pid, msg)
        self.logger.debug("Broadcast %s to %d peers", msg.msg_type, ok)
        return ok

    # ------------------------------------------------------------------
    # Receiving loop
    # ------------------------------------------------------------------

    def start_message_loop(self, handler):
        self._message_handler = handler
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        self.logger.info("Message loop started")

    def _loop(self):  # pragma: no cover – infinite loop
        while self.running:
            try:
                socks = dict(self.poller.poll(timeout=100))
                if self.router_socket in socks:
                    _sender, raw = self.router_socket.recv_multipart()
                    try:
                        msg = MVBAMessage.from_json(raw.decode())
                        if self._message_handler:
                            self._message_handler(msg)
                    except Exception as exc:
                        self.logger.error("Error processing message: %s", exc)
            except zmq.ZMQError as zerr:
                if zerr.errno == zmq.ETERM:
                    break
                self.logger.error("ZMQ error: %s", zerr)
            except Exception as exc:
                self.logger.error("Unexpected error in loop: %s", exc)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self.running = False
        if self.router_socket:
            self.router_socket.close()
        for sock in self.dealer_sockets.values():
            sock.close()
        self.context.term()
        self.logger.info("ZeroMQ manager stopped")
