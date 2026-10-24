# RC8 expand-only relation snapshots. No existing user FKs are altered.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('shiftreport', '0011_optionalreportattachment'),
        ('monitor', '0032_stage2_actor_links'),
    ]

    operations = [
        migrations.AddField(
            model_name='shifthandovercontact',
            name='contact_snapshot',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='handover_contacts', to='monitor.historicalactor'),
        ),
        migrations.AddField(
            model_name='shiftreport',
            name='preparer_snapshot',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='prepared_shift_reports', to='monitor.historicalactor'),
        ),
        migrations.AddField(
            model_name='historicalreportrevision',
            name='owner_snapshot',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='owned_historical_revisions', to='monitor.historicalactor'),
        ),
        migrations.AddField(
            model_name='historicalreportrevision',
            name='creator_snapshot',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='created_historical_revisions', to='monitor.historicalactor'),
        ),
        migrations.AddField(
            model_name='shiftimportantissue',
            name='creator_snapshot',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='important_issues', to='monitor.historicalactor'),
        ),
        migrations.AddField(
            model_name='mnocpfereport',
            name='preparer_snapshot',
            field=models.ForeignKey(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='prepared_pfe_reports', to='monitor.historicalactor'),
        ),
    ]
