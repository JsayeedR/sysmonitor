from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('monitor', '0027_colocation_alarm_engine'),
    ]

    operations = [
        migrations.CreateModel(
            name='DutyRoster',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('month', models.DateField(unique=True)),
                ('source_filename', models.CharField(blank=True, max_length=200)),
                ('imported_at', models.DateTimeField(auto_now=True)),
                ('import_warnings', models.JSONField(blank=True, default=list)),
                ('imported_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='duty_rosters_imported', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-month'],
            },
        ),
        migrations.CreateModel(
            name='DutyRosterAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('duty_date', models.DateField()),
                ('engineer_name', models.CharField(max_length=150)),
                ('duty_code', models.CharField(max_length=12)),
                ('source_row', models.PositiveIntegerField(default=0)),
                ('roster', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assignments', to='monitor.dutyroster')),
            ],
            options={
                'ordering': ['duty_date', 'duty_code', 'engineer_name'],
            },
        ),
        migrations.AddConstraint(
            model_name='dutyrosterassignment',
            constraint=models.UniqueConstraint(fields=('roster', 'duty_date', 'engineer_name'), name='unique_roster_date_engineer'),
        ),
        migrations.AddIndex(
            model_name='dutyrosterassignment',
            index=models.Index(fields=['duty_date', 'duty_code'], name='monitor_dut_duty_da_9f4262_idx'),
        ),
    ]
