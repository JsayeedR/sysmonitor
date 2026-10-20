"""Safe small draft attachments; no external unauthenticated download endpoints."""
from pathlib import Path
from django.core.exceptions import ValidationError
from .models import OptionalReportAttachment
import mimetypes

ALLOWED={'.pdf','.xlsx','.xls','.csv','.png','.jpg','.jpeg','.txt'}
MAX_BYTES=3*1024*1024
MAX_FILES=3

def store_optional_uploads(request, *, shift_report=None, pfe_report=None):
    uploads=request.FILES.getlist('report_attachments')
    if not uploads:
        return
    owner=shift_report or pfe_report
    if owner is None or (shift_report is not None and pfe_report is not None):
        raise ValidationError('Exactly one report is required')
    if owner.status == 'SENT' or owner.status == 'SENDING':
        raise ValidationError('Sent or sending reports cannot be changed')
    existing=owner.uploads.count()
    if existing+len(uploads)>MAX_FILES:
        raise ValidationError('Maximum 3 optional files per report')
    for upload in uploads:
        filename=Path(upload.name).name
        if filename in ('','.','..') or len(filename)>160 or Path(filename).suffix.lower() not in ALLOWED:
            raise ValidationError('Unsupported attachment filename/type')
        if upload.size<=0 or upload.size>MAX_BYTES:
            raise ValidationError('Each optional attachment must be 1 byte–3 MB')
    for upload in uploads:
        filename=Path(upload.name).name
        content=upload.read()
        if len(content)!=upload.size:
            raise ValidationError('Attachment read failed')
        OptionalReportAttachment.objects.create(
            shift_report=shift_report, pfe_report=pfe_report, name=filename,
            mime_type=mimetypes.guess_type(filename)[0] or 'application/octet-stream', content=content)

def outgoing_attachments(report):
    return [(f.name,bytes(f.content),f.mime_type) for f in report.uploads.all()]
