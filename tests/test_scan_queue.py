from __future__ import annotations

import unittest

from ui.scan_queue import ScanQueue


class ScanQueueTestCase(unittest.TestCase):
    def test_two_reads_are_taken_once_and_in_order(self) -> None:
        queue = ScanQueue()
        first = queue.enqueue("7891234567895")
        second = queue.enqueue("7891234567888")

        self.assertEqual(queue.take_next(), first)
        self.assertIsNone(queue.take_next())
        self.assertTrue(queue.finish(first))
        self.assertEqual(queue.take_next(), second)
        self.assertTrue(queue.finish(second))
        self.assertFalse(queue.has_pending)

    def test_error_finish_allows_the_next_read(self) -> None:
        queue = ScanQueue()
        failed = queue.enqueue("7891234567895")
        recovered = queue.enqueue("7891234567888")
        self.assertEqual(queue.take_next(), failed)
        self.assertTrue(queue.finish(failed))
        self.assertEqual(queue.take_next(), recovered)

    def test_old_generation_result_is_discarded(self) -> None:
        queue = ScanQueue()
        stale = queue.enqueue("7891234567895")
        self.assertEqual(queue.take_next(), stale)
        queue.advance_generation()

        self.assertFalse(queue.finish(stale))
        fresh = queue.enqueue("7891234567888")
        self.assertEqual(queue.take_next(), fresh)
        self.assertTrue(queue.finish(fresh))

    def test_queue_has_a_bounded_pending_size(self) -> None:
        queue = ScanQueue(max_pending=2)
        queue.enqueue("1")
        queue.enqueue("2")
        with self.assertRaises(OverflowError):
            queue.enqueue("3")


if __name__ == "__main__":
    unittest.main()
