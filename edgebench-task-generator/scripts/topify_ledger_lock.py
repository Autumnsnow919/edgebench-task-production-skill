"""Serialize campaign mutations across HTTP threads and local CLI processes."""

from contextlib import contextmanager
import fcntl
from functools import wraps
import os
from pathlib import Path
import stat
import threading

import topify_common as common

_guard = threading.Lock()
_locks = {}
_local = threading.local()


@contextmanager
def locked(workspace):
    root = Path(workspace).resolve(strict=True)
    directory = root / "supervision"
    if directory.is_symlink():
        raise common.PipelineError("supervision directory must not be a symlink")
    directory.mkdir(exist_ok=True)
    with _guard:
        lock = _locks.setdefault(str(root), threading.RLock())
    with lock:
        held = getattr(_local, "held", set())
        if str(root) in held:
            yield
            return
        fd = os.open(directory / ".ledger.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise common.PipelineError("ledger lock must be a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX)
            _local.held = held | {str(root)}
            yield
        finally:
            _local.held = held
            os.close(fd)


def mutation(function):
    @wraps(function)
    def call(workspace, *args, **kwargs):
        with locked(workspace):
            return function(workspace, *args, **kwargs)
    return call
