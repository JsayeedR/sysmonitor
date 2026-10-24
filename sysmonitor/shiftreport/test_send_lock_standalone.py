"""Run without Django: python -m unittest shiftreport.test_send_lock_standalone -v"""
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from shiftreport.send_lock import exclusive_shift_delivery, ShiftDeliveryBusy


def attempt_same_lock(folder, queue):
    try:
        with exclusive_shift_delivery(12, lock_dir=folder):
            queue.put('unexpected-acquisition')
    except ShiftDeliveryBusy:
        queue.put('blocked')


class ShiftDeliveryLockTests(unittest.TestCase):
    def test_contention_blocks_second_process(self):
        with tempfile.TemporaryDirectory() as d:
            with exclusive_shift_delivery(12, lock_dir=d):
                q = multiprocessing.Queue()
                p = multiprocessing.Process(target=attempt_same_lock, args=(d, q))
                p.start()
                p.join(5)
                self.assertEqual(p.exitcode, 0)
                self.assertEqual(q.get(timeout=3), 'blocked')
            with exclusive_shift_delivery(12, lock_dir=d):
                self.assertTrue(Path(d, 'shift-12.lock').is_file())

    def test_distinct_reports_do_not_contend(self):
        with tempfile.TemporaryDirectory() as d:
            with exclusive_shift_delivery(12, lock_dir=d):
                with exclusive_shift_delivery(13, lock_dir=d):
                    pass

    def test_invalid_id_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                with exclusive_shift_delivery(-1, lock_dir=d):
                    pass
