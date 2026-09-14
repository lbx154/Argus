"""The Vertical Store: community verticals installed one directory at a time.

The ``argus-verticals`` repository publishes, per GitHub release, one zip per
vertical (exactly that vertical's ``paths`` + ``shared`` trees with their
repo-relative paths) and a ``catalog.json`` naming every archive's URL,
sha256 and size. This module is the consumer side: it downloads an archive,
verifies it, extracts it under ``<store root>/argus_verticals/<name>/`` and
records the result in ``registry.json``. Nothing here runs ``pip``; a vertical's
``python_requirements`` are shown to the operator, never installed.

Layout under the store root (``<ARGUS_SKILL_HOME>/verticals`` or the directory
named by ``ARGUS_VERTICALS_HOST_ROOT``)::

    argus_verticals/<name>/...            extracted trees (shared helpers at their
    argus_verticals/literary/shared/...   repo-relative place)
    registry.json                         what is installed, enabled, and who owns
                                          which shared tree (portalocker ``store.lock``)
    catalog.json                          the last catalog fetched, with its source
    operations/<name>.json                the running or last install/update/remove job
    logs/<name>.log                       one line per job step

Discovery is ``_registry.py``'s job: it reads ``registry.json`` and makes the
store's ``argus_verticals`` importable (appended to a pip-installed copy's
``__path__`` when one exists -- the pip copy wins -- or as a synthetic namespace
package otherwise), so the frozen desktop works without any dist-info.

Security: catalog and archive URLs must be https on an allow-listed GitHub
host, redirects are followed only within that allow-list, archives are
size-capped, sha256-verified, and extracted only after every member is checked
(no absolute paths, no ``..``, no symlinks, no file outside the trees the
catalog declares for that vertical). Local ``file://`` sources are honoured only
when the catalog itself came from a local file (offline mirrors and tests).
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import httpx
import portalocker

from ..core import paths as core_paths
from ..core.pipeline_state import read_pipeline_state
from ..core.plugin_manager import read_json, write_json
from ..core.process_identity import capture_process_identity, process_identity_is_running

log = logging.getLogger(__name__)

PACKAGE = "argus_verticals"
#: When set, this directory *is* the store root (a prepared, possibly read-only
#: root shared by every tenant of a hosted image); install/update/remove are refused.
HOST_ROOT_ENV = "ARGUS_VERTICALS_HOST_ROOT"
#: An https URL, a local path or a ``file://`` URL of a ``catalog.json``.
CATALOG_ENV = "ARGUS_VERTICAL_CATALOG"
#: Comma-separated vertical names a deployment prepares at startup.
PREINSTALL_ENV = "ARGUS_VERTICALS_PREINSTALL"
DEFAULT_CATALOG_URL = (
    "https://github.com/Argus-AiTeam/argus-verticals/releases/latest/download/catalog.json"
)
ALLOWED_HOSTS = frozenset({
    "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
})
CATALOG_SCHEMA = 1
REGISTRY_SCHEMA = 1
CATALOG_MAX_AGE = timedelta(hours=6)
CATALOG_LIMIT = 8 * 1024 * 1024
ARCHIVE_LIMIT = 256 * 1024 * 1024
EXPANDED_LIMIT = 512 * 1024 * 1024
ACTIONS: tuple[str, ...] = ("install", "update", "enable", "disable", "uninstall")
JOB_ACTIONS = frozenset({"install", "update", "uninstall"})
KINDS: tuple[str, ...] = ("builtin", "package", "installed", "available")
HOST_MANAGED = (
    "verticals are provided by the host of this deployment: install, update and remove "
    "are disabled here (enable and disable still work)"
)

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_MODULE = re.compile(r"^argus_verticals(\.[a-z][a-z0-9_]*)+\.stages$")
_TREE = re.compile(r"^argus_verticals(/[a-z][a-z0-9_]*)+$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_ARCHIVE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*\.zip$")
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
#: Distribution names whose importable module is spelled differently.
_MODULE_ALIASES = {
    "pyyaml": "yaml", "scikit-learn": "sklearn", "pillow": "PIL", "opencv-python": "cv2",
    "beautifulsoup4": "bs4", "python-dateutil": "dateutil", "attrs": "attr",
    "msgpack-python": "msgpack", "protobuf": "google.protobuf",
}
_UNSET: Any = object()

_jobs: dict[tuple[str, str], threading.Thread] = {}
_jobs_lock = threading.RLock()


class VerticalStoreError(RuntimeError):
    """An operator-facing refusal or failure; the message is the whole explanation."""


class UnknownVerticalError(VerticalStoreError):
    """The name is not in the catalog / not installed (the API answers 404)."""


# --------------------------------------------------------------------------- #
# Roots and files
# --------------------------------------------------------------------------- #


def store_root(root: str | Path | None = None) -> Path:
    """The store root: ``ARGUS_VERTICALS_HOST_ROOT`` when set, else ``<home>/verticals``."""
    host = os.environ.get(HOST_ROOT_ENV, "").strip()
    if host:
        return core_paths.resolve_runtime_path(host, context=HOST_ROOT_ENV)
    return core_paths.verticals_root(root)


def package_root(root: str | Path | None = None) -> Path:
    return store_root(root) / PACKAGE


def registry_path(root: str | Path | None = None) -> Path:
    return store_root(root) / "registry.json"


def catalog_cache_path(root: str | Path | None = None) -> Path:
    return store_root(root) / "catalog.json"


def operation_path(root: str | Path | None, name: str) -> Path:
    return store_root(root) / "operations" / f"{name}.json"


def log_path(root: str | Path | None, name: str) -> Path:
    return store_root(root) / "logs" / f"{name}.log"


def managed_by_host(env: Any = None) -> bool:
    """Hosted deployments own installation; the operator may only enable/disable."""
    environment = os.environ if env is None else env
    return bool(environment.get("ARGUS_TRIAL_HARNESS")) or bool(environment.get(HOST_ROOT_ENV))


def preinstalled_names(env: Any = None) -> list[str]:
    """The names ``ARGUS_VERTICALS_PREINSTALL`` declares, each once, in order."""
    raw = (os.environ if env is None else env).get(PREINSTALL_ENV, "")
    names: list[str] = []
    for part in str(raw).split(","):
        part = part.strip().lower()
        if part and part not in names:
            names.append(part)
    return names


def valid_module_name(module: str) -> bool:
    return bool(_MODULE.fullmatch(module))


def _valid_name(name: object) -> str:
    cleaned = name.strip().lower() if isinstance(name, str) else ""
    if not _NAME.fullmatch(cleaned):
        raise UnknownVerticalError(f"{name!r} is not a valid vertical name")
    return cleaned


def _builtin_names() -> frozenset[str]:
    from . import builtin_verticals

    return frozenset(builtin_verticals())


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


def _empty_registry() -> dict[str, Any]:
    return {"schema": REGISTRY_SCHEMA, "verticals": {}, "shared": {}}


def registry(root: str | Path | None = None) -> dict[str, Any]:
    """``registry.json`` normalised; an unreadable file is an error, not an empty store."""
    path = registry_path(root)
    try:
        data = read_json(path, {})
    except ValueError as exc:
        raise VerticalStoreError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise VerticalStoreError(f"{path} must contain a JSON object")
    if not data:
        return _empty_registry()
    if data.get("schema") != REGISTRY_SCHEMA:
        raise VerticalStoreError(f"{path} has schema {data.get('schema')!r}; this Argus reads schema {REGISTRY_SCHEMA}")
    raw_verticals = data.get("verticals")
    raw_shared = data.get("shared")
    verticals: dict[Any, Any] = raw_verticals if isinstance(raw_verticals, dict) else {}
    shared: dict[Any, Any] = raw_shared if isinstance(raw_shared, dict) else {}
    return {
        "schema": REGISTRY_SCHEMA,
        "verticals": {
            name: dict(entry) for name, entry in verticals.items()
            if isinstance(name, str) and _NAME.fullmatch(name) and isinstance(entry, dict)
        },
        "shared": {
            tree: {
                "owners": [o for o in (entry.get("owners") or []) if isinstance(o, str)],
                "sha256s": dict(entry.get("sha256s") or {}),
            }
            for tree, entry in shared.items()
            if isinstance(tree, str) and _TREE.fullmatch(tree) and isinstance(entry, dict)
        },
    }


def installed(root: str | Path | None = None) -> dict[str, dict[str, Any]]:
    return registry(root)["verticals"]


def enabled_entries(root: str | Path | None = None) -> dict[str, dict[str, Any]]:
    return {name: entry for name, entry in installed(root).items() if entry.get("enabled")}


def registry_signature(root: str | Path | None = None) -> tuple[int, int, int] | None:
    """Identity of the registry file on disk (``None`` when nothing is installed)."""
    try:
        stat = registry_path(root).stat()
    except FileNotFoundError:
        return None
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


@contextmanager
def _locked(root: str | Path | None) -> Iterator[None]:
    store = store_root(root)
    try:
        store.mkdir(parents=True, exist_ok=True)
        lock = portalocker.Lock(str(store / "store.lock"), timeout=30)
        lock.acquire()
    except (OSError, portalocker.exceptions.LockException) as exc:
        raise VerticalStoreError(f"the vertical store at {store} is not writable: {exc}") from exc
    try:
        yield
    finally:
        lock.release()


def _save_registry(root: str | Path | None, data: dict[str, Any]) -> None:
    try:
        write_json(registry_path(root), data)
    except OSError as exc:
        raise VerticalStoreError(f"registry.json could not be written: {exc}") from exc


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #


def catalog_source() -> str:
    return os.environ.get(CATALOG_ENV, "").strip() or DEFAULT_CATALOG_URL


def _local_path(source: str) -> Path | None:
    """The filesystem path a local source names; ``None`` for an https URL."""
    if source.startswith("https://"):
        return None
    if source.startswith("file://"):
        parsed = urlparse(source)
        return Path(url2pathname(unquote(parsed.path)))
    if "://" in source:
        raise VerticalStoreError(f"unsupported catalog source {source!r}: use https, file:// or a path")
    return Path(source).expanduser()


def _check_https(url: str, *, what: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise VerticalStoreError(f"{what} must be an https URL without credentials: {url}")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise VerticalStoreError(
            f"{what} host {parsed.hostname!r} is not allowed (allowed: {', '.join(sorted(ALLOWED_HOSTS))})"
        )


def _https_stream(url: str, sink: Callable[[bytes], object], *, limit: int, what: str) -> None:
    """GET ``url`` following at most eight redirects, every hop on an allowed host."""
    _check_https(url, what=what)
    current = url
    try:
        with httpx.Client(timeout=90) as client:
            for _ in range(8):
                with client.stream("GET", current, follow_redirects=False) as response:
                    if response.is_redirect:
                        target = str(response.url.join(response.headers.get("location", "")))
                        _check_https(target, what=f"{what} redirect")
                        current = target
                        continue
                    response.raise_for_status()
                    size = 0
                    for part in response.iter_bytes():
                        size += len(part)
                        if size > limit:
                            raise VerticalStoreError(f"{what} exceeds the {limit // (1024 * 1024)} MiB limit")
                        sink(part)
                    return
    except httpx.HTTPError as exc:
        raise VerticalStoreError(f"{what} could not be downloaded from {current}: {exc}") from exc
    raise VerticalStoreError(f"{what}: too many redirects from {url}")


def _fetch_catalog(source: str) -> dict[str, Any]:
    local = _local_path(source)
    if local is not None:
        try:
            text = local.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise VerticalStoreError(f"catalog {local} is unreadable: {exc}") from exc
    else:
        chunks: list[bytes] = []
        _https_stream(source, chunks.append, limit=CATALOG_LIMIT, what="catalog")
        try:
            text = b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VerticalStoreError(f"catalog {source} is not UTF-8: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise VerticalStoreError(f"catalog {source} is not valid JSON: {exc}") from exc
    return validate_catalog(data, local_source=local is not None)


def _string_list(value: object, *, where: str, pattern: re.Pattern[str] | None = None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise VerticalStoreError(f"{where} must be a list of strings")
    if pattern is not None:
        for item in value:
            if not pattern.fullmatch(item):
                raise VerticalStoreError(f"{where} contains an invalid entry {item!r}")
    return list(value)


def _validate_entry(name: str, raw: object, *, local_source: bool) -> dict[str, Any]:
    where = f"catalog vertical {name!r}"
    if not isinstance(raw, dict):
        raise VerticalStoreError(f"{where} must be an object")
    if not _NAME.fullmatch(name) or raw.get("name") != name:
        raise VerticalStoreError(f"{where}: invalid or mismatched name")
    version = raw.get("version")
    if not isinstance(version, str) or not version.strip() or "/" in version or "\\" in version:
        raise VerticalStoreError(f"{where}: invalid version {version!r}")
    paths = _string_list(raw.get("paths"), where=f"{where}.paths", pattern=_TREE)
    if not paths:
        raise VerticalStoreError(f"{where}: paths must name the vertical's directory")
    module = raw.get("module")
    if not isinstance(module, str) or not _MODULE.fullmatch(module):
        raise VerticalStoreError(f"{where}: invalid module {module!r}")
    if module != paths[0].replace("/", ".") + ".stages":
        raise VerticalStoreError(f"{where}: module {module} does not live in paths[0] {paths[0]}")
    shared = _string_list(raw.get("shared"), where=f"{where}.shared", pattern=_TREE)
    for tree in shared:
        if any(tree == p or tree.startswith(p + "/") for p in paths):
            raise VerticalStoreError(f"{where}: shared tree {tree} lies inside the vertical's own paths")
    requires = _string_list(raw.get("requires"), where=f"{where}.requires", pattern=_NAME)
    if name in requires:
        raise VerticalStoreError(f"{where}: requires itself")
    purpose = raw.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        raise VerticalStoreError(f"{where}: purpose is missing")
    purpose_zh = raw.get("purpose_zh")
    if purpose_zh is not None and (not isinstance(purpose_zh, str) or not purpose_zh.strip()):
        raise VerticalStoreError(f"{where}: purpose_zh must be a non-empty string when present")
    archive = raw.get("archive")
    if not isinstance(archive, dict):
        raise VerticalStoreError(f"{where}: archive is missing (this catalog is an index, not a release)")
    file_name = archive.get("file")
    if not isinstance(file_name, str) or not _ARCHIVE_FILE.fullmatch(file_name):
        raise VerticalStoreError(f"{where}: invalid archive file name {file_name!r}")
    sha256 = archive.get("sha256")
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise VerticalStoreError(f"{where}: archive sha256 is missing or malformed")
    size = archive.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size > ARCHIVE_LIMIT:
        raise VerticalStoreError(f"{where}: archive size {size!r} is invalid")
    url = archive.get("url")
    if not isinstance(url, str) or not url:
        raise VerticalStoreError(f"{where}: archive url is missing")
    if url.startswith("https://"):
        _check_https(url, what=f"{where} archive url")
    elif not local_source:
        raise VerticalStoreError(f"{where}: archive url {url!r} is not https (local archives need a local catalog)")
    else:
        _local_path(url)  # raises on an unsupported scheme
    api_version = raw.get("api_version", 1)
    if not isinstance(api_version, int) or isinstance(api_version, bool):
        raise VerticalStoreError(f"{where}: api_version must be an integer")
    size_bytes = raw.get("size_bytes", 0)
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
        size_bytes = 0
    return {
        "name": name,
        "version": version,
        "module": module,
        "purpose": " ".join(purpose.split()),
        "purpose_zh": " ".join(purpose_zh.split()) if isinstance(purpose_zh, str) else None,
        "paths": paths,
        "requires": requires,
        "shared": shared,
        "python_requirements": _string_list(raw.get("python_requirements"), where=f"{where}.python_requirements"),
        "optional_python_requirements": _string_list(
            raw.get("optional_python_requirements"), where=f"{where}.optional_python_requirements"
        ),
        "tags": _string_list(raw.get("tags"), where=f"{where}.tags"),
        "skill_parents": _string_list(raw.get("skill_parents"), where=f"{where}.skill_parents", pattern=_NAME),
        "has_skills": bool(raw.get("has_skills", False)),
        "size_bytes": size_bytes,
        "api_version": api_version,
        "min_argus": str(raw.get("min_argus") or ""),
        "maintainers": _string_list(raw.get("maintainers"), where=f"{where}.maintainers"),
        "archive": {"file": file_name, "url": url, "sha256": sha256, "size": size},
    }


def validate_catalog(data: object, *, local_source: bool) -> dict[str, Any]:
    """Return a normalised catalog or raise ``VerticalStoreError`` naming the fault."""
    if not isinstance(data, dict):
        raise VerticalStoreError("catalog must be a JSON object")
    if data.get("schema") != CATALOG_SCHEMA:
        raise VerticalStoreError(f"catalog schema {data.get('schema')!r} is not {CATALOG_SCHEMA}")
    raw_verticals = data.get("verticals")
    if not isinstance(raw_verticals, dict):
        raise VerticalStoreError("catalog has no verticals object")
    verticals = {
        str(name): _validate_entry(str(name), entry, local_source=local_source)
        for name, entry in raw_verticals.items()
    }
    for name, entry in verticals.items():
        for required in entry["requires"]:
            if required not in verticals:
                raise VerticalStoreError(f"catalog vertical {name!r} requires unknown vertical {required!r}")
    raw_release = data.get("release")
    release: dict[Any, Any] = raw_release if isinstance(raw_release, dict) else {}
    tag = release.get("tag")
    raw_generated = data.get("generated_from")
    generated: dict[Any, Any] = raw_generated if isinstance(raw_generated, dict) else {}
    return {
        "schema": CATALOG_SCHEMA,
        "generated_from": {
            "repo": str(generated.get("repo") or ""),
            "commit": str(generated.get("commit") or ""),
        },
        "release": {"tag": tag if isinstance(tag, str) else None},
        "verticals": verticals,
    }


def load_catalog(
    root: str | Path | None = None,
    *,
    refresh: bool = False,
    max_age: timedelta = CATALOG_MAX_AGE,
) -> dict[str, Any]:
    """Return ``{"fetched_at", "source", "catalog", "error"}``.

    A fresh enough cache for the same source is served as is. When the fetch
    fails and a stale cache exists, the cache is returned with ``error`` set;
    with no usable cache the failure is raised.
    """
    source = catalog_source()
    local_source = _local_path(source) is not None
    cache: dict[str, Any] = read_json(catalog_cache_path(root), {})
    if not isinstance(cache, dict):
        cache = {}
    cached: dict[str, Any] | None = None
    if cache.get("source") == source and isinstance(cache.get("catalog"), dict):
        try:
            cached = validate_catalog(cache["catalog"], local_source=local_source)
        except VerticalStoreError:
            cached = None
    if cached is not None and not refresh:
        age = time.time() - float(cache.get("fetched_at") or 0)
        if 0 <= age <= max_age.total_seconds():
            return {"fetched_at": cache.get("fetched_at"), "source": source, "catalog": cached, "error": ""}
    try:
        catalog = _fetch_catalog(source)
    except VerticalStoreError as exc:
        if cached is not None:
            log.warning("serving the cached vertical catalog: %s", exc)
            return {"fetched_at": cache.get("fetched_at"), "source": source, "catalog": cached, "error": str(exc)}
        raise
    payload = {"fetched_at": time.time(), "source": source, "catalog": catalog}
    try:
        write_json(catalog_cache_path(root), payload)
    except OSError as exc:  # a read-only host root still serves the fetched catalog
        log.warning("vertical catalog cache could not be written: %s", exc)
    return {**payload, "error": ""}


def _closure(catalog: dict[str, Any], names: Iterable[str]) -> list[str]:
    """Install order: dependencies first, each name once; unknown names and cycles are errors."""
    verticals = catalog["verticals"]
    order: list[str] = []
    visiting: list[str] = []

    def visit(name: str) -> None:
        if name in order:
            return
        if name not in verticals:
            raise UnknownVerticalError(f"unknown vertical {name!r}: it is not in the catalog")
        if name in visiting:
            raise VerticalStoreError("requires cycle in the catalog: " + " -> ".join([*visiting, name]))
        visiting.append(name)
        for dependency in verticals[name]["requires"]:
            visit(dependency)
        visiting.pop()
        order.append(name)

    for name in names:
        visit(name)
    return order


def _is_current(entry: dict[str, Any], spec: dict[str, Any]) -> bool:
    return entry.get("version") == spec["version"] and entry.get("sha256") == spec["archive"]["sha256"]


# --------------------------------------------------------------------------- #
# Archives
# --------------------------------------------------------------------------- #


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_digest(root: Path) -> str:
    """Content digest of a directory: sorted relative paths and file digests."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _archive_origin(spec: dict[str, Any], source: str) -> Path | str:
    """A local archive next to a local catalog (an offline mirror), a local URL, or the https URL."""
    archive = spec["archive"]
    local_catalog = _local_path(source)
    if local_catalog is not None:
        sibling = local_catalog.parent / archive["file"]
        if sibling.is_file():
            return sibling
        if not archive["url"].startswith("https://"):
            local = _local_path(archive["url"])
            if local is not None:
                return local
    _check_https(archive["url"], what=f"{spec['name']} archive url")
    return archive["url"]


