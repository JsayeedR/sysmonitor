"""Verify administrator page inventory and compact directory without writes."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from .models import UserProfile
from .page_access import PAGE_DEFINITIONS, user_can_access_page


class ManagementImprovementsTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_superuser('admin_rc25', 'a@example.org', 'TempTest!1')
        UserProfile.objects.create(user=self.admin, role='admin')
        self.viewer=User.objects.create_user('viewer_rc25', password='TempTest!1')
        UserProfile.objects.create(user=self.viewer,role='viewer')
        self.client.force_login(self.admin)

    def test_page_inventory_includes_recovery_and_shift_configuration(self):
        response=self.client.get(reverse('page_access_manage')+'?user='+str(self.admin.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Page Access (recovery)')
        self.assertContains(response, 'Shift Report Configuration')
        self.assertContains(response, 'About Us')
        self.assertContains(response, 'Historical Shift Report')
        self.assertContains(response, 'Login, Logout &amp; Password Recovery')
        self.assertContains(response, 'Registered pages:')
        self.assertTrue(PAGE_DEFINITIONS['page_access']['locked'])

    def test_viewer_sees_denied_pages_in_inventory_without_grant(self):
        response=self.client.get(reverse('page_access_manage')+'?user='+str(self.viewer.pk))
        self.assertContains(response, 'Shift Report Configuration')
        self.assertContains(response, 'Not available to this role')
        self.assertFalse(user_can_access_page(self.viewer,'shift_report_config'))
        self.assertFalse(user_can_access_page(self.viewer,'page_access'))

    def test_user_table_is_compact_and_filterable(self):
        response=self.client.get(reverse('user_list'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'accountSearch')
        self.assertContains(response,'accountRole')
        self.assertContains(response,'accountRows')
        self.assertContains(response,'User Details')
        self.assertNotContains(response,'Total Usage (Start From 02 AUG 26)')

    def test_page_access_recovery_route_stays_available(self):
        self.admin.userprofile.hidden_pages=['page_access','about']
        self.admin.userprofile.save(update_fields=['hidden_pages'])
        self.assertTrue(user_can_access_page(self.admin,'page_access'))
        self.assertTrue(user_can_access_page(self.admin,'about'))
