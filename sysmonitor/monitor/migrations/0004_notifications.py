from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0003_outagecycle'),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationGateway',
            fields=[
                ('id',           models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel',      models.CharField(max_length=20, unique=True, choices=[
                                    ('whatsapp', 'WhatsApp (Meta Cloud API)'),
                                    ('telegram', 'Telegram Bot'),
                                    ('email',    'Email (Gmail SMTP)'),
                                 ])),
                ('is_enabled',   models.BooleanField(default=False)),
                # WhatsApp fields
                ('wa_phone_number_id',  models.CharField(max_length=100, blank=True)),
                ('wa_access_token',     models.CharField(max_length=500, blank=True)),
                ('wa_from_number',      models.CharField(max_length=30,  blank=True)),
                # Telegram fields
                ('tg_bot_token',        models.CharField(max_length=200, blank=True)),
                # Email fields
                ('email_host',          models.CharField(max_length=100, blank=True, default='smtp.gmail.com')),
                ('email_port',          models.IntegerField(default=587)),
                ('email_username',      models.CharField(max_length=200, blank=True)),
                ('email_password',      models.CharField(max_length=200, blank=True)),
                ('email_from',          models.CharField(max_length=200, blank=True)),
                ('updated_at',          models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': 'Notification Gateway'},
        ),
        migrations.CreateModel(
            name='NotificationRecipient',
            fields=[
                ('id',              models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name',            models.CharField(max_length=100)),
                ('channel',         models.CharField(max_length=20, choices=[
                                        ('whatsapp', 'WhatsApp'),
                                        ('telegram', 'Telegram'),
                                        ('email',    'Email'),
                                    ])),
                # contact — phone for WA/SMS, chat_id for Telegram, address for email
                ('contact',         models.CharField(max_length=200)),
                ('is_active',       models.BooleanField(default=True)),
                # which alert types this recipient gets
                ('alert_outage',    models.BooleanField(default=True,  verbose_name='Outage Start')),
                ('alert_critical',  models.BooleanField(default=True,  verbose_name='Critical Alert')),
                ('alert_alarm',     models.BooleanField(default=True,  verbose_name='Alarm Alert')),
                ('alert_complete',  models.BooleanField(default=True,  verbose_name='Cycle Complete')),
                ('added_at',        models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ['name'], 'verbose_name': 'Notification Recipient'},
        ),
        migrations.CreateModel(
            name='NotificationLog',
            fields=[
                ('id',          models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cycle_id',    models.IntegerField(null=True, blank=True)),
                ('event_type',  models.CharField(max_length=20)),   # OUTAGE_START, CRITICAL, ALARM, COMPLETE
                ('channel',     models.CharField(max_length=20)),
                ('recipient',   models.CharField(max_length=200)),
                ('status',      models.CharField(max_length=10)),   # SENT, FAILED
                ('error',       models.TextField(blank=True)),
                ('sent_at',     models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ['-sent_at'], 'verbose_name': 'Notification Log'},
        ),
    ]
