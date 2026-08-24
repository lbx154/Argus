"""Small, dependency-free client for an existing Argus WebAPI.

The client deliberately does not follow redirects: forwarding an Authorization
header to a redirected host would be an easy way to leak a bearer token.
"""

from __future__ import annotations

import hashlib
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

ARGUS_WEBAPI_PROTOCOL_NAME = "argus.webapi"
ARGUS_WEBAPI_PROTOCOL_MAJOR = 1
ARGUS_SNAPSHOT_SCHEMA_VERSION = 7
DEFAULT_ARTIFACT_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_ARTIFACT_BATCH_MAX_BYTES = 128 * 1024 * 1024
DEFAULT_JSON_MAX_BYTES = 16 * 1024 * 1024
ARGUS_OPTIONAL_FEATURE_CAPABILITIES = {
    "daemon_commands": "daemon.command.v1",
    "snapshot_schema": "snapshot.schema.v1",
    "snapshot_budget": "snapshot.budget.v1",
    "recorded_usage": "usage.recorded.v2",
    "manager_sse": "manager.sse.v1",
}
ARGUS_LAUNCH_REQUIRED_CAPABILITIES = frozenset(
    {
        "daemon.admission.v1",
        "daemon.command.v1",
        "mission.view.v1",
        "research.events.v1",
        "snapshot.schema.v1",
    }
)
ARGUS_LAUNCH_INCOMPATIBLE_ERROR = (
    "Argus WebAPI is not launch-compatible: expected argus.webapi major 1 "
    "with daemon.admission.v1, daemon.command.v1, mission.view.v1, "
    "research.events.v1 and snapshot.schema.v1"
)
ARGUS_BACKEND_NOT_READY_ERROR = (
    "Argus WebAPI is reachable, but its configured model backend/provider did "
    "not pass /api/system/doctor"
)


