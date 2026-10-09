from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('shiftreport', '0006_historicalreportrevision')]
    operations = [migrations.AddField(
        model_name='shiftreport',
        name='auto_send_after_shift',
        field=models.BooleanField(default=False),
    )]
