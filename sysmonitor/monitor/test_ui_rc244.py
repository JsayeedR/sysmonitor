"""RC24.4 UI checks — isolated DB tests only, no production mutations."""
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, SimpleTestCase
from django.urls import reverse

from monitor.models import UserProfile
from monitor.page_access import PAGE_DEFINITIONS, pages_for_role, user_can_access_page


class _Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_starttag(self, tag, attrs):
        if tag == 'input':
            data = dict(attrs)
            if data.get('name') == 'visible_pages':
                self.items.append(data)


class PageAccessCheckboxTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='rc244-operator', email='operator@example.test', password='TemporaryTestPassword!1'
        )
        UserProfile.objects.create(user=self.admin, role='admin')
        self.operator = User.objects.create_user(
            username='rc244-shift-user', password='TemporaryTestPassword!1'
        )
        UserProfile.objects.create(user=self.operator, role='user')
        self.client.force_login(self.admin)
        self.url = reverse('page_access_manage')

    def inputs(self):
        response = self.client.get(f'{self.url}?user={self.operator.pk}')
        self.assertEqual(response.status_code, 200)
        parser = _Inputs()
        parser.feed(response.content.decode())
        return parser.items

    def test_all_registered_pages_have_real_checkboxes(self):
        inputs = self.inputs()
        self.assertEqual(len(inputs), len(PAGE_DEFINITIONS))
        self.assertEqual({v['value'] for v in inputs}, set(PAGE_DEFINITIONS))
        self.assertTrue(all(v.get('type') == 'checkbox' for v in inputs))
        eligible = pages_for_role('user')
        for item in inputs:
            key = item['value']
            cannot_toggle = key not in eligible or PAGE_DEFINITIONS[key].get('locked')
            self.assertEqual('disabled' in item, bool(cannot_toggle), key)

    def test_uncheck_and_recheck_changes_only_selected_user(self):
        allowed = {k for k, v in pages_for_role('user').items() if not v.get('locked')}
        key = 'generator_manual_cycle'
        self.assertIn(key, allowed)
        response = self.client.post(self.url, {
            'user_id': self.operator.pk,
            'visible_pages': sorted(allowed - {key}),
        })
        self.assertEqual(response.status_code, 302)
        self.operator.userprofile.refresh_from_db()
        self.assertEqual(self.operator.userprofile.hidden_pages, [key])
        self.assertFalse(user_can_access_page(self.operator, key))
        self.assertEqual(self.admin.userprofile.hidden_pages or [], [])

        response = self.client.post(self.url, {
            'user_id': self.operator.pk,
            'visible_pages': sorted(allowed),
        })
        self.assertEqual(response.status_code, 302)
        self.operator.userprofile.refresh_from_db()
        self.assertEqual(self.operator.userprofile.hidden_pages, [])
        self.assertTrue(user_can_access_page(self.operator, key))

    def test_cannot_make_role_ineligible_page_available(self):
        self.assertNotIn('page_access', pages_for_role('user'))
        response = self.client.post(self.url, {
            'user_id': self.operator.pk,
            'visible_pages': sorted(set(pages_for_role('user')) | {'page_access', 'users'}),
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(user_can_access_page(self.operator, 'page_access'))
        self.assertFalse(user_can_access_page(self.operator, 'users'))


class UiSourceOnlyTests(SimpleTestCase):
    def source(self, relative):
        return (Path(settings.BASE_DIR) / relative).read_text(encoding='utf-8')

    def test_shift_source_control_is_readable_in_both_editors(self):
        for path in (
            'shiftreport/templates/shiftreport/home.html',
            'shiftreport/templates/shiftreport/edit.html',
        ):
            with self.subTest(path=path):
                doc = self.source(path)
                self.assertIn('id="smActivitySourceMode"', doc)
                self.assertIn('color:#0f172a !important', doc)
                self.assertIn('background:#ffffff !important', doc)
                self.assertIn('color-scheme:light', doc)
                self.assertIn('smActivitySourceLoad', doc)
                self.assertNotIn('color:inherit;background:#1e293b', doc)

    def test_outage_remarks_moved_to_manual_cycle_page(self):
        nav = self.source('monitor/templates/monitor/navbar.html')
        audit = self.source('monitor/templates/monitor/generator_cycle_audit.html')
        self.assertNotIn('href="{{ base_path }}/outage-cycle-remarks/"', nav)
        self.assertIn('href="{{ base_path }}/outage-cycle-remarks/"', audit)
        self.assertIn('{% if "outage_cycle_remarks" not in page_hidden %}', audit)
        self.assertEqual(PAGE_DEFINITIONS['outage_cycle_remarks']['group'], 'Generator Data')
        self.assertEqual(PAGE_DEFINITIONS['outage_cycle_remarks']['roles'], ('user','admin'))
        context = self.source('monitor/context_processors.py')
        self.assertNotIn("        'outage_cycle_remarks',", context)

    def test_indicators_keep_existing_api_mapping_and_accessibility(self):
        dash = self.source('monitor/templates/monitor/dashboard.html')
        for label, lamp, txt in (
            ('PDB','sm-pdb-light','sm-pdb-text'),
            ('Generator','sm-gen-light','sm-gen-text'),
            ('Equipment','sm-nvr-light','sm-nvr-text'),
        ):
            self.assertIn('data-light-label="'+label+'"', dash)
            self.assertIn('id="'+lamp+'"', dash)
            self.assertIn('id="'+txt+'"', dash)
        self.assertIn('width:37px; height:37px', dash)
        self.assertIn('class="sm-visually-hidden"', dash)
        self.assertIn("assignLight('sm-pdb-light','sm-pdb-text',p.pdb,'UP','pdb-up')", dash)
        self.assertIn("assignLight('sm-gen-light','sm-gen-text',p.generator,'ON','gen-on')", dash)
        self.assertIn("assignLight('sm-nvr-light','sm-nvr-text',p.equipment,'UP','nvr-up')", dash)
        self.assertIn('role="img"', dash)
        self.assertIn('Unknown', dash)
