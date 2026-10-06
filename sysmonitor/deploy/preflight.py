"""
Pre-flight check run before code is published (master) or activated (remote).
Catches what `manage.py check` alone misses:
  1. every .py file compiles (syntax errors anywhere, even in rarely-loaded modules)
  2. every Django template parses (bad {% tags %} / filters)
  3. Django's own `check`
Exit code 0 = safe, 1 = problem (details printed).
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
os.chdir(BASE)
sys.path.insert(0, str(BASE))
problems = []

py_files = [BASE / 'manage.py'] + sorted(BASE.glob('monitor/**/*.py')) + sorted(BASE.glob('sysmonitor/**/*.py'))
for p in py_files:
    try:
        compile(p.read_text(encoding='utf-8'), str(p), 'exec')
    except SyntaxError as e:
        problems.append(f'syntax error: {p.relative_to(BASE)} line {e.lineno}: {e.msg}')

if not problems:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sysmonitor.settings')
    try:
        import django
        django.setup()
        from django.core.management import call_command
        from django.template import engines
        call_command('check', verbosity=0)
        engine = engines['django']
        for p in sorted(BASE.glob('monitor/templates/**/*.html')):
            try:
                engine.get_template(str(p.relative_to(BASE / 'monitor' / 'templates')))
            except Exception as e:          # TemplateSyntaxError etc.
                problems.append(f'template error: {p.relative_to(BASE)}: {e}')
    except Exception as e:
        problems.append(f'django check failed: {e}')

if problems:
    print('PREFLIGHT FAILED:')
    print('\n'.join('  - ' + x for x in problems))
    sys.exit(1)
print('preflight OK')
