from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0004_notifications'),
    ]

    operations = [
        migrations.CreateModel(
            name='GeneratorModeLog',
            fields=[
                ('id',          models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('generator',   models.CharField(max_length=20, choices=[
                                    ('Gen-01', 'Generator 01'),
                                    ('Gen-02', 'Generator 02'),
                                 ])),
                ('switched_at', models.DateTimeField()),
                ('note',        models.CharField(max_length=200, blank=True)),
                ('added_by',    models.CharField(max_length=100, blank=True)),
                ('created_at',  models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ['-switched_at'], 'verbose_name': 'Generator Mode Log'},
        ),
        migrations.AddField(
            model_name='notificationrecipient',
            name='daily_summary',
            field=models.BooleanField(default=False),
        ),
    ]
