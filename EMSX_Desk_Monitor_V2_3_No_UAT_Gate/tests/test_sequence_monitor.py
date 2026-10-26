"""Offline regression tests for Bloomberg EMSX event sequence health.

Synthetic event streams only: no Bloomberg connection and no ACK sends.
"""
import queue
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desk_core import DeskBook
from bloomberg_feed import BloombergWorker


class TestAllEventSequenceAccounting(unittest.TestCase):
    def test_heartbeat_in_middle_does_not_create_false_gap(self):
        book = DeskBook()
        book.ingest('O', 4, {'API_SEQ_NUM': 100, 'EMSX_SEQUENCE': 99})
        book.ingest('O', 1, {'API_SEQ_NUM': 101})
        book.ingest('O', 7, {'API_SEQ_NUM': 102, 'EMSX_SEQUENCE': 99})
        self.assertEqual(book.event_gaps, [])
        self.assertEqual(book.seq_last['O'], 102)

    def test_snapshot_end_sequence_is_tracked(self):
        book = DeskBook()
        book.ingest('R', 4, {'API_SEQ_NUM': 10, 'EMSX_SEQUENCE': 99, 'EMSX_ROUTE_ID': 1})
        book.ingest('R', 11, {'API_SEQ_NUM': 11})
        book.ingest('R', 7, {'API_SEQ_NUM': 12, 'EMSX_SEQUENCE': 99, 'EMSX_ROUTE_ID': 1})
        self.assertTrue(book.snapshot_done['R'])
        self.assertEqual(book.event_gaps, [])

    def test_true_gap_still_blocks_monitor_health(self):
        book = DeskBook()
        book.subscriptions = {'O':'STARTED','R':'STARTED'}
        book.ingest('O', 4, {'API_SEQ_NUM': 1, 'EMSX_SEQUENCE': 99})
        book.ingest('O', 1, {'API_SEQ_NUM': 2})
        book.ingest('O', 7, {'API_SEQ_NUM': 5, 'EMSX_SEQUENCE': 99})
        book.ingest('O', 11, {})
        book.ingest('R', 11, {})
        self.assertIn('2 -> 5',book.event_gaps[0])
        self.assertFalse(book.counters()['stream_ok'])

    def test_backwards_sequence_remains_blocking(self):
        book = DeskBook()
        book.ingest('O', 4, {'API_SEQ_NUM': 20, 'EMSX_SEQUENCE': 99})
        book.ingest('O', 7, {'API_SEQ_NUM': 18, 'EMSX_SEQUENCE': 99})
        self.assertEqual(len(book.event_gaps), 1)
        self.assertIn('backwards', book.event_gaps[0])

    def test_worker_is_independent_per_subscription(self):
        out = queue.Queue()
        worker = BloombergWorker('BETA','HDGB',out)
        worker._observe_sequence('O',4,1)
        worker._observe_sequence('R',4,1)
        worker._observe_sequence('O',1,2)
        worker._observe_sequence('R',11,2)
        worker._observe_sequence('O',7,3)
        self.assertFalse(worker.feed_error)
        self.assertEqual(worker.seq_last, {'O':3,'R':2})

    def test_worker_real_discontinuity_fails_closed(self):
        out = queue.Queue()
        worker = BloombergWorker('BETA','HDGB',out)
        worker._observe_sequence('O',4,10)
        worker._observe_sequence('O',1,11)
        worker._observe_sequence('O',7,14)
        self.assertTrue(worker.feed_error)
        reported = out.get_nowait()
        self.assertEqual(reported['type'], 'error')
        self.assertIn('11->14', reported['message'])
        self.assertIn('EVENT_STATUS=7', reported['message'])


if __name__ == '__main__':
    unittest.main()
