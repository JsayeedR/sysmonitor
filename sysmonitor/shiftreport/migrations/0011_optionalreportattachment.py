from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[('shiftreport', '0010_wetcircuitconfig')]
    operations=[migrations.CreateModel(name='OptionalReportAttachment',fields=[
      ('id',models.BigAutoField(primary_key=True,serialize=False,auto_created=True,verbose_name='ID')),
      ('name',models.CharField(max_length=160)),('mime_type',models.CharField(max_length=120,default='application/octet-stream')),
      ('content',models.BinaryField()),('uploaded_at',models.DateTimeField(auto_now_add=True)),
      ('shift_report',models.ForeignKey(to='shiftreport.shiftreport',related_name='uploads',null=True,blank=True,on_delete=django.db.models.deletion.CASCADE)),
      ('pfe_report',models.ForeignKey(to='shiftreport.mnocpfereport',related_name='uploads',null=True,blank=True,on_delete=django.db.models.deletion.CASCADE))]),
      migrations.AddConstraint(model_name='optionalreportattachment',constraint=models.CheckConstraint(condition=(models.Q(shift_report__isnull=False,pfe_report__isnull=True)|models.Q(shift_report__isnull=True,pfe_report__isnull=False)),name='report_attachment_one_owner'))]
