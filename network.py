import asyncio
from collections import defaultdict

from node import Node


class Network:
    def __init__(self):
        self.message_queues = defaultdict(asyncio.Queue)
        self.nodes = {}

    def register_nodes(self, node_list: list[Node]):
        for node in node_list:
            self.nodes[node.node_id] = node
            node.set_network(self)
            print(f"Node {node.node_id} registered into the network.")

    def get_all_node_ids(self):
        return list(self.nodes.keys())

    async def send(self, receiver_id: int, message: dict):
        await self.message_queues[receiver_id].put(message)

    async def broadcast(self, sender_id: int, message: dict, all_node_ids: list[int]):
        for receiver_id in all_node_ids:
            if receiver_id != sender_id:
                await self.send(receiver_id, message.copy())

    async def receive(self, node_id: int):
        return await self.message_queues[node_id].get()


async def main():
    network = Network()
    await network.broadcast(0, {"type": "hash", "value": "abc"}, [0, 1, 2, 3])
    msg = await network.receive(1)
    print(msg)


if __name__ == "__main__":
    asyncio.run(main())
