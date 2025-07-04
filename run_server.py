import asyncio

from network.server import NetworkServer

if __name__ == "__main__":
    try:
        asyncio.run(NetworkServer().start())
    except KeyboardInterrupt:
        print("Server stopped")
