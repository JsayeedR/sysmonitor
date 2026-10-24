# Expand-only schema; no live data backfill, no deletion, no SMTP side effects.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('shiftreport', '0013_stage2_detach_report_users')]

    operations = [
        migrations.AddField(
            model_name='shiftreport',
            name='sent_signature_html',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='mnocpfereport',
            name='sent_signature_html',
            field=models.TextField(blank=True, default=''),
        ),
    ]
