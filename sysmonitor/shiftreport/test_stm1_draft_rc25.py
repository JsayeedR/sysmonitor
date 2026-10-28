"""Regression: saving / reopening a draft may not turn STM-1 7 into 9."""
import re
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from .services import (bandwidth_from_post, format_circuit_bandwidth,
                       parse_circuit_counts)


class STMDraftPersistenceTests(SimpleTestCase):
    @patch('shiftreport.services.wet_circuit_limits', return_value=(20, 77, 1, 11))
    def test_new_format_draft_round_trip(self, _):
        text = format_circuit_bandwidth((19, 51, 1, 7))
        self.assertIn('STM-1                : 7/11', text)
        self.assertEqual(parse_circuit_counts(text), (19, 51, 1, 7))
        # All four fields are what the browser submits on Save Draft.
        posted = {'circuit_100gbe': '19', 'circuit_10ge': '51',
                  'circuit_stm16': '1', 'circuit_stm1': '7',
                  'bandwidth_status': text}
        saved = bandwidth_from_post(posted, text)
        self.assertIn('STM-1                : 7/11', saved)
        self.assertEqual(parse_circuit_counts(saved), (19, 51, 1, 7))

    @patch('shiftreport.services.wet_circuit_limits', return_value=(20, 77, 1, 11))
    def test_no_silent_nine_on_incomplete_new_block(self, _):
        text = format_circuit_bandwidth((19, 51, 1, 7))
        damaged = text.replace('STM-1                : 7/11',
                               'STM-1                : ?/11')
        with self.assertRaisesRegex(ValueError, 'STM-1'):
            parse_circuit_counts(damaged)

    def test_ui_templates_do_not_restore_legacy_nine(self):
        directory = Path(__file__).parent / 'templates' / 'shiftreport'
        for name in ('home.html', 'edit.html'):
            with self.subTest(name=name):
                text = (directory / name).read_text(encoding='utf-8')
                self.assertNotIn('const defaults=[19,51,1,9]', text)
                self.assertIn('const matches=readCounts(text);', text)
                self.assertIn('STM-1', text)
                self.assertIn('found.length===1', text)
                # Over-capacity clearing is intentional; it is not an STM-1 default.
                self.assertEqual(text.count("area.value='';return;"), 1)
                self.assertIn("Capacity exceeds total installed capacity.", text)
