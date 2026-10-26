"""RC25 fixed-text bandwidth display and immutable delivery tests."""
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from .services import (CIRCUIT_PENDING_SUBMISSION, format_circuit_bandwidth,
                       is_new_circuit_block, parse_circuit_counts)
from .reporting import _circuit_status_html


class CircuitStatusFormattingTests(SimpleTestCase):
    @patch('shiftreport.services.wet_circuit_limits', return_value=(20, 77, 1, 11))
    def test_exact_capacity_and_circuit_display(self, _):
        text = format_circuit_bandwidth((19, 51, 1, 7))
        self.assertIn('Used Capacity        : 2,413.58 Gbps (51.90%)', text)
        self.assertIn('Assigned Capacity    : 2,774.20 Gbps (59.66%)', text)
        self.assertIn('Assigned, No Traffic :   360.62 Gbps (7.76%)', text)
        self.assertIn('Unassigned Capacity  : 1,875.80 Gbps (40.34%)', text)
        self.assertIn('10GbE (LAN PHY)      : 51/77', text)
        self.assertIn('Total Wet-Activated  : 109', text)
        self.assertIn('Carrying Traffic     : 78', text)
        self.assertIn('Not Carrying Traffic : 31', text)
        self.assertIn(CIRCUIT_PENDING_SUBMISSION, text)
        self.assertTrue(is_new_circuit_block(text))
        self.assertEqual(parse_circuit_counts(text), (19, 51, 1, 7))

    @patch('shiftreport.services.wet_circuit_limits', return_value=(20, 77, 1, 11))
    def test_submission_stamp_in_bdt_and_preserved_html(self, _):
        submitted = timezone.make_aware(datetime(2026, 10, 26, 1, 15))
        text = format_circuit_bandwidth((19, 51, 1, 7), submitted_at=submitted)
        self.assertIn('Report Date & Time: 26/10/2026 01:15:00 AM BDT', text)
        self.assertNotIn(CIRCUIT_PENDING_SUBMISSION, text)
        self.assertIn('<pre', _circuit_status_html(text))

    def test_legacy_sent_content_not_reformatted(self):
        old = '100G: 19\n10G: 51\nSTM-16: 1\nSTM-1: 7'
        self.assertFalse(is_new_circuit_block(old))
        self.assertEqual(_circuit_status_html(old), old.replace('\n', '<br>'))
        self.assertEqual(parse_circuit_counts(old), (19, 51, 1, 7))
