# Stage 2 rehearsal-only schema: remove cascading login-account ties for history.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('monitor', '0032_stage2_actor_links'),
    ]

    operations = [
        migrations.AlterField(
            model_name='profilechangerequest', name='user',
            field=models.ForeignKey('auth.User', on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True, related_name='change_requests'),
        ),
        migrations.AlterField(
            model_name='notificationrequest', name='user',
            field=models.ForeignKey('auth.User', on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True, related_name='notification_requests'),
        ),
        migrations.AlterField(
            model_name='outagecycleremark', name='author',
            field=models.ForeignKey('auth.User', on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True, related_name='outage_remarks'),
        ),
        migrations.AddConstraint(
            model_name='outagecycleremark',
            constraint=models.UniqueConstraint(
                fields=('cycle','author_snapshot'),
                condition=models.Q(author__isnull=True, author_snapshot__isnull=False),
                name='uniq_cycle_archive_remark',
            ),
        ),
    ]
