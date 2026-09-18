"""POSIX no-follow, descriptor-relative I/O for the offline recovery transaction.

Writer exclusion includes namespace changes. Existing directories are pinned and
rechecked before mutations; names are never resolved through symlinks. Descriptors
also prevent a late parent symlink replacement from redirecting an operation.
"""

import json
import os
import secrets
import stat
import time
from contextlib import contextmanager
from pathlib import Path

import portalocker


class RecoveryPaths:
    def __init__(self, root):
        # Canonical root aliases are supported, but descendants must not alias.
        self.root = Path(root).resolve(strict=True)
        self.dirs = {"": os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)}
        self.paths = set()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        for fd in self.dirs.values():
            os.close(fd)

    @staticmethod
    def parts(name):
        if (
            not isinstance(name, str)
            or not name
            or any(p in ("", ".", "..") for p in name.split("/"))
            or name.startswith("/")
        ):
            raise ValueError("invalid recovery relative path")
        return name.split("/")

    def parent(self, name, *, create=False):
        parts = self.parts(name)
        fd, key = self.dirs[""], ""
        for part in parts[:-1]:
            key = f"{key}/{part}" if key else part
            try:
                opened = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if key in self.dirs:
                    raise ValueError("recovery directory disappeared")
                if not create:
                    return None, parts[-1]
                os.mkdir(part, mode=0o700, dir_fd=fd)
                os.fsync(fd)
                opened = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            if key in self.dirs:
                a, b = os.fstat(opened), os.fstat(self.dirs[key])
                os.close(opened)
                if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                    raise ValueError("recovery directory replaced")
            else:
                self.dirs[key] = opened
            fd = self.dirs[key]
        return fd, parts[-1]

    def check(self, name):
        self.paths.add(name)
        fd, leaf = self.parent(name)
        if fd is None:
            return
        try:
            info = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("recovery path is not a regular file: " + name)

    def recheck(self):
        info, pinned = self.root.stat(), os.fstat(self.dirs[""])
        if (info.st_dev, info.st_ino) != (pinned.st_dev, pinned.st_ino):
            raise ValueError("recovery root replaced")
        for name in tuple(self.paths):
            self.check(name)

    def read(self, name, *, optional=False):
        self.check(name)
        fd, leaf = self.parent(name)
        try:
            if fd is None:
                raise FileNotFoundError(name)
            opened = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except FileNotFoundError:
            if optional:
                return None
            raise
        with os.fdopen(opened, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("recovery input is not a regular file")
            return handle.read()

    def write(self, name, data, *, append=False):
        self.check(name)
        self.recheck()
        fd, leaf = self.parent(name, create=True)
        if append:
            opened = os.open(
                leaf,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=fd,
            )
            with os.fdopen(opened, "ab") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValueError("recovery audit is not a regular file")
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(fd)
            return
        tmp = ".recovery-" + secrets.token_hex(16)
        opened = os.open(
            tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        try:
            with os.fdopen(opened, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, leaf, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(tmp, dir_fd=fd)
            except FileNotFoundError:
                pass

    def json(self, name, value, *, append=False):
        self.write(
            name,
            json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode() + b"\n",
            append=append,
        )

    @contextmanager
    def locked(self, name):
        # Same lock inode/protocol as cost_control, without pathname-following I/O.
        from .cost_control import CostControlLockBusyError

        self.check(name)
        self.recheck()
        parent, leaf = self.parent(name)
        fd = os.open(
            leaf, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent
        )
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("recovery lock is not a regular file")
            deadline = time.monotonic() + 2
            while True:
                try:
                    portalocker.lock(fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
                    break
                except portalocker.exceptions.LockException as exc:
                    if time.monotonic() >= deadline:
                        raise CostControlLockBusyError("recovery lock busy") from exc
                    time.sleep(0.01)
            yield
        finally:
            os.close(fd)
