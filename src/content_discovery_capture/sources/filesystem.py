from __future__ import annotations
from pathlib import Path
import mimetypes
import os
import stat
import time
import heapq

from ..domain import Scope, Budget, DiscoveryBatch, Observation, CaptureResult, CaptureError, AccessRequired, BudgetExceeded


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
            info = path.lstat()
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
                with os.scandir(path) as entries:
                    names = heapq.nsmallest(limit + 1, candidates(entries))
                chunk = names[:limit]
                batch.frontier = [{"location": str(path / name), "depth": cursor["depth"] + 1} for name in chunk]
                if len(names) > limit:
                    batch.frontier.append({"location": str(path), "depth": cursor["depth"], "after": chunk[-1], "stamp": stamp})
                return batch
            if not stat.S_ISREG(info.st_mode):
                batch.gaps.append({"location": str(path), "reason": "Special file excluded", "excluded": True})
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
            # O_NOFOLLOW prevents swapping the final file for a symlink at open time.
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as source, destination.open("xb") as target:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise CaptureError("Only regular files may be copied.")
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
