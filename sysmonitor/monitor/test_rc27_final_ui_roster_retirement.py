"""RC27 UI/roster/off-day/retirement follow-up safety regressions."""
from datetime import date
from django.contrib.auth.models import User
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from .models import DutyRoster, DutyRosterAssignment, UserProfile, ActivityLog
from .roster_service import rolling_duty_days


class RosterShortNameTests(TestCase):
    def test_off_leave_preserved_and_short_names(self):
        day = date(2026, 10, 30)
        roster = DutyRoster.objects.create(month=day.replace(day=1))
        for name, code in [('Engr. Md. Nafis Fuad (2134)', 'M'),
                           ('Sakhawat Hossain', 'O'),
                           ('Md. Rezwan Hasan Pramanik', 'CL'),
                           ('Abu Raihan Siddique', 'G')]:
            DutyRosterAssignment.objects.create(roster=roster,duty_date=day,
                engineer_name=name,duty_code=code,source_row=8)
        result = rolling_duty_days(start_date=day,days=10)[0]
        self.assertEqual(result['shifts'][0]['short_display'],'Nafis')
        self.assertEqual(result['shifts'][3]['short_display'],'Raihan')
        self.assertEqual(result['off_display'],'Sakhawat')
        self.assertEqual(result['leave'][0]['name'],'Rezwan')
        self.assertEqual(result['leave'][0]['code'],'CL')

    def test_duplicate_first_names_stay_unambiguous(self):
        day=date(2026,10,30); roster=DutyRoster.objects.create(month=day.replace(day=1))
        for name, code in [('Md. Rahim Uddin','M'),('Rahim Hossain','E')]:
            DutyRosterAssignment.objects.create(roster=roster,duty_date=day,
                engineer_name=name,duty_code=code,source_row=8)
        slots=rolling_duty_days(start_date=day,days=10)[0]['shifts']
        self.assertNotEqual(slots[0]['short_display'],slots[1]['short_display'])


class SafeAccountRetirementTests(TestCase):
    def setUp(self):
        self.root=User.objects.create_superuser('root-safe','root@example.test','dummy-test')
        self.root2=User.objects.create_superuser('root-other','root2@example.test','dummy-test')
        self.admin=User.objects.create_user('role-admin',password='dummy-test')
        UserProfile.objects.create(user=self.admin, role='admin')
        self.member=User.objects.create_user('shift-engineer',password='dummy-test')
        UserProfile.objects.create(user=self.member,role='user')
        ActivityLog.objects.create(user=self.member,action='LOGIN',detail='synthetic')

    def test_normal_admin_can_never_retire_accounts(self):
        self.client.force_login(self.admin)
        url=reverse('user_delete',args=[self.member.pk])
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.post(url,{'confirm_username':self.member.username}).status_code,403)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_active)

    def test_retirement_preserves_user_id_and_audit_history(self):
        self.client.force_login(self.root)
        url=reverse('user_delete',args=[self.member.pk]); ident=self.member.pk
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.post(url,{}).status_code,302)
        self.member.refresh_from_db();self.assertTrue(self.member.is_active)
        self.assertEqual(self.client.post(url,{'confirm_username':self.member.username}).status_code,302)
        self.member.refresh_from_db()
        self.assertEqual(self.member.pk,ident)
        self.assertFalse(self.member.is_active)
        self.assertFalse(self.member.has_usable_password())
        self.assertEqual(ActivityLog.objects.filter(user=self.member,action='LOGIN').count(),1)

    def test_superuser_may_retire_another_superuser_but_never_self(self):
        self.client.force_login(self.root)
        self.assertEqual(self.client.post(reverse('user_delete',args=[self.root.pk]),
               {'confirm_username':self.root.username}).status_code,403)
        self.assertEqual(self.client.post(reverse('user_delete',args=[self.root2.pk]),
               {'confirm_username':self.root2.username}).status_code,302)
        self.root2.refresh_from_db()
        self.assertFalse(self.root2.is_active)
        self.assertFalse(self.root2.is_superuser)
        self.assertFalse(self.root2.is_staff)

    def test_commands_not_in_global_navigation(self):
        self.client.force_login(self.root)
        page=self.client.get('/users/')
        self.assertEqual(page.status_code,200)
        self.assertNotContains(page,'/manual/#admin-cli')
        self.assertContains(page,'Retire access')
        self.client.force_login(self.admin)
        page=self.client.get('/users/')
        self.assertNotContains(page,'Retire access')

    def test_roster_layout_shows_off_and_no_per_cell_time(self):
        self.client.force_login(self.admin)
        for url in ('/duty-roster/','/duty-schedule/'):
            if url == '/duty-schedule/':
                self.client.force_login(self.member)
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'Off / Leave')
            self.assertNotContains(response,'<span class="shiftmeta">')

class SharedNavSourceTests(SimpleTestCase):
    def test_shared_nav_palette_is_explicit(self):
        from pathlib import Path
        from django.conf import settings
        p=Path(settings.BASE_DIR)/'monitor/templates/monitor/navbar.html'
        text=p.read_text()
        self.assertIn('id="sysmonitor-main-navigation"',text)
        self.assertIn('background: #172033 !important',text)
        self.assertNotIn('href="{{ base_path }}/manual/#admin-cli"',text)
