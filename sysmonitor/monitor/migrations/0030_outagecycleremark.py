from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('monitor', '0029_duty_roster_original_file'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.AlterField(model_name='userprofile',name='role',field=models.CharField(max_length=12,choices=[('admin','Admin'),('user','User'),('viewer','Viewer'),('fullviewer','Full Viewer (Read Only)'),('guest','Guest')],default='viewer')),
    migrations.CreateModel(name='OutageCycleRemark', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('text', models.TextField(max_length=2000)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
        ('author', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='outage_remarks', to=settings.AUTH_USER_MODEL)),
        ('cycle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='engineer_remarks', to='monitor.outagecycle')),
    ], options={'ordering': ['created_at', 'pk']}),
    migrations.AddConstraint(model_name='outagecycleremark', constraint=models.UniqueConstraint(fields=('cycle','author'), name='unique_cycle_engineer_remark'))]
