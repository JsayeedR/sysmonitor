# Generated for RC7 isolated rehearsal. Expand-only: no existing data changed.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('monitor', '0030_outagecycleremark')]

    operations = [
        migrations.CreateModel(
            name='HistoricalActor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('original_user_id', models.PositiveIntegerField(unique=True)),
                ('username', models.CharField(max_length=150)),
                ('display_name', models.CharField(max_length=301)),
                ('designation', models.CharField(max_length=100, blank=True)),
                ('signature_phone', models.CharField(max_length=30, blank=True)),
                ('profile_picture_path', models.CharField(max_length=500, blank=True)),
                ('captured_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ['original_user_id']},
        ),
        migrations.CreateModel(
            name='ArchivedAdminLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_log_id', models.PositiveIntegerField(unique=True)),
                ('action_time', models.DateTimeField()),
                ('action_flag', models.PositiveSmallIntegerField()),
                ('original_content_type_id', models.PositiveIntegerField(null=True, blank=True)),
                ('object_id', models.TextField(null=True, blank=True)),
                ('object_repr', models.CharField(max_length=200)),
                ('change_message', models.TextField(blank=True)),
                ('captured_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                            related_name='archived_admin_actions', to='monitor.historicalactor')),
            ],
            options={'ordering': ['-action_time', '-source_log_id']},
        ),
    ]
