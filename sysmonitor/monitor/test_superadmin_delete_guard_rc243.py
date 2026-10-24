"""RC24.3 fail-closed deletion-route authorization; no account removed."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from .models import UserProfile

class SuperadminDeletionGuardTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_user('ordinary-admin-rc243',password='synthetic-password')
        UserProfile.objects.create(user=self.admin,role='admin')
        self.root=User.objects.create_superuser('test-root-rc243','synthetic@example.org','synthetic-password')
        self.person=User.objects.create_user('ordinary-rc243',password='synthetic-password')
        UserProfile.objects.create(user=self.person,role='user')

    def test_normal_admin_cannot_enter_deletion_endpoint(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('user_delete',args=[self.person.pk])).status_code,403)
        self.assertTrue(User.objects.filter(pk=self.person.pk).exists())

    def test_superadmin_endpoint_does_not_actually_delete_until_approved(self):
        self.client.force_login(self.root)
        self.assertEqual(self.client.post(reverse('user_delete',args=[self.person.pk])).status_code,302)
        self.assertTrue(User.objects.filter(pk=self.person.pk).exists())
