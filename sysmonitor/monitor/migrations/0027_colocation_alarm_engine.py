from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('monitor', '0026_alter_activitylog_action')]
    operations = [
        migrations.AddField(model_name='sensoralarmconfig', name='alarm_confirmation_readings',
                            field=models.PositiveSmallIntegerField(default=2, help_text='Consecutive valid readings required for an alarm or recovery (1–10).')),
        migrations.CreateModel(name='SensorAlarmState', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('device_id', models.CharField(max_length=64)),
            ('condition', models.CharField(max_length=32)),
            ('active', models.BooleanField(default=False)),
            ('candidate_count', models.PositiveSmallIntegerField(default=0)),
            ('recovery_count', models.PositiveSmallIntegerField(default=0)),
            ('last_sent_at', models.DateTimeField(blank=True, null=True)),
            ('updated_at', models.DateTimeField(auto_now=True))]),
        migrations.AddConstraint(model_name='sensoralarmstate', constraint=models.UniqueConstraint(fields=['device_id', 'condition'], name='sensor_alarm_device_condition_unique')),
    ]
