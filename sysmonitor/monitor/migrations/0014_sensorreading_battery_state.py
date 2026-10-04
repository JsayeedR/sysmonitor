from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0013_messagetemplate_notificationrecipient_monthly_report_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='sensorreading',
            name='battery_state',
            field=models.CharField(blank=True, max_length=20),
        ),
    ]
