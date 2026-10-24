# RC8 expand-only relation snapshots. No existing user FKs are altered.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('monitor', '0031_stage2_identity_archive'),
    ]

    operations = [
        migrations.AddField(
            model_name='activitylog',
            name='actor_snapshot',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='activity_logs', to='monitor.historicalactor'),
        ),
        migrations.AddField(
            model_name='profilechangerequest',
            name='requester_snapshot',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='profile_change_requests', to='monitor.historicalactor'),
        ),
        migrations.AddField(
            model_name='notificationrecipient',
            name='account_snapshot',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='notification_recipients', to='monitor.historicalactor'),
        ),
        migrations.AddField(
            model_name='notificationrequest',
            name='requester_snapshot',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='notification_requests', to='monitor.historicalactor'),
        ),
        migrations.AddField(
            model_name='dutyroster',
            name='importer_snapshot',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='duty_rosters', to='monitor.historicalactor'),
        ),
        migrations.AddField(
            model_name='outagecycleremark',
            name='author_snapshot',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='outage_remarks', to='monitor.historicalactor'),
        ),
    ]
