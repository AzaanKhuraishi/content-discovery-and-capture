"""Transactional metadata and immutable, atomically promoted content objects."""
from __future__ import annotations
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import json
import os
import sqlite3

from .domain import CaptureError, canonical, now, safe_data, uid

IMMUTABLE = {"snapshot", "scope", "version", "representation", "placement", "artefact", "verification", "provenance", "comparison", "manifest"}


@contextmanager
def project_lock(root):
    """One execution writer per project; user review transactions remain independent."""
    import fcntl
    with (Path(root) / "execution.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CaptureError("Another operation is executing in this project. Retry when it finishes.") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.objects = self.root / "objects"
        self.staging = self.root / "staging"
        self.objects.mkdir(exist_ok=True, mode=0o700)
        self.staging.mkdir(exist_ok=True, mode=0o700)
        self.objects.chmod(0o700)
        self.staging.chmod(0o700)
        db_path = self.root / "project.sqlite3"
        db_path.touch(mode=0o600, exist_ok=True)
        db_path.chmod(0o600)
        self.db = sqlite3.connect(db_path, timeout=30)
        # A private rollback journal avoids world-readable WAL/SHM sidecars on
        # systems whose SQLite defaults inherit a permissive umask.
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS records (
                kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(kind, id));
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS immutable_records BEFORE UPDATE ON records
            WHEN OLD.kind IN ('snapshot','scope','version','representation','placement','artefact','verification','provenance','comparison','manifest')
            BEGIN SELECT RAISE(ABORT, 'immutable record'); END;
            CREATE TRIGGER IF NOT EXISTS retain_records BEFORE DELETE ON records
            BEGIN SELECT RAISE(ABORT, 'history is retained'); END;
            CREATE TRIGGER IF NOT EXISTS retain_events BEFORE UPDATE ON events
            BEGIN SELECT RAISE(ABORT, 'immutable event'); END;
            CREATE TRIGGER IF NOT EXISTS retain_events_delete BEFORE DELETE ON events
            BEGIN SELECT RAISE(ABORT, 'immutable event'); END;
        """)
        version = self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if version and version[0] != "1":
            raise CaptureError("This project requires a different application schema version.")
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version','1')")
        self.db.commit()
        for sidecar in (self.root / "project.sqlite3-wal", self.root / "project.sqlite3-shm",
                        self.root / "project.sqlite3-journal"):
            if sidecar.exists():
                sidecar.chmod(0o600)

    @contextmanager
    def transaction(self):
        with self.db:
            yield

    def put(self, kind, record):
        record = {"schema_version": 1, **record}
        existing = self.get(kind, record["id"], optional=True)
        if existing is not None and kind in IMMUTABLE:
            if existing != record:
                raise CaptureError("Cannot overwrite immutable history.")
            return record
        self.db.execute("INSERT INTO records VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data",
                        (kind, record["id"], canonical(record)))
        return record

    def get(self, kind, identity, optional=False):
        row = self.db.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, identity)).fetchone()
        if row:
            return json.loads(row[0])
        if optional:
            return None
        raise CaptureError(f"Unknown {kind}.")

    def all(self, kind):
        return [json.loads(row[0]) for row in self.db.execute("SELECT data FROM records WHERE kind=? ORDER BY rowid", (kind,))]

    def event(self, kind, data):
        self.db.execute("INSERT INTO events(at,kind,data) VALUES (?,?,?)", (now(), kind, canonical(safe_data(data))))

    def temp(self):
        return self.staging / uid("transfer")

    def promote(self, path: Path, expected=None):
        try:
            if path.is_symlink():
                raise CaptureError("Staged content cannot be a symbolic link.")
            resolved = path.resolve(strict=False)
            if resolved.parent != self.staging.resolve() and not resolved.is_relative_to(self.staging.resolve()):
                raise CaptureError("Staged content is outside the private project directory.")
        except (OSError, RuntimeError):
            raise CaptureError("Staged content is unavailable.") from None
        hasher = sha256()
        size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                hasher.update(chunk)
            os.fsync(stream.fileno())
        hexdigest = hasher.hexdigest()
        if expected and expected != hexdigest:
            raise CaptureError("Content integrity check failed.")
        target = self.objects / hexdigest
        if target.exists():
            if self.hash_file(target) != hexdigest:
                raise CaptureError("Existing content object failed integrity verification.")
            path.unlink()
        else:
            os.replace(path, target)
            target.chmod(0o444)
            fd = os.open(self.objects, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return {"sha256": hexdigest, "size": size}

    @staticmethod
    def hash_file(path):
        h = sha256()
        with Path(path).open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()

    def object_path(self, hexdigest):
        if len(hexdigest) != 64 or any(c not in "0123456789abcdef" for c in hexdigest):
            raise CaptureError("Invalid content identity.")
        return self.objects / hexdigest

    def close(self):
        self.db.close()
