import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from monitor.nvr_critical import tick

class NVRCriticalTest(unittest.TestCase):
    def test_alarm_repeats_recovers_and_restarts(self):
        start = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
        events = []
        def capture(kind, now, down_since, holder_up, mode):
            events.append((kind, int((now-start).total_seconds()), holder_up))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.json'
            def call(mins, up=False, holder=True):
                return tick(up, holder, now=start+timedelta(minutes=mins),
                            state_path=path, mode='private', deliver=capture)
            self.assertEqual(call(0), 'started')
            self.assertEqual(call(4), 'none')
            self.assertEqual(call(5,holder=False), 'critical')
            self.assertEqual(call(6), 'none')
            self.assertEqual(call(7), 'critical')
            self.assertEqual(call(9), 'critical')
            self.assertEqual(call(10,up=True), 'recovery')
            self.assertEqual(call(11,up=True), 'none')
            self.assertEqual([e[0] for e in events], ['critical']*3+['recovery'])
            self.assertEqual([e[1] for e in events], [300,420,540,600])
            self.assertEqual(call(12), 'started')
            self.assertEqual(call(17), 'critical')
    def test_off_is_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.json'
            self.assertEqual(tick(False, True, state_path=path, mode='off'), 'disabled')
            self.assertFalse(path.exists())

if __name__=='__main__':
    unittest.main()
