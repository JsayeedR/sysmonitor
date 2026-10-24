from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('shiftreport', '0014_stage2_report_signatures')]
    operations = [
        migrations.AddField(
            model_name='shiftreportactivity', name='source_reference',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