def _fetch_archive(spec: dict[str, Any], destination: Path, source: str) -> None:
    name = spec["name"]
    origin = _archive_origin(spec, source)
    if isinstance(origin, Path):
        try:
            if origin.stat().st_size > ARCHIVE_LIMIT:
                raise VerticalStoreError(f"{name}: archive {origin} exceeds the size limit")
            shutil.copyfile(origin, destination)
        except OSError as exc:
            raise VerticalStoreError(f"{name}: archive {origin} is unreadable: {exc}") from exc
    else:
        with destination.open("wb") as output:
            _https_stream(origin, output.write, limit=ARCHIVE_LIMIT, what=f"{name} archive")
    actual = destination.stat().st_size
    if actual != spec["archive"]["size"]:
        raise VerticalStoreError(
            f"{name}: archive is {actual} bytes, the catalog says {spec['archive']['size']}; nothing was installed"
        )
    digest = _sha256_file(destination)
    if digest != spec["archive"]["sha256"]:
        raise VerticalStoreError(f"{name}: archive sha256 {digest[:12]}... does not match the catalog; nothing was installed")


def _extract(archive: Path, destination: Path, *, trees: list[str], name: str) -> None:
    """Extract only after every member is checked against the declared trees."""
    prefixes = tuple(f"{tree}/" for tree in trees)
    root = destination.resolve()
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if sum(info.file_size for info in infos) > EXPANDED_LIMIT:
                raise VerticalStoreError(f"{name}: archive expands beyond the size limit")
            for info in infos:
                member = info.filename
                parts = member.split("/")
                if (
                    not member
                    or member.startswith("/")
                    or "\\" in member
                    or any(part in {"", ".", ".."} for part in parts[:-1])
                    or parts[-1] in {".", ".."}
                    or not member.startswith(prefixes)
                ):
                    raise VerticalStoreError(f"{name}: archive member outside the vertical's trees: {member}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise VerticalStoreError(f"{name}: archive contains a symlink: {member}")
                resolved = (destination / member).resolve()
                if root != resolved and root not in resolved.parents:
                    raise VerticalStoreError(f"{name}: archive member escapes the destination: {member}")
            if zf.testzip() is not None:
                raise VerticalStoreError(f"{name}: archive is corrupt; nothing was installed")
            zf.extractall(destination)
    except zipfile.BadZipFile as exc:
        raise VerticalStoreError(f"{name}: archive is not a valid zip: {exc}") from exc


def _verify_tree(tree_root: Path, spec: dict[str, Any]) -> None:
    name = spec["name"]
    stages = tree_root / spec["paths"][0] / "stages.py"
    if not stages.is_file():
        raise VerticalStoreError(f"{name}: archive has no {spec['paths'][0]}/stages.py")
    for tree in [*spec["paths"], *spec["shared"]]:
        if not (tree_root / tree).is_dir():
            raise VerticalStoreError(f"{name}: archive does not contain the declared tree {tree}")


def _nested_trees(data: dict[str, Any], tree: str, *, exclude: str) -> list[str]:
    """Registered trees of *other* verticals that lie strictly inside ``tree``."""
    nested: list[str] = []
    for other, entry in data["verticals"].items():
        if other == exclude:
            continue
        for candidate in entry.get("paths", []):
            if candidate.startswith(tree + "/") and candidate not in nested:
                nested.append(candidate)
    for candidate, owners in data["shared"].items():
        if candidate.startswith(tree + "/") and owners["owners"] and candidate not in nested:
            nested.append(candidate)
    return nested


def _place(
    root: str | Path | None,
    spec: dict[str, Any],
    tree_root: Path,
    staging: Path,
    catalog: dict[str, Any],
) -> None:
    """Swap the extracted trees into the store and record them; all or nothing."""
    name = spec["name"]
    store = store_root(root)
    trees = [*spec["paths"], *spec["shared"]]
    replaced_dir = staging / "replaced"
    backups: list[tuple[Path, Path]] = []
    moved: list[Path] = []
    with _locked(root):
        data = registry(root)
        try:
            for index, tree in enumerate(trees):
                target = store / tree
                source = tree_root / tree
                target.parent.mkdir(parents=True, exist_ok=True)
                nested = _nested_trees(data, tree, exclude=name)
                backup: Path | None = None
                if target.exists():
                    backup = replaced_dir / str(index)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, backup)
                    backups.append((target, backup))
                os.replace(source, target)
                moved.append(target)
                if backup is not None:
                    for inner in nested:  # another vertical's directory inside this tree survives the swap
                        relative = inner[len(tree) + 1:]
                        kept = backup / relative
                        if kept.is_dir():
                            shutil.rmtree(target / relative, ignore_errors=True)
                            (target / relative).parent.mkdir(parents=True, exist_ok=True)
                            os.replace(kept, target / relative)
            previous = data["verticals"].get(name, {})
            data["verticals"][name] = {
                "version": spec["version"],
                "sha256": spec["archive"]["sha256"],
                "module": spec["module"],
                "source": {
                    "repo": catalog["generated_from"]["repo"],
                    "tag": catalog["release"]["tag"],
                    "url": spec["archive"]["url"],
                },
                "enabled": bool(previous.get("enabled", True)),
                "installed_at": time.time(),
                "requires": list(spec["requires"]),
                "shared": list(spec["shared"]),
                "paths": list(spec["paths"]),
            }
            for tree in spec["shared"]:
                owners = data["shared"].setdefault(tree, {"owners": [], "sha256s": {}})
                if name not in owners["owners"]:
                    owners["owners"].append(name)
                owners["sha256s"][name] = _tree_digest(store / tree)
            _save_registry(root, data)
        except Exception:
            for target in moved:
                shutil.rmtree(target, ignore_errors=True)
            for target, backup in backups:
                shutil.rmtree(target, ignore_errors=True)
                os.replace(backup, target)
            raise
    _after_change(trees)


