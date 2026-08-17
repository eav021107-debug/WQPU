import unittest

import wqpu_meter


def frame(command, payload=b""):
    return bytes([int(command)]) + len(payload).to_bytes(8, "little") + payload


class UsageBookMultiStreamTests(unittest.TestCase):
    def test_independent_tcp_streams_never_share_parser_buffer(self):
        book = wqpu_meter.UsageBook()
        first = book.meter("worker")
        second = book.meter("worker")
        self.assertIsNot(first, second)

        a = frame(wqpu_meter.RPC_CMD_SET_TENSOR, b"a" * 20)
        b = frame(wqpu_meter.RPC_CMD_SET_TENSOR, b"b" * 30)

        # Deliberately interleave physical stream progress. A shared parser would append
        # stream B inside stream A's incomplete frame and synthesize corrupt RPC bytes.
        first.feed(a[:4])
        second.feed(b)
        first.feed(a[4:])

        stats = book.snapshot()["worker"]
        self.assertEqual(stats["requests"], 2)
        self.assertEqual(stats["tensor_upload_bytes"], 50)
        self.assertEqual(stats["invalid_frames"], 0)
        self.assertEqual(stats["trailing_bytes"], 0)

    def test_incomplete_physical_stream_poison_aggregate_fail_closed(self):
        book = wqpu_meter.UsageBook()
        clean = book.meter("worker")
        partial = book.meter("worker")
        clean.feed(frame(wqpu_meter.RPC_CMD_SET_TENSOR, b"ok"))
        partial.feed(frame(wqpu_meter.RPC_CMD_SET_TENSOR, b"partial")[:5])

        stats = book.snapshot()["worker"]
        self.assertEqual(stats["requests"], 1)
        self.assertGreater(stats["trailing_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
