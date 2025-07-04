import asyncio

from node.node import DistributedNode


async def main():
    node = DistributedNode()
    await node.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Node stopped")
