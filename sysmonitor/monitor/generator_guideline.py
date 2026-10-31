"""RC28 read-only, UNAPPROVED generator-shifting document.

This module intentionally has no controls, workflows, or model writes.
The submitted electrical switching sequence requires competent site review.
"""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed, FileResponse
from pathlib import Path
from django.shortcuts import render

from .access_policy import is_full_viewer
from .page_access import user_can_access_page
from .views import get_role


# This is a transcription/clarification of the operator-supplied draft, NOT
# certification that any breaker, interlock, transfer or AVR sequence is safe.
def _permitted(request):
    user = request.user
    return bool(user.is_active and get_role(user) in ("viewer", "user", "admin")
                and not is_full_viewer(user)
                and user_can_access_page(user, "generator_shifting_guideline")
                and not getattr(request, "_mirror_forwarded", False))


PDB_ACTIVE_SECTIONS = (
    {
        'code': 'A', 'title': 'PDB Transfer Between ATS Panels',
        'subtitle': 'Submitted steps 1–3 · PDB remains energized',
        'steps': (
            (1, 'Set ATS-1 from AUTO to OFF using the front-panel control.'),
            (2, 'The submitted procedure calls for switching ON the ATS-2 PDB MCB to bring ACB-2 (PDB) into service and transfer the PDB supply through ATS-2.'),
            (3, 'After confirming that ATS-2 has taken over the PDB supply, the submitted procedure calls for switching OFF the ATS-1 PDB MCB, which is described as opening ACB-1.'),
        ),
    },
    {
        'code': 'B', 'title': 'Generator Selection',
        'subtitle': 'Submitted steps 4–9 · PDB live',
        'steps': (
            (4, 'Set GEN-1 from AUTO to OFF.'),
            (5, 'Open the GEN-1 circuit breaker.'),
            (6, 'Switch OFF the GEN-1 MCB at ATS-1.'),
            (7, 'Set GEN-2 to AUTO.'),
            (8, 'Close the GEN-2 circuit breaker.'),
            (9, 'Switch ON the GEN-2 MCB at ATS-2.'),
        ),
    },
    {
        'code': 'C', 'title': 'Proposed Generator Test',
        'subtitle': 'Submitted steps 10–13 · site-specific controls',
        'steps': (
            (10, 'The submitted test begins by pressing the RED AVR button to turn the AVR OFF.'),
            (11, 'The submitted timings are 2–3 minutes for GEN-2 to start and ATS-2 to transfer load (approximately 100 kW), followed by 1–2 minutes of stable loaded operation.'),
            (12, 'The submitted test then presses the GREEN AVR button to turn the AVR ON.'),
            (13, 'The submitted sequence expects GEN-2 to stop and ATS-2 to return the load to the PDB supply.'),
        ),
    },
)


@login_required(login_url='login')
def generator_shifting_guideline_view(request, scenario='pdb-active'):
    """Read-only safety-gated document. No server-side switching operations."""
    user = request.user
    if not _permitted(request):
        return render(request, 'monitor/denied.html', status=403)

    if request.method not in ('GET', 'HEAD'):
        return HttpResponseNotAllowed(['GET', 'HEAD'])

    if scenario not in ('pdb-active', 'pdb-out'):
        return render(request, 'monitor/denied.html', status=404)

    response = render(request, 'monitor/generator_shifting_guideline.html', {
        'role': get_role(user),
        'scenario': scenario,
        'sections': PDB_ACTIVE_SECTIONS if scenario == 'pdb-active' else (),
    })
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@login_required(login_url='login')
def guideline_illustration(request):
    """Serve the conceptual diagram privately without relying on collectstatic."""
    if not _permitted(request):
        return render(request, 'monitor/denied.html', status=403)
    if request.method not in ('GET', 'HEAD'):
        return HttpResponseNotAllowed(['GET', 'HEAD'])
    image_path = Path(__file__).resolve().parents[1] / 'static/img/generator_shifting_guideline_draft.png'
    result = FileResponse(image_path.open('rb'), content_type='image/png')
    result['Cache-Control'] = 'private, no-store, max-age=0'
    result['X-Content-Type-Options'] = 'nosniff'
    return result