def _purge_modules(trees: Iterable[str]) -> None:
    prefixes = [tree.replace("/", ".") for tree in trees]
    for module_name in list(sys.modules):
        if any(module_name == p or module_name.startswith(p + ".") for p in prefixes):
            sys.modules.pop(module_name, None)


def _after_change(trees: Iterable[str]) -> None:
    _purge_modules(trees)
    importlib.invalidate_caches()
    from ._registry import refresh_vertical_plugins

    refresh_vertical_plugins()


def _append_log(root: str | Path | None, name: str, action: str, message: str) -> None:
    try:
        path = log_path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {action} {message}\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #


def _job_alive(root: str | Path | None, name: str, op: dict[str, Any]) -> bool:
    if op.get("pid") == os.getpid():
        thread = _jobs.get((str(store_root(root)), name))
        return bool(thread and (thread.is_alive() or thread.ident is None))
    return process_identity_is_running(int(op.get("pid") or 0), op.get("identity"))


def operation(name: str, root: str | Path | None = None) -> dict[str, Any] | None:
    """The running or last operation for ``name``; a dead job is marked failed."""
    try:
        name = _valid_name(name)
    except UnknownVerticalError:
        return None
    path = operation_path(root, name)
    op = read_json(path, {})
    if not isinstance(op, dict) or not op:
        return None
    if op.get("status") == "running" and not _job_alive(root, name, op):
        op.update(status="failed", finished=time.time(),
                  message="the process running this operation ended before it finished")
        try:
            write_json(path, op)
        except OSError:
            pass
    op.pop("identity", None)
    return op


