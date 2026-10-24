"""Local-master Shift SMTP concurrency guard.

This is an inter-process advisory lock, NOT a durable SMTP idempotency key.
It protects overlapping HTTP/scheduler invocations on one Ubuntu master,
without changing report rows or deleting any history.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path


class ShiftDeliveryBusy(Exception):
    """Another master process is attempting delivery for this report."""


@contextmanager
def exclusive_shift_delivery(report_id, *, lock_dir=None):
    report_id = int(report_id)
    if report_id <= 0:
        raise ValueError('Positive Shift Report ID required')
    folder = Path(lock_dir) if lock_dir is not None else Path(__file__).resolve().parent.parent / 'logs' / 'shift_delivery_locks'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = folder / f'shift-{report_id}.lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise ShiftDeliveryBusy('A Shift Report delivery is already in progress.') from exc
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
