from django.core.management.base import BaseCommand
from django.utils import timezone
from shiftreport.models import MnocPfeReport
from shiftreport.pfe_delivery import deliver_pfe


class Command(BaseCommand):
    help = 'Send due MNOC PFE reports (execute every minute using an OS scheduler).'

    def handle(self, *args, **kwargs):
        ids = list(MnocPfeReport.objects.filter(
            status='SCHEDULED', scheduled_send_at__lte=timezone.now(),
        ).values_list('pk', flat=True)[:100])
        for pk in ids:
            try:
                deliver_pfe(pk, scheduled_only=True)
            except ValueError:
                continue  # Another worker already claimed, or canceled.
            except Exception as exc:
                self.stderr.write(f'PFE report {pk}: delivery failed: {exc}')
        self.stdout.write(f'Checked {len(ids)} due PFE reports.')
