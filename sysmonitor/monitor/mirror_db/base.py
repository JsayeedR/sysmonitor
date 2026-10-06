"""
SQLite backend for the REMOTE mirror only (SYSMONITOR_ROLE=remote).

Problem it solves: the mirror receives a fresh database snapshot every ~30 s.
If Django kept using one fixed file name, a snapshot being swapped in could
collide with the small writes the app makes on every request (page counter,
last login…) and a request could fail with "disk I/O error".

Solution: every snapshot arrives under its OWN file name (mirror-<id>.sqlite3)
and a tiny pointer file (mirror_current) says which one is newest. Each new
database connection (= each request) reads the pointer and opens that file.
A file is never replaced while in use, and a request that started on an older
snapshot simply finishes on it. Old snapshots are deleted after ~10 minutes.
"""
from pathlib import Path

from django.db.backends.sqlite3 import base as sqlite_base


class DatabaseWrapper(sqlite_base.DatabaseWrapper):
    def get_connection_params(self):
        params = super().get_connection_params()
        base_dir = Path(self.settings_dict['NAME']).parent
        try:
            name = (base_dir / 'mirror_current').read_text().strip()
            if name and '/' not in name and name.startswith('mirror-'):
                params['database'] = str(base_dir / name)
        except OSError:
            pass  # no pointer yet → fall back to NAME (mirror.sqlite3)
        return params
