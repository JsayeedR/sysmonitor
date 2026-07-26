from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0005_generator_mode_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='designation',
            field=models.CharField(max_length=100, blank=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='mobile_number',
            field=models.CharField(max_length=30, blank=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='whatsapp_number',
            field=models.CharField(max_length=30, blank=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='telegram_handle',
            field=models.CharField(max_length=100, blank=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='profile_picture',
            field=models.ImageField(upload_to='profile_pics/', blank=True, null=True),
        ),
        migrations.CreateModel(
            name='ProfileChangeRequest',
            fields=[
                ('id',           models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('field',        models.CharField(max_length=10, choices=[('email', 'Email'), ('mobile', 'Mobile Number')])),
                ('old_value',    models.CharField(max_length=200, blank=True)),
                ('new_value',    models.CharField(max_length=200)),
                ('status',       models.CharField(max_length=10, choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')], default='PENDING')),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at',  models.DateTimeField(null=True, blank=True)),
                ('reviewed_by',  models.CharField(max_length=100, blank=True)),
                ('user',         models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='change_requests', to='auth.user')),
            ],
            options={'ordering': ['-requested_at']},
        ),
    ]