class ArgusWebApiError(RuntimeError):
    """A transport, authentication, or response-contract failure."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ArgusDaemonCommandReceipt:
    """Security-relevant facts from one Argus daemon command receipt.

    Argus deliberately returns command failures as authenticated HTTP 2xx
    responses so callers can reconcile an idempotent ``command_id``.  HTTP
    success is therefore transport truth only; these fields are the execution
    truth Flywheel must check before changing a campaign state.
    """

    command_status: str | None
    rc: int | None
    command_id: str | None
    command_revision: int | None
    project_id: str | None
    spawned: bool
    already_alive: bool
    admission_required: bool
    error: str

    @property
    def activation_proven(self) -> bool:
        return self.spawned or self.already_alive

    def audit_payload(self) -> dict[str, Any]:
        """Return the bounded, non-secret receipt facts safe for event logs."""

        return {
            "command_id": self.command_id,
            "command_status": self.command_status,
            "command_revision": self.command_revision,
            "rc": self.rc,
            "argus_project_id": self.project_id,
            "spawned": self.spawned,
            "already_alive": self.already_alive,
            "admission_required": self.admission_required,
            "error": self.error,
        }


class ArgusDaemonCommandError(ArgusWebApiError):
    """A well-formed HTTP response that did not prove command application."""

    def __init__(
        self,
        message: str,
        *,
        receipt: ArgusDaemonCommandReceipt,
        outcome: str,
    ) -> None:
        super().__init__(message)
        self.receipt = receipt
        self.outcome = outcome


def parse_argus_daemon_command_receipt(
    payload: Mapping[str, Any],
) -> ArgusDaemonCommandReceipt:
    """Extract the current Argus command ACK without treating it as success."""

    nested_start = payload.get("start")
    if not isinstance(nested_start, Mapping):
        nested_start = {}
    nested_command = payload.get("command")
    if not isinstance(nested_command, Mapping):
        nested_command = {}

    raw_rc = payload.get("rc")
    rc = raw_rc if isinstance(raw_rc, int) and not isinstance(raw_rc, bool) else None
    raw_revision = payload.get("command_revision")
    revision = (
        raw_revision
        if isinstance(raw_revision, int) and not isinstance(raw_revision, bool)
        else None
    )
    project_id = next(
        (
            value.strip()
            for key in ("sid", "project_id", "session_id")
            if isinstance((value := payload.get(key)), str) and value.strip()
        ),
        None,
    )
    status = payload.get("command_status")
    command_status = status if isinstance(status, str) and status else None
    command_id = payload.get("command_id")
    if not isinstance(command_id, str) or not command_id:
        command_id = None

    admission_required = (
        payload.get("admission_required") is True
        or nested_start.get("admission_required") is True
    )
    error_candidates = (
        nested_start.get("error") if admission_required else None,
        payload.get("error"),
        nested_command.get("error"),
        nested_start.get("error"),
    )
    error = next(
        (
            value.strip()
            for value in error_candidates
            if isinstance(value, str) and value.strip()
        ),
        "",
    )
    return ArgusDaemonCommandReceipt(
        command_status=command_status,
        rc=rc,
        command_id=command_id,
        command_revision=revision,
        project_id=project_id,
        spawned=payload.get("spawned") is True,
        already_alive=(
            payload.get("already_alive") is True
            or nested_start.get("already_alive") is True
        ),
        admission_required=admission_required,
        error=error,
    )


def require_argus_daemon_command_applied(
    payload: Mapping[str, Any],
    *,
    operation: str,
    require_activation: bool = False,
) -> ArgusDaemonCommandReceipt:
    """Fail closed unless the Argus command ACK proves the requested effect."""

    receipt = parse_argus_daemon_command_receipt(payload)
    label = operation.strip() or "daemon command"
    if receipt.admission_required:
        detail = receipt.error or "Argus requires explicit daemon admission"
        raise ArgusDaemonCommandError(
            f"Argus {label} requires admission: {detail}",
            receipt=receipt,
            outcome="admission_required",
        )
    if receipt.command_status in {"failed", "rejected"}:
        detail = receipt.error or f"command_status={receipt.command_status}"
        raise ArgusDaemonCommandError(
            f"Argus {label} was {receipt.command_status}: {detail}",
            receipt=receipt,
            outcome=receipt.command_status,
        )
    if receipt.command_status != "applied":
        raise ArgusDaemonCommandError(
            f"Argus {label} receipt is inconclusive: command_status must be 'applied'",
            receipt=receipt,
            outcome="inconclusive",
        )
    if receipt.rc != 0:
        detail = receipt.error or "receipt must contain integer rc=0"
        raise ArgusDaemonCommandError(
            f"Argus {label} receipt is inconclusive: {detail}",
            receipt=receipt,
            outcome="inconclusive" if receipt.rc is None else "failed",
        )
    if require_activation and not receipt.activation_proven:
        raise ArgusDaemonCommandError(
            f"Argus {label} receipt did not prove spawned=true or already_alive=true",
            receipt=receipt,
            outcome="inconclusive",
        )
    return receipt


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, bytes]]
RawTransport = Callable[
    [str, str, Mapping[str, str], float],
    tuple[int, Mapping[str, str], Iterable[bytes]],
]


@dataclass(frozen=True)
class ConnectionTest:
    ok: bool
    authenticated: bool
    authentication_required: bool
    runtime: Mapping[str, Any]
    protocol: Mapping[str, Any]
    capabilities: tuple[str, ...]
    snapshot_schema_version: int | None
    feature_support: Mapping[str, bool] = field(default_factory=dict)
    backend_ready: bool = False
    doctor_generated_at: str | None = None
    doctor_summary: Mapping[str, Any] = field(default_factory=dict)

    def supports(self, capability: str) -> bool:
        """Feature-detect an advertised capability without making it launch-critical."""

        return capability in self.capabilities

    def supports_feature(self, feature: str) -> bool:
        capability = ARGUS_OPTIONAL_FEATURE_CAPABILITIES.get(feature)
        if capability is None:
            raise ValueError(f"unknown Argus feature: {feature}")
        if feature in self.feature_support:
            return bool(self.feature_support[feature])
        return self.supports(capability)


@dataclass(frozen=True)
class ArtifactDigest:
    """Integrity receipt produced while streaming one allowlisted artifact."""

    path: str
    size: int
    sha256: str
    content_type: str | None


@dataclass(frozen=True)
class ArtifactDownload(ArtifactDigest):
    content: bytes


@dataclass(frozen=True)
class EventCursor:
    """Opaque polling state for Argus' ordered tail endpoint.

    WebAPI 1.13 does not expose a server cursor for research events. Keeping
    ordered fingerprints lets Flywheel overlap successive tail windows without
    dropping two legitimate, identical adjacent events.
    """

    project_id: str
    view: str
    fingerprints: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventBatch:
    events: tuple[Mapping[str, Any], ...]
    cursor: EventCursor
    overlap_count: int
    gap_detected: bool


@dataclass(frozen=True)
class ArgusConnectionAssessment:
    """One authoritative launch/readiness interpretation of ``/api/meta``."""

    status: str
    error: str | None
    protocol_compatible: bool
    launch_compatible: bool
    missing_capabilities: tuple[str, ...]
    backend_ready: bool


def assess_argus_connection(tested: ConnectionTest) -> ArgusConnectionAssessment:
    """Classify authentication and the exact WebAPI launch contract.

    Keeping this interpretation beside the wire contract prevents interactive
    probes and periodic health polling from disagreeing about whether a target
    may admit a Flywheel campaign.
    """

    protocol = tested.protocol
    major = protocol.get("major")
    protocol_compatible = (
        protocol.get("name") == ARGUS_WEBAPI_PROTOCOL_NAME
        and isinstance(major, int)
        and not isinstance(major, bool)
        and major == ARGUS_WEBAPI_PROTOCOL_MAJOR
    )
    missing_capabilities = tuple(
        sorted(ARGUS_LAUNCH_REQUIRED_CAPABILITIES - set(tested.capabilities))
    )
    backend_ready = getattr(tested, "backend_ready", False) is True
    launch_compatible = (
        protocol_compatible and not missing_capabilities and backend_ready
    )
    if not tested.ok:
        status, error = "unauthorized", "Authentication failed"
    elif not protocol_compatible or missing_capabilities:
        status, error = "incompatible", ARGUS_LAUNCH_INCOMPATIBLE_ERROR
    elif not backend_ready:
        status, error = "incompatible", ARGUS_BACKEND_NOT_READY_ERROR
    else:
        status, error = "online", None
    return ArgusConnectionAssessment(
        status=status,
        error=error,
        protocol_compatible=protocol_compatible,
        launch_compatible=launch_compatible,
        missing_capabilities=missing_capabilities,
        backend_ready=backend_ready,
    )


def argus_connection_metadata(
    tested: ConnectionTest,
    assessment: ArgusConnectionAssessment | None = None,
) -> dict[str, Any]:
    """Return the non-secret compatibility/runtime facts safe to persist."""

    assessed = assessment or assess_argus_connection(tested)
    runtime = dict(tested.runtime)
    # Some host tests and third-party adapters expose the historical
    # ConnectionTest-shaped object without its newer helper methods. Keep
    # metadata negotiation structurally compatible with those probes.
    capabilities = set(tested.capabilities)
    declared_features = getattr(tested, "feature_support", {})
    feature_support = {
        name: bool(
            declared_features.get(name, capability in capabilities)
            if isinstance(declared_features, Mapping)
            else capability in capabilities
        )
        for name, capability in ARGUS_OPTIONAL_FEATURE_CAPABILITIES.items()
    }
    doctor_summary = getattr(tested, "doctor_summary", {})
    if not isinstance(doctor_summary, Mapping):
        doctor_summary = {}
    return {
        "argus_revision": runtime.get("revision"),
        "argus_release_id": runtime.get("release_id"),
        "argus_package_version": runtime.get("package_version"),
        "argus_worktree": runtime.get("worktree"),
        "protocol": dict(tested.protocol),
        "protocol_compatible": assessed.protocol_compatible,
        "snapshot_schema_version": tested.snapshot_schema_version,
        "snapshot_contract": {
            "advertised": feature_support["snapshot_schema"],
            "schema_version": tested.snapshot_schema_version,
            "schema_7_understood": tested.snapshot_schema_version == ARGUS_SNAPSHOT_SCHEMA_VERSION,
            "budget_fields_advertised": feature_support["snapshot_budget"],
            "usage_records_advertised": feature_support["recorded_usage"],
        },
        "capabilities": list(tested.capabilities),
        "feature_support": feature_support,
        "backend_ready": assessed.backend_ready,
        "system_doctor": {
            **dict(doctor_summary),
            "generated_at": getattr(tested, "doctor_generated_at", None),
        },
        "launch_compatible": assessed.launch_compatible,
        "missing_capabilities": list(assessed.missing_capabilities),
    }


def _system_doctor_backend_summary(
    report: Mapping[str, Any],
) -> tuple[bool, str | None, dict[str, Any]]:
    """Reduce Doctor output to bounded, non-secret backend launch evidence."""

    generated_at = report.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at.strip():
        generated_at = None
    findings = report.get("findings")
    if not isinstance(findings, list):
        return False, generated_at, {
            "status": "invalid_contract",
            "schema_version": report.get("schema_version"),
            "backend_finding_count": 0,
            "blocking_codes": [],
        }
    backend_findings = [
        finding
        for finding in findings
        if isinstance(finding, Mapping) and finding.get("scope") == "backend"
    ]
    blocking = [
        finding
        for finding in backend_findings
        if finding.get("severity") in {"error", "critical"}
        and finding.get("ok") is not True
    ]
    ready = bool(backend_findings) and not blocking
    blocking_codes = sorted(
        {
            str(finding.get("code") or "backend_unknown")
            for finding in blocking
        }
    )
    return ready, generated_at, {
        "status": "ready" if ready else "not_ready",
        "schema_version": report.get("schema_version"),
        "report_ok": report.get("ok") is True,
        "backend_finding_count": len(backend_findings),
        "blocking_codes": blocking_codes,
    }


class ArgusWebApiClient:
    """Connect Flywheel to local or remote Argus without importing Argus code."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 10.0,
        verify_tls: bool = True,
        transport: Transport | None = None,
        raw_transport: RawTransport | None = None,
        max_artifact_bytes: int = DEFAULT_ARTIFACT_MAX_BYTES,
        max_artifact_batch_bytes: int = DEFAULT_ARTIFACT_BATCH_MAX_BYTES,
        max_json_bytes: int = DEFAULT_JSON_MAX_BYTES,
    ) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http(s) URL")
        if parsed.username or parsed.password:
            raise ValueError("credentials must not be embedded in base_url")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"} and token:
            raise ValueError("remote bearer authentication requires HTTPS")
        if token and any(ord(character) < 32 or ord(character) == 127 for character in token):
            raise ValueError("token must not contain control characters")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_artifact_bytes <= 0 or max_artifact_batch_bytes <= 0:
            raise ValueError("artifact byte limits must be positive")
        if max_json_bytes <= 0:
            raise ValueError("JSON response byte limit must be positive")
        if max_artifact_batch_bytes < max_artifact_bytes:
            raise ValueError("batch artifact byte limit must cover one artifact")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self.verify_tls = verify_tls
        self._transport = transport or self._urlopen_transport
        self._raw_transport = raw_transport
        self.max_artifact_bytes = max_artifact_bytes
        self.max_artifact_batch_bytes = max_artifact_batch_bytes
        self.max_json_bytes = max_json_bytes

    def _headers(self, *, accept: str, authenticated: bool = True) -> dict[str, str]:
        headers = {"Accept": accept, "User-Agent": "Argus-Flywheel/1"}
        if authenticated and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _urlopen_transport(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, bytes]:
        context = None
        if url.startswith("https://") and not self.verify_tls:
            context = ssl._create_unverified_context()  # noqa: SLF001 - explicit opt-out
        opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=context)
        )
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(self.max_json_bytes + 1)
                if len(raw) > self.max_json_bytes:
                    raise ArgusWebApiError("Argus WebAPI JSON response exceeds the byte limit")
                return int(response.status), raw
        except urllib.error.HTTPError as exc:
            raw = exc.read(self.max_json_bytes + 1)
            if len(raw) > self.max_json_bytes:
                raise ArgusWebApiError("Argus WebAPI JSON error response exceeds the byte limit")
            return int(exc.code), raw
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ArgusWebApiError(f"Argus WebAPI is unreachable: {exc}") from exc

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            raise ValueError("API path must start with '/'")
        url = self.base_url + path
        if query:
            clean = {key: value for key, value in query.items() if value is not None}
            url += "?" + urllib.parse.urlencode(clean)
        headers = self._headers(accept="application/json", authenticated=authenticated)
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, raw = self._transport(method, url, headers, body, self.timeout)
        if not isinstance(raw, bytes):
            raise ArgusWebApiError("Argus WebAPI transport returned non-byte data", status=status)
        if len(raw) > self.max_json_bytes:
            raise ArgusWebApiError(
                "Argus WebAPI JSON response exceeds the byte limit", status=status
            )
        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArgusWebApiError("Argus WebAPI returned non-JSON data", status=status) from exc
        if not 200 <= status < 300:
            detail = decoded.get("detail") if isinstance(decoded, dict) else None
            raise ArgusWebApiError(str(detail or f"Argus WebAPI returned HTTP {status}"), status=status)
        if not isinstance(decoded, dict):
            raise ArgusWebApiError("Argus WebAPI response must be a JSON object", status=status)
        return decoded

    def test_connection(self) -> ConnectionTest:
        meta = self._request("GET", "/api/meta", authenticated=True)
        authentication = meta.get("authentication") or {}
        required = bool(authentication.get("required"))
        authenticated = bool(authentication.get("authenticated", not required))
        connection_authenticated = authenticated or not required
        backend_ready = False
        doctor_generated_at: str | None = None
        doctor_summary: dict[str, Any] = {
            "status": "not_probed_unauthenticated",
            "backend_finding_count": 0,
            "blocking_codes": [],
        }
        if connection_authenticated:
            try:
                doctor = self._request(
                    "GET", "/api/system/doctor", authenticated=True
                )
            except ArgusWebApiError as exc:
                doctor_summary = {
                    "status": (
                        "endpoint_missing" if exc.status == 404 else "unavailable"
                    ),
                    "http_status": exc.status,
                    "backend_finding_count": 0,
                    "blocking_codes": [],
                }
            else:
                (
                    backend_ready,
                    doctor_generated_at,
                    doctor_summary,
                ) = _system_doctor_backend_summary(doctor)
        protocol = meta.get("protocol") if isinstance(meta.get("protocol"), dict) else {}
        capabilities = meta.get("capabilities")
        normalized_capabilities = tuple(
            str(item) for item in capabilities if isinstance(item, str)
        ) if isinstance(capabilities, list) else ()
        feature_support = {
            name: capability in normalized_capabilities
            for name, capability in ARGUS_OPTIONAL_FEATURE_CAPABILITIES.items()
        }
        snapshot_schema = meta.get("snapshot_schema_version")
        return ConnectionTest(
            ok=connection_authenticated,
            authenticated=authenticated,
            authentication_required=required,
            runtime=meta.get("runtime") or {},
            protocol=protocol,
            capabilities=normalized_capabilities,
            snapshot_schema_version=(
                snapshot_schema
                if isinstance(snapshot_schema, int) and not isinstance(snapshot_schema, bool)
                else None
            ),
            feature_support=feature_support,
            backend_ready=backend_ready,
            doctor_generated_at=doctor_generated_at,
            doctor_summary=doctor_summary,
        )

    def list_projects(self, *, limit: int = 100, include_empty: bool = False) -> list[dict[str, Any]]:
        data = self._request(
            "GET", "/api/projects", query={"limit": limit, "include_empty": str(include_empty).lower()}
        )
        projects = data.get("projects")
        if not isinstance(projects, list):
            raise ArgusWebApiError("project list is missing")
        return [row for row in projects if isinstance(row, dict)]

    def create_daemon(
        self,
        *,
        name: str,
        objective: str,
        workdir: str,
        launch_cwd: str | None = None,
        command_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/api/daemons",
            payload={
                "name": name,
                "objective": objective,
                "workdir": workdir,
                "launch_cwd": launch_cwd or workdir,
                "command_id": command_id or str(uuid.uuid4()),
                "expected_revision": expected_revision,
            },
        )
        require_argus_daemon_command_applied(
            response,
            operation="create daemon",
            # Empty-objective sessions are intentionally created idle.  A
            # Flywheel research launch always supplies an objective and must
            # prove that an executor was spawned (or already alive).
            require_activation=bool(objective.strip()),
        )
        return response

    def snapshot(self, sid: str, *, events_limit: int = 80, compact: bool = False) -> dict[str, Any]:
        return self._request(
            "GET", self._project_path(sid, "/snapshot"),
            query={"events_limit": events_limit, "compact": str(compact).lower()},
        )

    def events(self, sid: str, *, limit: int = 80, view: str = "ui") -> list[dict[str, Any]]:
        if view not in {"ui", "full"}:
            raise ValueError("view must be 'ui' or 'full'")
        data = self._request("GET", self._project_path(sid, "/events"), query={"limit": limit, "view": view})
        events = data.get("events")
        if not isinstance(events, list):
            raise ArgusWebApiError("event list is missing")
        return [row for row in events if isinstance(row, dict)]

    def poll_events(
        self,
        sid: str,
        *,
        cursor: EventCursor | None = None,
        limit: int = 200,
        view: str = "full",
    ) -> EventBatch:
        """Poll the ordered event tail with deterministic overlap de-duplication.

        Argus 1.13 exposes an ordered tail but no research-event cursor.  This
        contract finds the longest overlap between the prior tail and the new
        one. ``gap_detected`` becomes true when a prior cursor exists but the
        server's bounded window no longer overlaps it; callers should then
        retain all returned events and mark the episode as potentially gapped.
        """

        if cursor is not None and (cursor.project_id != sid or cursor.view != view):
            raise ValueError("event cursor belongs to a different project or view")
        current_events = self.events(sid, limit=limit, view=view)
        current = tuple(self._event_fingerprint(event) for event in current_events)
        previous = cursor.fingerprints if cursor is not None else ()
        overlap = self._tail_overlap(previous, current)
        fresh = tuple(current_events[overlap:])
        next_fingerprints = current if current else previous
        return EventBatch(
            events=fresh,
            cursor=EventCursor(sid, view, next_fingerprints),
            overlap_count=overlap,
            gap_detected=bool(previous and current and overlap == 0),
        )

    @staticmethod
    def _event_fingerprint(event: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            event,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _tail_overlap(previous: tuple[str, ...], current: tuple[str, ...]) -> int:
        for size in range(min(len(previous), len(current)), 0, -1):
            if previous[-size:] == current[:size]:
                return size
        return 0

    def artifacts(self, sid: str) -> list[dict[str, Any]]:
        """Return Argus' allowlisted, read-only project artifact index."""
        data = self._request("GET", self._project_path(sid, "/artifacts"))
        artifacts = data.get("artifacts")
        if not isinstance(artifacts, list):
            raise ArgusWebApiError("artifact list is missing")
        return [row for row in artifacts if isinstance(row, dict)]

    def artifact(self, sid: str, path: str) -> dict[str, Any]:
        """Read one allowlisted artifact's JSON metadata and bounded preview.

        ``path`` is always sent as an encoded query value to Argus.  It is
        never joined to a Flywheel-local filesystem path; Argus remains the
        authority that normalizes the path and enforces its workspace
        allowlist.
        """
        self._validate_artifact_path(path)
        return self._request(
            "GET",
            self._project_path(sid, "/artifact"),
            query={"path": path},
        )

    def download_artifact(
        self,
        sid: str,
        path: str,
        *,
        max_bytes: int | None = None,
    ) -> ArtifactDownload:
        """Download one allowlisted raw artifact and hash it while streaming."""

        receipt = self._read_raw_artifact(
            sid,
            path,
            max_bytes=self._artifact_limit(max_bytes, self.max_artifact_bytes, "artifact"),
            retain_content=True,
        )
        if not isinstance(receipt, ArtifactDownload):  # pragma: no cover - internal invariant
            raise AssertionError("download must retain artifact bytes")
        return receipt

    def artifact_digest(
        self,
        sid: str,
        path: str,
        *,
        max_bytes: int | None = None,
    ) -> ArtifactDigest:
        """Hash one artifact incrementally without retaining its bytes in memory."""

        return self._read_raw_artifact(
            sid,
            path,
            max_bytes=self._artifact_limit(max_bytes, self.max_artifact_bytes, "artifact"),
            retain_content=False,
        )

    def download_artifacts(
        self,
        sid: str,
        paths: Iterable[str],
        *,
        max_bytes_each: int | None = None,
        max_bytes_total: int | None = None,
    ) -> tuple[ArtifactDownload, ...]:
        """Download a batch under independent per-file and cumulative ceilings."""

        per_file = self._artifact_limit(
            max_bytes_each, self.max_artifact_bytes, "per-artifact"
        )
        total_limit = self._artifact_limit(
            max_bytes_total, self.max_artifact_batch_bytes, "artifact batch"
        )
        downloads: list[ArtifactDownload] = []
        total = 0
        for path in paths:
            remaining = total_limit - total
            if remaining <= 0:
                raise ArgusWebApiError(
                    f"artifact batch exceeds cumulative byte limit ({total_limit})"
                )
            effective_limit = min(per_file, remaining)
            try:
                receipt = self._read_raw_artifact(
                    sid,
                    path,
                    max_bytes=effective_limit,
                    retain_content=True,
                )
            except ArgusWebApiError as exc:
                if remaining < per_file and "byte limit" in str(exc):
                    raise ArgusWebApiError(
                        f"artifact batch exceeds cumulative byte limit ({total_limit})",
                        status=exc.status,
                    ) from exc
                raise
            if not isinstance(receipt, ArtifactDownload):  # pragma: no cover
                raise AssertionError("batch download must retain bytes")
            total += receipt.size
            downloads.append(receipt)
        return tuple(downloads)

    def _read_raw_artifact(
        self,
        sid: str,
        path: str,
        *,
        max_bytes: int,
        retain_content: bool,
    ) -> ArtifactDigest:
        self._validate_artifact_path(path)
        url = self.base_url + self._project_path(sid, "/artifact/raw")
        url += "?" + urllib.parse.urlencode({"path": path, "download": "true"})
        headers = self._headers(accept="application/octet-stream")
        if self._raw_transport is not None:
            status, response_headers, chunks = self._raw_transport(
                "GET", url, headers, self.timeout
            )
            return self._consume_artifact_stream(
                path,
                status,
                response_headers,
                chunks,
                max_bytes=max_bytes,
                retain_content=retain_content,
            )
        return self._urlopen_raw_artifact(
            path,
            url,
            headers,
            max_bytes=max_bytes,
            retain_content=retain_content,
        )

    def _urlopen_raw_artifact(
        self,
        path: str,
        url: str,
        headers: Mapping[str, str],
        *,
        max_bytes: int,
        retain_content: bool,
    ) -> ArtifactDigest:
        context = None
        if url.startswith("https://") and not self.verify_tls:
            context = ssl._create_unverified_context()  # noqa: SLF001 - explicit opt-out
        opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=context)
        )
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            response = opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            error_response = exc
            with exc:
                return self._consume_artifact_stream(
                    path,
                    int(exc.code),
                    dict(exc.headers.items()),
                    iter(lambda: error_response.read(64 * 1024), b""),
                    max_bytes=max_bytes,
                    retain_content=retain_content,
                )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ArgusWebApiError(f"Argus WebAPI is unreachable: {exc}") from exc
        with response:
            return self._consume_artifact_stream(
                path,
                int(response.status),
                dict(response.headers.items()),
                iter(lambda: response.read(64 * 1024), b""),
                max_bytes=max_bytes,
                retain_content=retain_content,
            )

    @staticmethod
    def _consume_artifact_stream(
        path: str,
        status: int,
        headers: Mapping[str, str],
        chunks: Iterable[bytes],
        *,
        max_bytes: int,
        retain_content: bool,
    ) -> ArtifactDigest:
        normalized_headers = {str(key).lower(): str(value) for key, value in headers.items()}
        if not 200 <= status < 300:
            detail_bytes = bytearray()
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise ArgusWebApiError("Argus raw transport yielded non-byte data", status=status)
                detail_bytes.extend(chunk[: max(0, 64 * 1024 - len(detail_bytes))])
                if len(detail_bytes) >= 64 * 1024:
                    break
            detail: Any = None
            try:
                decoded = json.loads(bytes(detail_bytes).decode("utf-8"))
                detail = decoded.get("detail") if isinstance(decoded, dict) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise ArgusWebApiError(
                str(detail or f"Argus WebAPI returned HTTP {status}"), status=status
            )
        declared_length = normalized_headers.get("content-length")
        if declared_length:
            try:
                parsed_length = int(declared_length)
            except ValueError as exc:
                raise ArgusWebApiError("Argus artifact has an invalid Content-Length") from exc
            if parsed_length < 0 or parsed_length > max_bytes:
                raise ArgusWebApiError(
                    f"artifact exceeds byte limit ({max_bytes})", status=status
                )
        digest = hashlib.sha256()
        content_parts: list[bytes] = []
        size = 0
        if isinstance(chunks, (bytes, bytearray)):
            chunks = (bytes(chunks),)
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise ArgusWebApiError("Argus raw transport yielded non-byte data", status=status)
            size += len(chunk)
            if size > max_bytes:
                raise ArgusWebApiError(
                    f"artifact exceeds byte limit ({max_bytes})", status=status
                )
            digest.update(chunk)
            if retain_content:
                content_parts.append(chunk)
        common = {
            "path": path,
            "size": size,
            "sha256": digest.hexdigest(),
            "content_type": normalized_headers.get("content-type"),
        }
        if retain_content:
            return ArtifactDownload(content=b"".join(content_parts), **common)
        return ArtifactDigest(**common)

    @staticmethod
    def _artifact_limit(requested: int | None, ceiling: int, label: str) -> int:
        if requested is None:
            return ceiling
        if isinstance(requested, bool) or requested <= 0:
            raise ValueError(f"{label} byte limit must be a positive integer")
        if requested > ceiling:
            raise ValueError(f"{label} byte limit exceeds configured ceiling ({ceiling})")
        return requested

    def start(self, sid: str, *, expected_revision: int | None = None) -> dict[str, Any]:
        return self._command(sid, "start", expected_revision=expected_revision)

    def resolve_decision(
        self,
        sid: str,
        decision_id: str,
        *,
        option_id: str,
        note: str = "",
    ) -> dict[str, Any]:
        """Resolve an Argus typed operator-decision card (WebAPI 1.13)."""

        self._validate_identifier(decision_id, "decision id")
        if not isinstance(option_id, str) or not option_id.strip():
            raise ValueError("option_id must be a non-empty string")
        if not isinstance(note, str):
            raise ValueError("note must be a string")
        return self._request(
            "POST",
            self._project_path(
                sid,
                f"/decisions/{urllib.parse.quote(decision_id, safe='')}/resolve",
            ),
            payload={"option_id": option_id, "note": note},
        )

    def answer_backlog_item(self, sid: str, item_id: str, *, text: str) -> dict[str, Any]:
        """Answer the legacy pending-question route for older backlog cards."""

        self._validate_identifier(item_id, "backlog item id")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("answer text must be a non-empty string")
        return self._request(
            "POST",
            self._project_path(
                sid,
                f"/backlog/{urllib.parse.quote(item_id, safe='')}/answer",
            ),
            payload={"text": text},
        )

    def stop(
        self,
        sid: str,
        *,
        drain: bool = True,
        force: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            self._project_path(sid, "/daemon/stop"),
            payload={
                "drain": drain,
                "force": force,
                "command_id": str(uuid.uuid4()),
                "expected_revision": expected_revision,
            },
        )
        require_argus_daemon_command_applied(response, operation="stop daemon")
        return response

    def _command(self, sid: str, operation: str, *, expected_revision: int | None) -> dict[str, Any]:
        response = self._request(
            "POST", self._project_path(sid, f"/daemon/{operation}"),
            payload={"command_id": str(uuid.uuid4()), "expected_revision": expected_revision},
        )
        # Argus 1.13 start ACKs prove application with rc=0; its dedicated
        # start route does not yet publish the create route's ``spawned`` bit.
        require_argus_daemon_command_applied(response, operation=f"{operation} daemon")
        return response

    @staticmethod
    def _project_path(sid: str, suffix: str) -> str:
        ArgusWebApiClient._validate_identifier(sid, "Argus project id")
        return f"/api/projects/{urllib.parse.quote(sid, safe='')}{suffix}"

    @staticmethod
    def _validate_identifier(value: str, label: str) -> None:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 512
            or "/" in value
            or "\\" in value
            or value in {".", ".."}
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError(f"invalid {label}")

    @staticmethod
    def _validate_artifact_path(path: str) -> None:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("artifact path must be a non-empty string")
        parts = path.split("/")
        if (
            len(path) > 4096
            or path.startswith("/")
            or path.endswith("/")
            or "\\" in path
            or ":" in parts[0]
            or any(part in {"", ".", ".."} for part in parts)
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
        ):
            raise ValueError("artifact path must be a normalized relative allowlist path")
