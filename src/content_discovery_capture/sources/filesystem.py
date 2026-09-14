from __future__ import annotations
from pathlib import Path
import mimetypes
import os
import stat
import time
import heapq

from ..domain import Scope, Budget, DiscoveryBatch, Observation, CaptureResult, CaptureError, AccessRequired, BudgetExceeded


_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def _descriptor_parent(path: Path):
    """Open every parent component without following symlinks (POSIX)."""
    if os.name == "nt" or not path.is_absolute() or os.open not in getattr(os, "supports_dir_fd", set()):
        return None, None
    # macOS exposes /var and /tmp as system symlinks. Resolve only the parents;
    # the final component is still opened/stat'ed with O_NOFOLLOW/follow_symlinks=False.
    if path == Path(os.sep):
        resolved = path
    else:
        resolved = path.parent.resolve(strict=False) / path.name
    parts = resolved.parts
    fd = os.open(os.sep, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
    if len(parts) == 1:
        return fd, None
    try:
        for component in parts[1:-1]:
            child = os.open(component, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, parts[-1]
    except Exception:
        os.close(fd)
        raise


def _safe_lstat(path: Path):
    parent, name = _descriptor_parent(path)
    if parent is None:
        return path.lstat()
    try:
        if name is None:
            return os.fstat(parent)
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    finally:
        os.close(parent)


def _open_nofollow(path: Path, flags: int):
    parent, name = _descriptor_parent(path)
    if parent is None:
        return os.open(path, flags | _O_NOFOLLOW)
    if name is None:
        return parent
    try:
        fd = os.open(name, flags | _O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    return fd


class FilesystemAdapter:
    kind = "filesystem"

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()

    def allowed(self, path: Path, scope: Scope):
        return scope.permits(str(path), self.kind) and not (path.resolve() == self.project_root or path.resolve().is_relative_to(self.project_root))

    def discover(self, source, scope, cursor, budget):
        path = Path(cursor["location"])
        batch = DiscoveryBatch()
        if not self.allowed(path, scope):
            return batch
        try:
            # Descriptor-relative lstat/open prevents a parent directory from being
            # replaced with a symlink between scope validation and enumeration.
            info = _safe_lstat(path)
            if stat.S_ISLNK(info.st_mode):
                batch.gaps.append({"location": str(path), "reason": "Symlink not followed", "excluded": True})
                return batch
            if stat.S_ISDIR(info.st_mode):
                if cursor["depth"] >= scope.max_depth:
                    batch.gaps.append({"location": str(path), "reason": "Depth limit"})
                    return batch
                # A lexical checkpoint bounds memory and is independent of directory entry order.
                after = cursor.get("after", "")
                stamp = [info.st_mtime_ns, info.st_ctime_ns]
                if cursor.get("stamp", stamp) != stamp:
                    after = ""
                started = time.monotonic()
                def candidates(entries):
                    for entry in entries:
                        if time.monotonic() - started > budget.max_seconds:
                            raise BudgetExceeded("Directory inspection reached its time budget.")
                        if entry.name > after:
                            yield entry.name
                limit = min(256, budget.max_items)
                dir_fd = _open_nofollow(path, os.O_RDONLY | _O_DIRECTORY)
                try:
                    with os.scandir(os.dup(dir_fd)) as entries:
                        names = heapq.nsmallest(limit + 1, candidates(entries))
                finally:
                    os.close(dir_fd)
                chunk = names[:limit]
                batch.frontier = [{"location": str(path / name), "depth": cursor["depth"] + 1} for name in chunk]
                if len(names) > limit:
                    batch.frontier.append({"location": str(path), "depth": cursor["depth"], "after": chunk[-1], "stamp": stamp})
                return batch
            if not stat.S_ISREG(info.st_mode):
                batch.gaps.append({"location": str(path), "reason": "Special file excluded", "excluded": True})
                return batch
            if info.st_nlink > 1:
                batch.gaps.append({"location": str(path), "reason": "Hard-linked file excluded", "excluded": True})
                return batch
            batch.observations.append(Observation(
                identity=f"{info.st_dev}:{info.st_ino}", location=str(path), title=path.name,
                size=info.st_size, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                metadata={"mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
                          "device": info.st_dev, "inode": info.st_ino, "identity_basis": "filesystem-file-id"},
                parent=str(path.parent)))
            return batch
        except PermissionError:
            raise AccessRequired("Filesystem permission is required for this location.") from None
        except FileNotFoundError:
            batch.gaps.append({"location": str(path), "reason": "Location disappeared during scan"})
            return batch

    def methods(self, observation):
        return [] if observation.get("external") else ["copy"]

    def capture(self, observation, scope, method, destination, max_bytes, timeout):
        path = Path(observation["location"])
        if method != "copy" or not self.allowed(path, scope):
            raise CaptureError("File is outside the approved scope or method is unsupported.")
        started = time.monotonic()
        total = 0
        try:
            # Walk every component without following symlinks, including the final file.
            fd = _open_nofollow(path, os.O_RDONLY)
            with os.fdopen(fd, "rb") as source, destination.open("xb") as target:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise CaptureError("Only regular files may be copied.")
                if before.st_nlink > 1:
                    raise CaptureError("Hard-linked files are excluded from capture.")
                meta = observation["metadata"]
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        meta["device"], meta["inode"], observation["size"], meta["mtime_ns"], meta["ctime_ns"]):
                    raise CaptureError("Source changed since the reviewed inventory; refresh and review this version.")
                if not self.allowed(path, scope):
                    raise CaptureError("Source path left the authorised scope.")
                while block := source.read(min(1024 * 1024, max_bytes - total + 1)):
                    total += len(block)
                    if total > max_bytes or time.monotonic() - started > timeout:
                        raise BudgetExceeded("Copy reached the approved budget.")
                    target.write(block)
                after = os.fstat(source.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise CaptureError("File changed while being copied.")
        except PermissionError:
            raise AccessRequired("Filesystem permission is required to copy this file.") from None
        return CaptureResult(destination, observation["media_type"])
