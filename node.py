import time
from collections import defaultdict

from utilities.common_coin import common_coin
from utilities.hash_util import hash_value
from utilities.predicate import predicate


class Node:
    def __init__(self, node_id, input_value):
        self.node_id = node_id
        self.network = None
        self.input_value = input_value
        self.inboxes = defaultdict(asyncio.Queue)

        self.round = 0
        self.hashes = {}
        self.proposals = {}
        self.output = None

    def set_network(self, network):
        self.network = network

    def get_all_peer_ids(self):
        return self.network.get_all_node_ids()

    async def send(self, receiver_id, message):
        await self.network.send(receiver_id, message)

    async def broadcast(self, message):
        await self.network.broadcast(self.node_id, message, self.get_all_peer_ids())

    async def receive(self):
        return await self.network.receive(self.node_id)

    def log(self, message):
        print(f"Node {self.node_id}: {message}")

    async def handle_message(self, incoming_msg, target_hash=None):
        msg_type = incoming_msg["type"]

        if msg_type == "acidh_hash":
            sender = incoming_msg["sender"]
            sender_hash = incoming_msg["hash"]

            if sender not in self.hashes:
                self.hashes[sender] = sender_hash
                self.log(f"Received hash from {sender}: {sender_hash}")

        elif msg_type == "drh_request":
            requested_hash = incoming_msg["hash"]
            sender = incoming_msg["sender"]

            my_hash = hash_value(self.input_value)
            if requested_hash == my_hash:
                response_msg = {
                    "type": "drh_response",
                    "value": self.input_value,
                    "sender": self.node_id,
                }
                await self.send(sender, response_msg)

        elif msg_type in {"drh_response", "abba_vote"}:
            await self.inboxes[msg_type].put(incoming_msg)

        # if incoming_msg["type"] == "drh_response":
        #     candidate = incoming_msg["value"]
        #     if target_hash and (hash_value(candidate) == target_hash):
        #         self.proposals[target_hash] = candidate
        #         self.log(f"Retrieved valid value from DRh: {candidate}")
        #         return candidate

    async def acidh_broadcast(self):
        hashed_i = hash_value(self.input_value)
        self.hashes[self.node_id] = hashed_i
        msg = {"type": "acidh_hash", "hash": hashed_i, "sender": self.node_id}
        await self.broadcast(msg)

        nodes_count = len(self.get_all_peer_ids())
        while len(self.hashes) < nodes_count:
            incoming = await self.receive()
            await self.handle_message(incoming)

    async def drh_retrieve(self, target_hash):
        request_msg = {
            "type": "drh_request",
            "hash": target_hash,
            "sender": self.node_id,
        }
        await self.broadcast(request_msg)

        while True:
            msg = await self.inboxes["drh_response"].get()
            candidate = msg["value"]
            if hash_value(candidate) == target_hash:
                self.proposals[target_hash] = candidate
                self.log(f"Retrieved valid value from DRh: {candidate}")
                return candidate

    async def execute_mvbah(self):
        self.log("Starting OciorMVBAh execution...")

        # Broadcast hash and collect others' hash
        await self.acidh_broadcast()

        round = 0

        all_node_ids = self.get_all_peer_ids()
        context = "mvbah"

        while True:
            round += 1
            self.log(f"Round {round}:")

            # Elect a leader using common coin
            leader_id = common_coin(round, context, all_node_ids)
            self.log(f"Leader selected: Node {leader_id}")

            target_hash = self.hashes.get(leader_id)
            if not target_hash:
                self.log(f"No hash found for leader {leader_id}, skipping round")
                continue

            w_l = await self.drh_retrieve(target_hash)
            if w_l is None:
                self.log(
                    f"Failed to retrieve value for hash {target_hash}, skipping round"
                )
                continue

            b = 1 if await predicate(w_l) else 0

            c = await self.abba(round, context, b)
            self.log(f"ABBA result: {c}")

            if c == 1:
                self.output = w_l
                self.log(f"Decided on value: {w_l}")
                return w_l
            self.log("Continuing to next round...")

    async def abba(self, round_id, context_id, inital_bit):
        node_ids = self.get_all_peer_ids()

        n = len(node_ids)
        t = (n - 1) // 3
        threshold = 2 * t + 1

        bit = inital_bit
        round = 0

        while True:
            round += 1

            msg = {
                "type": "abba_vote",
                "round": round,
                "bit": bit,
                "sender": self.node_id,
            }
            await self.broadcast(msg)
            self.log(f"ABBA round {round}: broadcasting vote {bit}")
            votes = {bit}
            seen_from = {self.node_id}

            while len(seen_from) < n:
                incoming = await self.inboxes["abba_vote"].get()

                if incoming["round"] == round:
                    sender = incoming["sender"]
                    vote_bit = incoming["bit"]
                    if sender not in seen_from:
                        seen_from.add(sender)
                        votes.add(vote_bit)
            if len(seen_from) >= threshold:
                if len(votes) == 1:
                    decision = votes.pop()
                    self.log(f"ABBA decided {decision} in round {round}")
                    return decision

            bit = common_coin(round_id + round, context_id, [0, 1])
            self.log(f"ABBA continues to next round with coin flip: {bit}")

    async def message_loop(self):
        while True:
            msg = await self.receive()
            await self.handle_message(msg)

    async def run(self):
        asyncio.create_task(self.message_loop())
        result = await self.execute_mvbah()
        return result


async def main():
    from network import Network

    num_nodes = 3

    nodes = [Node(node_id=i, input_value=f"value_from_{i}") for i in range(num_nodes)]
    network = Network()
    network.register_nodes(nodes)
    results = await asyncio.gather(*(node.run() for node in nodes))

    print("\n --- Final Results ---")
    for i, result in enumerate(results):
        print(f"Node {i} decided on: {result}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