def _write_operation(root: str | Path | None, name: str, op: dict[str, Any]) -> None:
    write_json(operation_path(root, name), op)


def _start_job(
    root: str | Path | None,
    name: str,
    action: str,
    target: Callable[..., None],
    args: tuple[Any, ...],
) -> dict[str, Any]:
    with _jobs_lock:
        current = operation(name, root)
        if current is not None and current.get("status") == "running":
            raise VerticalStoreError(f"{name}: a {current.get('action')} operation is already running")
        op: dict[str, Any] = {
            "status": "running", "action": action, "progress": 0, "message": "starting",
            "started": time.time(), "finished": None, "pid": os.getpid(),
            "identity": capture_process_identity(os.getpid()),
        }
        try:
            _write_operation(root, name, op)
        except OSError as exc:
            raise VerticalStoreError(f"the vertical store at {store_root(root)} is not writable: {exc}") from exc
        thread = threading.Thread(
            target=_run_job, args=(root, name, action, op, target, args),
            daemon=True, name=f"vertical-{action}-{name}",
        )
        _jobs[(str(store_root(root)), name)] = thread
        try:
            thread.start()
        except RuntimeError as exc:
            _jobs.pop((str(store_root(root)), name), None)
            op.update(status="failed", finished=time.time(), message="the job thread could not start")
            _write_operation(root, name, op)
            raise VerticalStoreError(f"{name}: the {action} job could not start") from exc
    return {k: v for k, v in op.items() if k != "identity"}


