import asyncio
import unittest

import wqpu_public_security


class FakeMesh(object):
    def __init__(self):
        # Pre-create synchronization points so the test cannot race the scheduled
        # service coroutine while looking up its Event in the dictionary.
        self.started = {
            "one": asyncio.Event(),
            "two": asyncio.Event(),
        }
        self.release = asyncio.Event()

    async def handle_open_request(self, message, via=None):
        name = str(message.get("stream") or "")
        event = self.started.setdefault(name, asyncio.Event())
        event.set()
        await self.release.wait()


class ControlConcurrencyTests(unittest.TestCase):
    def test_long_rpc_stream_does_not_block_second_open(self):
        async def scenario():
            mesh = FakeMesh()
            first = wqpu_public_security._dispatch_open(
                mesh, {"type": "open", "service": "rpc", "stream": "one"}, {"relay": True}
            )
            await asyncio.wait_for(mesh.started["one"].wait(), 1.0)

            second = wqpu_public_security._dispatch_open(
                mesh, {"type": "open", "service": "rpc", "stream": "two"}, {"relay": True}
            )
            await asyncio.wait_for(mesh.started["two"].wait(), 1.0)
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            self.assertEqual(len(mesh._wqpu_control_service_tasks), 2)

            mesh.release.set()
            await asyncio.gather(first, second)
            await asyncio.sleep(0)
            self.assertEqual(len(mesh._wqpu_control_service_tasks), 0)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
