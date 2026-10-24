"""Server-side validation and gated access for profile images only."""
import io
import uuid
from pathlib import PurePosixPath
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404
from django.contrib.auth.decorators import login_required
from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PROFILE_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 12_000_000


def sanitize_profile_image(upload):
    if getattr(upload, 'size', 0) > MAX_PROFILE_BYTES or getattr(upload, 'size', 0) <= 0:
        raise ValueError('Profile image must be between 1 byte and 2 MB.')
    try:
        raw = upload.read(MAX_PROFILE_BYTES + 1)
        if len(raw) > MAX_PROFILE_BYTES:
            raise ValueError('Profile image exceeds 2 MB.')
        with Image.open(io.BytesIO(raw)) as candidate:
            candidate.verify()
        with Image.open(io.BytesIO(raw)) as candidate:
            if candidate.format not in ('JPEG', 'PNG'):
                raise ValueError('Only JPEG and PNG profile images are accepted.')
            if candidate.width * candidate.height > MAX_PIXELS:
                raise ValueError('Image dimensions exceed the supported limit.')
            img = ImageOps.exif_transpose(candidate)
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            else:
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=86, optimize=True)
    except (UnidentifiedImageError, OSError, SyntaxError, OverflowError) as exc:
        raise ValueError('Invalid profile image.') from exc
    finally:
        upload.seek(0)
    return ContentFile(buf.getvalue(), name=f'{uuid.uuid4().hex}.jpg')


@login_required
def authenticated_profile_media(request, path):
    # Never expose arbitrary local media or old uploaded active content.
    pure = PurePosixPath(path)
    if len(pure.parts) != 2 or pure.parts[0] != 'profile_pics':
        raise Http404
    if pure.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
        raise Http404
    from monitor.models import UserProfile
    if not UserProfile.objects.filter(profile_picture=path).exists():
        raise Http404
    from django.core.files.storage import default_storage
    if not default_storage.exists(path):
        raise Http404
    # Existing images are treated as inert binary image content with nosniff.
    mime = {'.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.png':'image/png',
            '.gif':'image/gif', '.webp':'image/webp'}[pure.suffix.lower()]
    response = FileResponse(default_storage.open(path, 'rb'), content_type=mime)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'"
    response['Cache-Control'] = 'private, no-store'
    response['Content-Disposition'] = 'inline'
    return response
