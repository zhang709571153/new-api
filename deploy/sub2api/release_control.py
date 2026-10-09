"""Small host-local admission and startup controls; no account credentials."""
from contextlib import contextmanager
from pathlib import Path
import os
import threading


@contextmanager
def release_lock(private):
    """The release runner and scheduled/manual startup share this OS lock."""
    import msvcrt
    path = Path(private) / 'release.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError('Another release or startup owns the deployment lock') from error
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def maintenance_active(marker):
    """Only an absent marker opens admission; inaccessible markers fail closed."""
    try:
        Path(marker).lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


@contextmanager
def maintenance_safe_write(private):
    """Serialize local writers with releases, checking the gate after the lock."""
    with release_lock(private):
        if maintenance_active(Path(private) / 'release-maintenance.json'):
            raise RuntimeError('Release maintenance is active; local writes are suspended')
        yield


class Admission:
    def __init__(self, marker):
        self.marker = Path(marker)
        self.lock = threading.Lock()
        self.active = 0

    def closed(self):
        return maintenance_active(self.marker)

    def enter(self):
        with self.lock:
            if self.closed():
                return False
            self.active += 1
            return True

    def leave(self):
        with self.lock:
            self.active -= 1

    def status(self):
        with self.lock:
            return {'maintenance': self.closed(), 'active_requests': self.active,
                    'pid': os.getpid(), 'protocol': 1}
