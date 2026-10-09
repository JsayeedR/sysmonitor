from copy import deepcopy
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


ORIGINAL_HISTORICAL_ROWS = [{'category_1': 'NOC', 'category_2': 'Cooling at Equipment room', 'details': 'High temp alarm on COX-SAT-6500_SH1 SL9', 'remarks': '">10-06-2026 Alarm appeared due to less cooling around SLTE isle. Post Hard Reset alarm cleared. Additinally, run stand fan to cool down (fan also need maintenance) ***This board does not contain any traffic right now. If any traffic carrying board experience such issue, full traffic within the card will be down instantly. (For East such each card carrying 300G & for West such each card carrying 400G) >11-06-2026 MNOC reported same alarm on multiple TRM. No alarm observed on COX terminal Replied the same in email & TT (7N110626), asked to share alarm log >12-06-2026 No alarm appears"', 'event_type': 'Reporting'}, {'category_1': 'MNOC', 'category_2': '10G Tester', 'details': 'Testing revealed that the tester is faulty.', 'remarks': '"09-05-2026 >Test was clear but test report can not be saved due to appearing an error message. MNOC has been informed. Tried application restart and tester reboot but no luck. 13-05-2026 >Query update from MNOC regarding repair procedure. 19-05-2026 >Ask MNOC for update 20-05-2026 >Share error details with MNOC again for checking repair scope 22-06-2026 >MNOC suggested to check this scope with O&MSC on quarterly conference call"', 'event_type': 'Followup'}, {'category_1': 'NOC', 'category_2': 'Power', 'details': 'DC Distribution Capacity Assessment', 'remarks': '"Contact HS Engineering for costing of Rectifier module. Cost is yet to receive."', 'event_type': 'Followup'}, {'category_1': 'NOC', 'category_2': 'Co-location', 'details': 'Removal of BTCL dismantled equipment from station.', 'remarks': '">According to Mr. Hasib (DGM-Tx), they are waiting for BSCPLC reply on their letter regarding removal. >Requested Mr. Hasib to communicate with respective department and accelerate removal process."', 'event_type': 'Reporting'}, {'category_1': 'MNOC', 'category_2': 'Secure VPN solution proposal', 'details': '"Red Sea Cable Cut: To restore SMW4 Network Supervision => Secure VPN solution proposal- COX IIG propose conference call for clarification."', 'remarks': '">MNOC requested their queries on 28/11/25 >COX NOC requests IIG for taking care of the queries on 03/12/25 >IIG requests for conference call on 10/12/25 >Conference call organized on 14/12/25 >IIG unit requested to discuss price schedule with Marketing team on 30/12/25 >COX NOC loop MNS team for price schedule on 01/01/26 >Ask MNS for update on 7 JAN 25. >MNS team will share the cost after including into current tariff plan post discussion with IIG team. > Asked MNS for update on 18 JAN 26 Again"', 'event_type': 'Followup'}, {'category_1': 'NOC', 'category_2': 'Air Conditioner', 'details': 'Cooling at NOC equipment room', 'remarks': '**AC-7: Compressor damaged since August, 2024.\n**AC-12: is not running post burning smell issue at PFE room.\n**AC-1: Compressor issue', 'event_type': 'Followup'}, {'category_1': 'NOC', 'category_2': 'Air Conditioner', 'details': 'Cooling at Co-location', 'remarks': "*** Timers have been installed for 2X2 ton AC's and 1X5 ton AC on 28/10/2025. After the installation:\nminimum running capacity: 6 ton (including Summit's 2-ton AC)\nmaximum running capacity: 11 ton (including Summit's 2-ton AC) ", 'event_type': 'Followup'}, {'category_1': 'Power', 'category_2': 'Rectifier', 'details': 'Rectifier Module', 'remarks': 'An alarm is being observed on Rectifier-A, Module 25. Removed on 02/02/2026 (stored in store)', 'event_type': 'Followup'}, {'category_1': 'NOC', 'category_2': 'Roof water leakage', 'details': "It didn't rain during the shift.", 'remarks': '', 'event_type': 'Reporting'}, {'category_1': 'NOC', 'category_2': 'Automatic Fire Control System', 'details': 'No active alarms. Cylinder pressure is fine on indicator.', 'remarks': '', 'event_type': 'Reporting'}, {'category_1': 'NOC', 'category_2': 'Colocation Room & Generator Room', 'details': '"For proper monitoring and record-keeping, we urgently need at least two cameras in the colocation room to cover the rack faces, especially when someone works alone. Additionally, one camera is required in the generator room to cover the ATS, AVR, and transformer area."', 'remarks': '"Camera Coverage in Colocation Room & Generator Room Remarks: Additional cameras are required to achieve complete monitoring coverage in the colocation room and generator room."', 'event_type': 'Followup'}]

def initialize_personal_versions(apps, schema_editor):
    Revision = apps.get_model('shiftreport','HistoricalReportRevision')
    Profile = apps.get_model('monitor','UserProfile')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    # Do not infer a common seed from the mutable/previously sent global V1.
    # Existing legacy revisions remain exactly as they were in the database.
    seed_rows = ORIGINAL_HISTORICAL_ROWS
    ids = set(Profile.objects.filter(role__in=['admin','user']).values_list('user_id',flat=True))
    ids.update(User.objects.filter(is_superuser=True).values_list('pk',flat=True))
    for user_id in sorted(ids):
        Revision.objects.get_or_create(
            owner_id=user_id, revision_number=1,
            defaults={'rows':deepcopy(seed_rows),'status':'DRAFT','created_by_id':user_id},
        )


class Migration(migrations.Migration):
    dependencies = [('shiftreport','0007_shiftreport_auto_send_after_shift')]
    operations = [
        migrations.AddField(model_name='historicalreportrevision', name='owner',field=models.ForeignKey(
            to=settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='personal_historical_revisions')),
        migrations.AlterField(model_name='historicalreportrevision',name='revision_number',field=models.PositiveIntegerField()),
        migrations.AlterField(model_name='historicalreportrevision',name='status',field=models.CharField(
            max_length=10, default='DRAFT',choices=[('DRAFT','Draft'),('SAVED','Saved Draft'),('SENT','Sent')])),
        migrations.AddConstraint(model_name='historicalreportrevision', constraint=models.UniqueConstraint(
            fields=('owner','revision_number'),name='unique_personal_historical_version')),
        migrations.RunPython(initialize_personal_versions,migrations.RunPython.noop),
    ]
