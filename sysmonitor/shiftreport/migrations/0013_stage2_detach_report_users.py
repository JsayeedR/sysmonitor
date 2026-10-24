# Stage 2 rehearsal-only schema. UI user deletion remains disabled.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('shiftreport', '0012_stage2_actor_links'),
        ('monitor', '0033_stage2_detach_nonreport_users'),
    ]

    operations = [
        migrations.AlterField(
            model_name='shiftreport', name='prepared_by',
            field=models.ForeignKey('auth.User', on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True, related_name='prepared_shift_reports'),
        ),
        migrations.AlterField(
            model_name='historicalreportrevision', name='owner',
            field=models.ForeignKey('auth.User', on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True, related_name='personal_historical_revisions'),
        ),
        migrations.AlterField(
            model_name='mnocpfereport', name='prepared_by',
            field=models.ForeignKey('auth.User', on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True, related_name='prepared_mnoc_pfe_reports'),
        ),
        migrations.AddConstraint(
            model_name='historicalreportrevision',
            constraint=models.UniqueConstraint(
                fields=('owner_snapshot','revision_number'),
                condition=models.Q(owner__isnull=True, owner_snapshot__isnull=False),
                name='uniq_archived_personal_hist_version',
            ),
        ),
    ]