def _run_job(
    root: str | Path | None,
    name: str,
    action: str,
    op: dict[str, Any],
    target: Callable[..., None],
    args: tuple[Any, ...],
) -> None:
    def progress(percent: int, message: str) -> None:
        op["progress"] = max(0, min(100, int(percent)))
        op["message"] = message
        _append_log(root, name, action, f"{op['progress']:3d}% {message}")
        _write_operation(root, name, op)

    _append_log(root, name, action, "started")
    try:
        target(*args, progress=progress)
        op.update(status="done", progress=100, message=f"{action} finished")
    except VerticalStoreError as exc:
        op.update(status="failed", message=str(exc))
        log.warning("vertical %s %s failed: %s", name, action, exc)
    except Exception as exc:  # noqa: BLE001 - the job must always record its end
        op.update(status="failed", message=f"{type(exc).__name__}: {exc}")
        log.warning("vertical %s %s failed", name, action, exc_info=True)
    finally:
        op["finished"] = time.time()
        _append_log(root, name, action, f"{op['status']}: {op['message']}")
        try:
            _write_operation(root, name, op)
        except OSError:
            log.warning("vertical %s: operation record could not be written", name, exc_info=True)


def wait_for_operation(
    name: str,
    root: str | Path | None = None,
    timeout: float = 3600,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Block until ``name``'s operation ends; ``on_progress`` sees every change."""
    name = _valid_name(name)
    deadline = time.monotonic() + timeout
    last: tuple[Any, Any] | None = None
    while True:
        op = operation(name, root)
        if op is None:
            raise VerticalStoreError(f"{name}: no operation is recorded")
        marker = (op.get("progress"), op.get("message"))
        if on_progress is not None and marker != last:
            last = marker
            on_progress(op)
        if op.get("status") != "running":
            return op
        if time.monotonic() >= deadline:
            raise VerticalStoreError(f"{name}: the {op.get('action')} did not finish within {timeout:.0f} seconds")
        thread = _jobs.get((str(store_root(root)), name))
        if thread is not None and thread.is_alive():
            thread.join(0.2)
        else:
            time.sleep(0.2)


def _finish(
    name: str,
    root: str | Path | None,
    op: dict[str, Any],
    *,
    wait: bool,
    timeout: float,
    on_progress: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    if not wait:
        return op
    final = wait_for_operation(name, root, timeout, on_progress)
    if final.get("status") != "done":
        raise VerticalStoreError(str(final.get("message") or f"{name}: {op['action']} failed"))
    return final


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #


def _install_one(
    root: str | Path | None,
    spec: dict[str, Any],
    catalog: dict[str, Any],
    source: str,
    step: Callable[[float, str], None],
) -> None:
    name = spec["name"]
    if spec["api_version"] != 1:
        raise VerticalStoreError(f"{name}: vertical API version {spec['api_version']} is not supported by this Argus")
    store = store_root(root)
    try:
        staging = store / ".staging" / f"{name}-{uuid.uuid4().hex[:8]}"
        staging.mkdir(parents=True)
    except OSError as exc:
        raise VerticalStoreError(f"the vertical store at {store} is not writable: {exc}") from exc
    try:
        step(0.05, "downloading")
        archive = staging / spec["archive"]["file"]
        _fetch_archive(spec, archive, source)
        step(0.55, "verifying")
        tree_root = staging / "tree"
        tree_root.mkdir()
        _extract(archive, tree_root, trees=[*spec["paths"], *spec["shared"]], name=name)
        _verify_tree(tree_root, spec)
        step(0.8, "installing")
        _place(root, spec, tree_root, staging, catalog)
        step(1.0, f"installed {spec['version']}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _install_job(
    root: str | Path | None,
    todo: list[str],
    catalog: dict[str, Any],
    source: str,
    *,
    progress: Callable[[int, str], None],
) -> None:
    total = max(1, len(todo))
    for index, member in enumerate(todo):
        base = index * 100 / total

        def step(fraction: float, message: str, base: float = base, member: str = member) -> None:
            progress(int(base + fraction * 100 / total), f"{member}: {message}")

        _install_one(root, catalog["verticals"][member], catalog, source, step)


def _begin_install(
    name: str,
    root: str | Path | None,
    *,
    action: str,
    wait: bool,
    timeout: float,
    on_progress: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    if name in _builtin_names():
        raise VerticalStoreError(f"{name} is a built-in vertical; it ships with Argus and cannot be installed from the store")
    loaded = load_catalog(root)
    catalog = loaded["catalog"]
    if name not in catalog["verticals"]:
        raise UnknownVerticalError(f"unknown vertical {name!r}: it is not in the catalog ({loaded['source']})")
    spec = catalog["verticals"][name]
    present = installed(root)
    if action == "install" and name in present and _is_current(present[name], spec):
        state = "" if present[name].get("enabled") else " (disabled: enable it instead)"
        raise VerticalStoreError(f"{name} {spec['version']} is already installed{state}")
    if action == "update":
        if name not in present:
            raise UnknownVerticalError(f"{name} is not installed")
        if _is_current(present[name], spec):
            raise VerticalStoreError(f"{name} {spec['version']} is already current")
    order = _closure(catalog, [name])
    todo = [
        member for member in order
        if member == name or member not in present or not _is_current(present[member], catalog["verticals"][member])
    ]
    op = _start_job(root, name, action, _install_job, (root, todo, catalog, loaded["source"]))
    return _finish(name, root, op, wait=wait, timeout=timeout, on_progress=on_progress)


def install(
    name: str,
    root: str | Path | None = None,
    *,
    wait: bool = False,
    timeout: float = 3600,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Install ``name`` and whatever it ``requires`` (dependencies first) as one job."""
    name = _valid_name(name)
    if managed_by_host():
        raise VerticalStoreError(HOST_MANAGED)
    return _begin_install(name, root, action="install", wait=wait, timeout=timeout, on_progress=on_progress)


def update(
    name: str,
    root: str | Path | None = None,
    *,
    wait: bool = False,
    timeout: float = 3600,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Reinstall ``name`` from the catalog when its version or digest changed."""
    name = _valid_name(name)
    if managed_by_host():
        raise VerticalStoreError(HOST_MANAGED)
    return _begin_install(name, root, action="update", wait=wait, timeout=timeout, on_progress=on_progress)


def set_enabled(name: str, enabled: bool, root: str | Path | None = None) -> dict[str, Any]:
    name = _valid_name(name)
    with _locked(root):
        data = registry(root)
        if name not in data["verticals"]:
            raise UnknownVerticalError(f"{name} is not installed")
        data["verticals"][name]["enabled"] = bool(enabled)
        _save_registry(root, data)
        entry = dict(data["verticals"][name])
    _after_change(entry.get("paths", []))
    return entry


def enable(name: str, root: str | Path | None = None) -> dict[str, Any]:
    return set_enabled(name, True, root)


def disable(name: str, root: str | Path | None = None) -> dict[str, Any]:
    return set_enabled(name, False, root)


def _sessions_by_vertical(root: str | Path | None) -> dict[str, list[str]]:
    """Session ids grouped by the vertical their ``PIPELINE_STATE.json`` names."""
    sessions = core_paths.session_states_root(root)
    result: dict[str, list[str]] = {}
    try:
        directories = sorted(p for p in sessions.iterdir() if p.is_dir())
    except OSError:
        return result
    for directory in directories:
        try:
            payload = read_pipeline_state(directory)
        except (OSError, ValueError):
            continue
        raw = payload.get("vertical")
        if not isinstance(raw, str):
            continue
        cleaned = raw.strip().lower()
        if cleaned.endswith("-needed"):
            cleaned = cleaned[: -len("-needed")]
        if cleaned:
            result.setdefault(cleaned, []).append(directory.name)
    return result


def used_by(name: str, root: str | Path | None = None) -> list[str]:
    """Session ids whose persisted pipeline state names ``name`` as its vertical."""
    return list(_sessions_by_vertical(root).get(_valid_name(name), []))


def _uninstall_job(root: str | Path | None, name: str, *, progress: Callable[[int, str], None]) -> None:
    store = store_root(root)
    with _locked(root):
        data = registry(root)
        entry = data["verticals"].get(name)
        if entry is None:
            raise UnknownVerticalError(f"{name} is not installed")
        trees = [*entry.get("paths", []), *entry.get("shared", [])]
        for tree in entry.get("paths", []):
            if _nested_trees(data, tree, exclude=name):
                raise VerticalStoreError(f"{name}: {tree} still contains another installed vertical")
        progress(20, "removing files")
        for tree in entry.get("paths", []):
            shutil.rmtree(store / tree, ignore_errors=True)
        for tree in entry.get("shared", []):
            owners = data["shared"].get(tree)
            if owners is None:
                continue
            owners["owners"] = [o for o in owners["owners"] if o != name]
            owners["sha256s"].pop(name, None)
            if not owners["owners"]:
                shutil.rmtree(store / tree, ignore_errors=True)
                del data["shared"][tree]
        progress(70, "updating the registry")
        del data["verticals"][name]
        _save_registry(root, data)
        _prune_empty_parents(store, trees)
    _after_change(trees)
    progress(100, "removed")


def _prune_empty_parents(store: Path, trees: Iterable[str]) -> None:
    package = store / PACKAGE
    for tree in trees:
        current = (store / tree).parent
        while current != package and package in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent


def uninstall(
    name: str,
    root: str | Path | None = None,
    *,
    force: bool = False,
    wait: bool = False,
    timeout: float = 600,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Remove ``name``'s trees; shared trees go when their last owner does; dependencies stay."""
    name = _valid_name(name)
    if managed_by_host():
        raise VerticalStoreError(HOST_MANAGED)
    present = installed(root)
    if name not in present:
        raise UnknownVerticalError(f"{name} is not installed")
    dependents = sorted(other for other, entry in present.items() if name in entry.get("requires", []))
    if dependents:
        raise VerticalStoreError(
            f"{name} is required by installed vertical(s) {', '.join(dependents)}; remove them first"
        )
    users = used_by(name, root)
    if users and not force:
        raise VerticalStoreError(
            f"{name} is the vertical of session(s) {', '.join(users)}; pass force to remove it anyway"
        )
    op = _start_job(root, name, "uninstall", _uninstall_job, (root, name))
    return _finish(name, root, op, wait=wait, timeout=timeout, on_progress=on_progress)


def _preinstall_need(name: str, root: str | Path | None, catalog: dict[str, Any]) -> str | None:
    if name not in catalog["verticals"]:
        raise UnknownVerticalError(f"unknown vertical {name!r}: it is not in the catalog")
    entry = installed(root).get(name)
    if entry is None:
        return "install"
    if not _is_current(entry, catalog["verticals"][name]):
        return "update"
    if not entry.get("enabled"):
        return "enable"
    return None


def preinstall(
    root: str | Path | None = None,
    *,
    names: Iterable[str] | None = None,
    wait: bool = True,
    timeout: float = 3600,
    logger: logging.Logger | None = None,
) -> dict[str, dict[str, Any]]:
    """Bring every declared vertical to installed, current and enabled.

    Used by the web server at startup (on its own thread) and by
    ``release_tools.preinstall_verticals`` when an image is built. The host
    refusal does not apply here: this *is* the host preparing its root.
    """
    logger = logger or log
    results: dict[str, dict[str, Any]] = {}
    wanted = preinstalled_names() if names is None else [str(n) for n in names]
    if not wanted:
        return results
    try:
        catalog = load_catalog(root)["catalog"]
    except VerticalStoreError as exc:
        logger.warning("verticals cannot be prepared: %s", exc)
        return {name: {"status": "failed", "message": str(exc)} for name in wanted}
    for name in wanted:
        try:
            cleaned = _valid_name(name)
            need = _preinstall_need(cleaned, root, catalog)
            if need is None:
                logger.info("vertical %s is installed, current and enabled under %s", cleaned, store_root(root))
                results[name] = {"status": "ready"}
                continue
            if need == "enable":
                set_enabled(cleaned, True, root)
                results[name] = {"status": "done", "action": "enable"}
            else:
                op = _begin_install(cleaned, root, action=need, wait=wait, timeout=timeout, on_progress=None)
                results[name] = {"status": op["status"], "action": need, "message": op.get("message")}
            (logger.info if results[name]["status"] in {"done", "running"} else logger.warning)(
                "vertical %s: %s %s", cleaned, need, results[name]["status"]
            )
        except VerticalStoreError as exc:
            logger.warning("vertical %s could not be prepared: %s", name, exc)
            results[name] = {"status": "failed", "message": str(exc)}
    return results


# --------------------------------------------------------------------------- #
# Import plumbing used by the registry
# --------------------------------------------------------------------------- #


def ensure_importable(package_dir: Path) -> None:
    """Make ``argus_verticals`` resolve store trees under ``package_dir``.

    A pip-installed ``argus_verticals`` gets the store directory appended to
    its ``__path__`` (its own copies win for duplicate names); without one, a
    synthetic namespace package is registered in ``sys.modules``. Neither path
    reads dist-info, so the frozen desktop works the same way.
    """
    location = str(package_dir)
    module: ModuleType | None = sys.modules.get(PACKAGE)
    if module is None:
        try:
            spec = importlib.util.find_spec(PACKAGE)
        except (ImportError, ValueError):
            spec = None
        if spec is not None:
            module = importlib.import_module(PACKAGE)
    if module is not None:
        search_path = getattr(module, "__path__", None)
        if search_path is None:
            raise VerticalStoreError(f"{PACKAGE} is installed as a module, not a package; the store cannot extend it")
        if location not in list(search_path):
            search_path.append(location)
            importlib.invalidate_caches()
        return
    synthetic = ModuleType(PACKAGE)
    spec = importlib.machinery.ModuleSpec(PACKAGE, None, is_package=True)
    spec.submodule_search_locations = [location]
    synthetic.__spec__ = spec
    synthetic.__path__ = spec.submodule_search_locations
    synthetic.__file__ = None
    synthetic.__package__ = PACKAGE
    synthetic.__doc__ = "Synthetic package root registered by the Argus Vertical Store."
    sys.modules[PACKAGE] = synthetic
    importlib.invalidate_caches()


# --------------------------------------------------------------------------- #
# Rows: the merged view for the UI and the CLI
# --------------------------------------------------------------------------- #


def requirement_module(requirement: str) -> str | None:
    """The importable top-level module a PEP 508 requirement most likely provides."""
    match = _REQUIREMENT_NAME.match(requirement)
    if not match:
        return None
    project = match.group(1)
    return _MODULE_ALIASES.get(project.lower().replace("_", "-"), project.replace("-", "_"))


def missing_python(requirements: Iterable[str]) -> list[str]:
    """Requirement names whose module ``importlib.util.find_spec`` cannot find."""
    missing: list[str] = []
    for requirement in requirements:
        module = requirement_module(requirement)
        if module is None:
            continue
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError, AttributeError):
            available = True  # an odd name is not evidence of absence
        if not available:
            match = _REQUIREMENT_NAME.match(requirement)
            missing.append(match.group(1) if match else requirement)
    return missing


def _actions(kind: str, *, entry: dict[str, Any] | None, spec: dict[str, Any] | None,
             update_available: bool, host: bool, running: bool) -> list[str]:
    if kind == "builtin" or running:
        return []
    if kind == "package":
        return ["uninstall"] if entry is not None and not host else []
    if kind == "installed":
        actions = ["disable" if entry and entry.get("enabled") else "enable"]
        if update_available and not host:
            actions.append("update")
        if not host:
            actions.append("uninstall")
        return actions
    return ["install"] if spec is not None and not host else []


def rows(root: str | Path | None = None, *, catalog: dict[str, Any] | None = _UNSET) -> list[dict[str, Any]]:
    """One row per vertical name across built-ins, pip package, store and catalog."""
    from ..skills.vertical_select import VERTICAL_PURPOSES, VERTICALS
    from ._registry import vertical_plugins

    host = managed_by_host()
    if catalog is _UNSET:
        try:
            catalog = load_catalog(root)["catalog"]
        except VerticalStoreError as exc:
            log.warning("vertical catalog unavailable: %s", exc)
            catalog = None
    specs: dict[str, dict[str, Any]] = catalog["verticals"] if catalog else {}
    try:
        present = installed(root)
    except VerticalStoreError as exc:
        log.warning("vertical store registry unreadable: %s", exc)
        present = {}
    try:
        plugins = vertical_plugins()
    except Exception:  # noqa: BLE001 - a broken plugin must not blank the store page
        log.warning("vertical plugin discovery failed", exc_info=True)
        plugins = {}
    packaged = {name: plugin for name, plugin in plugins.items() if plugin.origin == "entry_point"}
    sessions = _sessions_by_vertical(root)
    result: list[dict[str, Any]] = []
    for name in VERTICALS:
        result.append({
            "name": name, "purpose": VERTICAL_PURPOSES.get(name, ""), "purpose_zh": None,
            "kind": "builtin", "version": None, "installed_version": None, "enabled": True,
            "update_available": False, "requires": [], "shared": [], "python_requirements": [],
            "missing_python": [], "tags": [], "size_bytes": 0,
            "used_by": list(sessions.get(name, [])), "operation": None,
            "managed_by_host": host, "actions": [],
        })
    for name in sorted(set(present) | set(packaged) | set(specs)):
        if name in VERTICALS:
            continue
        spec = specs.get(name)
        entry = present.get(name)
        plugin = packaged.get(name)
        kind = "package" if plugin is not None else "installed" if entry is not None else "available"
        op = operation(name, root) if (entry is not None or spec is not None) else None
        update_available = bool(entry is not None and spec is not None and not _is_current(entry, spec))
        requirements = list(spec["python_requirements"]) if spec else []
        result.append({
            "name": name,
            "purpose": spec["purpose"] if spec else plugin.purpose if plugin else "",
            "purpose_zh": spec["purpose_zh"] if spec else None,
            "kind": kind,
            "version": spec["version"] if spec else None,
            "installed_version": entry.get("version") if entry else None,
            "enabled": True if kind == "package" else bool(entry and entry.get("enabled")),
            "update_available": update_available,
            "requires": list(spec["requires"]) if spec else list(entry.get("requires", [])) if entry else [],
            "shared": list(spec["shared"]) if spec else list(entry.get("shared", [])) if entry else [],
            "python_requirements": requirements,
            "missing_python": missing_python(requirements),
            "tags": list(spec["tags"]) if spec else [],
            "size_bytes": spec["size_bytes"] if spec else 0,
            "used_by": list(sessions.get(name, [])),
            "operation": op,
            "managed_by_host": host,
            "actions": _actions(
                kind, entry=entry, spec=spec, update_available=update_available, host=host,
                running=bool(op and op.get("status") == "running"),
            ),
        })
    return result


def overview(root: str | Path | None = None, *, refresh: bool = False) -> dict[str, Any]:
    """The ``GET /api/verticals`` payload: rows plus catalog and host status."""
    loaded: dict[str, Any] | None
    try:
        loaded = load_catalog(root, refresh=refresh)
        error = str(loaded.get("error") or "")
    except VerticalStoreError as exc:
        loaded = None
        error = str(exc)
    return {
        "verticals": rows(root, catalog=loaded["catalog"] if loaded else None),
        "catalog": {
            "source": catalog_source(),
            "fetched_at": loaded.get("fetched_at") if loaded else None,
            "release_tag": loaded["catalog"]["release"].get("tag") if loaded else None,
            "error": error,
        },
        "host": {"managed_by_host": managed_by_host(), "store_root": str(store_root(root))},
    }


__all__ = [
    "ACTIONS",
    "ALLOWED_HOSTS",
    "CATALOG_ENV",
    "DEFAULT_CATALOG_URL",
    "HOST_MANAGED",
    "HOST_ROOT_ENV",
    "JOB_ACTIONS",
    "KINDS",
    "PACKAGE",
    "PREINSTALL_ENV",
    "UnknownVerticalError",
    "VerticalStoreError",
    "catalog_source",
    "disable",
    "enable",
    "enabled_entries",
    "ensure_importable",
    "install",
    "installed",
    "load_catalog",
    "managed_by_host",
    "missing_python",
    "operation",
    "overview",
    "package_root",
    "preinstall",
    "preinstalled_names",
    "registry",
    "registry_signature",
    "rows",
    "set_enabled",
    "store_root",
    "uninstall",
    "update",
    "used_by",
    "valid_module_name",
    "validate_catalog",
    "wait_for_operation",
]
