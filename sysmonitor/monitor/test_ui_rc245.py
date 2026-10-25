"""RC24.5 scoped regression tests; default Django test DB only."""
from pathlib import Path
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from monitor.models import UserProfile
from monitor.page_access import PAGE_DEFINITIONS, pages_for_role, user_can_access_page

class SuperadminPageAccessTests(TestCase):
    def setUp(self):
        self.superadmin=User.objects.create_superuser(
            username='rc245-root', email='root@example.test', password='TestOnly1234!')
        UserProfile.objects.create(user=self.superadmin,role='admin')
        self.admin=User.objects.create_user(username='rc245-admin',password='TestOnly1234!')
        UserProfile.objects.create(user=self.admin,role='admin')
        self.client.force_login(self.superadmin)
        self.url=reverse('page_access_manage')

    def test_superadmin_can_hide_and_restore_admin_page_access(self):
        html=self.client.get(f'{self.url}?user={self.admin.pk}').content.decode()
        self.assertIn('Superadmin-controlled for Admin accounts', html)
        base={key for key,cfg in pages_for_role('admin').items()
              if not cfg.get('locked') or key=='page_access'}
        self.assertIn('page_access',base)
        r=self.client.post(self.url,{'user_id':str(self.admin.pk),
                                    'visible_pages':sorted(base-{'page_access'})})
        self.assertEqual(r.status_code,302)
        self.admin.userprofile.refresh_from_db()
        self.assertIn('page_access',self.admin.userprofile.hidden_pages)
        self.assertFalse(user_can_access_page(self.admin,'page_access'))
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.client.force_login(self.superadmin)
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertEqual(self.client.post(self.url,{'user_id':str(self.admin.pk),
                         'visible_pages':sorted(base)}).status_code,302)
        self.admin.userprofile.refresh_from_db()
        self.assertNotIn('page_access',self.admin.userprofile.hidden_pages)
        self.assertTrue(user_can_access_page(self.admin,'page_access'))

    def test_superadmin_recovery_is_never_hidden_even_in_saved_profile(self):
        self.superadmin.userprofile.hidden_pages=['page_access']
        self.superadmin.userprofile.save(update_fields=['hidden_pages'])
        self.assertTrue(user_can_access_page(self.superadmin,'page_access'))
        self.assertEqual(self.client.get(self.url).status_code,200)

    def test_normal_admin_cannot_change_another_admin_access(self):
        self.client.force_login(self.admin)
        r=self.client.post(self.url, {'user_id':self.superadmin.pk,'visible_pages':[]})
        self.assertEqual(r.status_code,302)
        self.assertTrue(user_can_access_page(self.superadmin,'page_access'))

class UiScopeTests(SimpleTestCase):
    def source(self,relative):
        return (Path(settings.BASE_DIR)/relative).read_text()

    def test_first_row_three_boxes_and_off_lamps_are_dim(self):
        html=self.source('monitor/templates/monitor/dashboard.html')
        self.assertIn('grid-template-areas:"status lamps cycle" "ats clock colo"',html)
        self.assertIn('minmax(165px,.56fr)',html)
        self.assertIn('sm-light.off',html)
        self.assertIn("?'unknown':'off'",html)
        for key in ('sm-pdb-light','sm-gen-light','sm-nvr-light'):
            self.assertIn('id="'+key+'"',html)

    def test_remarks_link_stays_in_generator_sections_not_others(self):
        nav=self.source('monitor/templates/monitor/navbar.html')
        gen=self.source('monitor/templates/monitor/generator_cycle_audit.html')
        link = 'href="{{ base_path }}/outage-cycle-remarks/"'
        generator_nav = nav.split('{% if page_show_generator %}', 1)[1].split('{% if page_show_others %}', 1)[0]
        others_and_later = nav.split('{% if page_show_others %}', 1)[1]
        self.assertIn(link, generator_nav)
        self.assertNotIn(link, others_and_later)
        self.assertEqual(nav.count(link), 1)
        self.assertIn('href="{{ base_path }}/outage-cycle-remarks/"',gen)
