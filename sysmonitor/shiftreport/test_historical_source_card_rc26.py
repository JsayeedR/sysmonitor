"""RC26 historical compact source card: accurate source-vs-draft metadata."""
from datetime import date, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from monitor.models import UserProfile
from .models import HistoricalReportRevision, ShiftHandoverContact, ShiftReport


class HistoricalCardMarkupTests(SimpleTestCase):
    def test_collapsed_source_card_and_distinct_draft_heading(self):
        html = (Path(__file__).parent / 'templates' / 'shiftreport' /
                'historical.html').read_text(encoding='utf-8')
        self.assertIn('id="smHistoricalSourceDetails" class="sm-source-details" hidden', html)
        self.assertIn('id="smHistoricalSourceToggle"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('id="smHistoricalDisplayedRows"', html)
        self.assertIn('id="smHistoricalLoadedContext"', html)
        self.assertIn('My Historical Draft', html)
        self.assertIn('UNSAVED changes to your draft', html)
        self.assertIn('MutationObserver(updateCount)', html)
        self.assertIn('data-shift-date=', html)
        self.assertIn('data-shift-time=', html)
        self.assertIn('Last updated (BDT)', html)
        self.assertIn('SENT time (BDT)', html)


class HistoricalCardMetadataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.viewer = User.objects.create_user('history_card_viewer', password='synthetic')
        cls.officer = User.objects.create_user('history_card_officer', email='officer@example.test', password='synthetic')
        for user in (cls.viewer, cls.officer):
            UserProfile.objects.create(user=user, role='user')
        cls.contact = ShiftHandoverContact.objects.create(
            name='Duty Officer', email='officer@example.test', is_active=True,
        )
        now = timezone.now()
        cls.report = ShiftReport.objects.create(
            prepared_by=cls.officer,
            handover_to=cls.contact,
            report_date=date(2026, 10, 27),
            shift='EVENING',
            shift_start=now - timedelta(hours=8),
            shift_end=now - timedelta(hours=4),
            status='SENT',
        )
        cls.sent = HistoricalReportRevision.objects.create(
            owner=cls.officer, created_by=cls.officer,
            revision_number=1, status='SENT', shift_report=cls.report,
            sent_at=now-timedelta(hours=3),
            rows=[dict(category_1='Network', details='SENT only')],
        )
        cls.private_draft = HistoricalReportRevision.objects.create(
            owner=cls.officer, created_by=cls.officer,
            revision_number=2, status='DRAFT',
            rows=[dict(details='PRIVATE DRAFT')],
        )

    def test_rendered_source_card_metadata_read_only(self):
        from .historical_sources import historical_source_choices
        choices = historical_source_choices(self.viewer)
        source = next(item for item in choices if item['key'] == f'contact:{self.contact.pk}')
        self.assertEqual(source['report_date'], date(2026,10,27))
        self.assertEqual(source['shift_label'], 'Evening Shift')
        self.assertEqual(source['entry_count'], 1)
        self.assertEqual(source['revision_number'], 1)
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('shiftreport:historical_home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-shift-date="27 Oct 2026"')
        self.assertContains(response, 'Duty Officer')
        self.assertContains(response, 'data-entries="1"')
        self.assertContains(response, 'My Historical Draft')
        self.private_draft.refresh_from_db()
        self.assertEqual(self.private_draft.rows[0]['details'], 'PRIVATE DRAFT')

    def test_source_json_returns_provenance_without_draft(self):
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse('shiftreport:historical_source_preview'),
            {'source':f'contact:{self.contact.pk}'},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['shift_date'], '27 Oct 2026')
        self.assertEqual(data['shift_name'], 'Evening Shift')
        self.assertIn('–', data['shift_time'])
        self.assertEqual(len(data['rows']), 1)
        self.assertEqual(data['rows'][0]['details'], 'SENT only')
        self.assertNotIn('PRIVATE DRAFT', str(data))
        self.assertIn('no-store', response['Cache-Control'])

    def test_no_link_is_not_guessed(self):
        from .historical_sources import sent_source_metadata
        metadata = sent_source_metadata(self.sent)
        self.assertEqual(metadata['shift_label'], 'Evening Shift')
        self.sent.shift_report = None  # local in-memory only; never save SENT revision
        self.sent.shift_report_id = None
        missing = sent_source_metadata(self.sent)
        self.assertIsNone(missing['report_date'])
        self.assertEqual(missing['shift_label'], 'Not linked')
        self.assertEqual(missing['shift_time'], 'Not linked')
