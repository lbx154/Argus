"""Read full public work records without enlarging bounded mission snapshots."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from ..core.jsonl_reader import MAX_JSONL_RECORD_BYTES
from ..core.mission_view import work_record_event_id
from ..core.role_reply import strip_named_lines
from ..core.secret_guard import known_secret_values, redact_secrets_text
from ..life.event_log import event_log_paths

ROLES = ('manager', 'planner', 'engineer', 'reviewer')
_NAMES = dict(zip(ROLES, ('统筹', '规划', '执行', '复核')))
_PROTOCOL = ('MILESTONE_STATUS', 'NEXT_OWNER', 'OPERATOR_QUESTION', 'OPERATOR_OPTIONS', 'ROLE_DECISION')


def _events(root: Path) -> Iterator[dict]:
    for path in event_log_paths(root / 'events.jsonl'):
        try:
            with path.open('rb') as stream:
                while raw := stream.readline(MAX_JSONL_RECORD_BYTES + 1):
                    if len(raw) > MAX_JSONL_RECORD_BYTES:
                        while raw and not raw.endswith(b'\n'):
                            raw = stream.readline(MAX_JSONL_RECORD_BYTES + 1)
                        continue
                    if not raw.endswith(b'\n'):
                        continue  # Never display an unfinished append as a complete record.
                    try:
                        event = json.loads(raw)
                    except (ValueError, UnicodeError, RecursionError):
                        continue
                    if isinstance(event, dict):
                        yield event
        except FileNotFoundError:
            continue  # A generation may have rotated between enumeration/open.


def _public_text(event: dict) -> str:
    kind = str(event.get('kind') or '')
    event_type = str(event.get('type') or '')
    if kind in {'reasoning', 'thinking', 'analysis'} or event.get('channel') in {'analysis', 'reasoning'}:
        return ''
    if event_type.startswith(('provider.', 'agent.io.', 'usage.')) or event_type == 'ui.operator':
        return ''
    role = str(event.get('agent_layer') or event.get('actor') or event.get('role') or '')
    for key in ('text', 'action_summary', 'summary', 'reason', 'objective', 'reply'):
        value = event.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if value.startswith('{') or (role in {'planner', 'reviewer'} and value.startswith('```json')):
            continue
        return strip_named_lines(value, _PROTOCOL).strip()
    return ''


def _role(event: dict) -> str:
    explicit = str(event.get('agent_layer') or event.get('actor') or event.get('role') or '')
    if explicit in ROLES:
        return explicit
    kind = str(event.get('type') or '')
    if kind.startswith('round.review.'):
        return 'reviewer'
    if kind.startswith(('round.main.', 'engineer.')):
        return 'engineer'
    if kind.startswith(('round.plan.', 'planner.')):
        return 'planner'
    return 'manager'


def _fingerprint(root: Path) -> tuple:
    rows = []
    for path in event_log_paths(root / 'events.jsonl'):
        try:
            stat = path.stat()
            rows.append((path.name, stat.st_ino, stat.st_size, stat.st_mtime_ns))
        except FileNotFoundError:
            continue
    return tuple(rows)


@lru_cache(maxsize=32)
def _full_record(root: str, role: str, identity: str, fingerprint: tuple) -> str:
    del fingerprint  # The cache key invalidates when any retained generation changes.
    result = ''
    for event in _events(Path(root)):
        message = str(event.get('message_id') or '')
        matches = work_record_event_id(event) == identity or bool(message and identity == f'{role}:{message}')
        if not matches:
            continue
        text = _public_text(event)
        if not text:
            continue
        if event.get('fragment_mode') == 'append' and result:
            result += '\n' + text
        elif len(text) >= len(result):
            result = text
    return result


def full_work_record(root: Path, role: str, identity: str) -> dict | None:
    detail = _full_record(str(root.resolve()), role, identity, _fingerprint(root))
    if not detail:
        return None
    # Redact against the current account too, rather than caching credential state.
    detail = redact_secrets_text(detail, known_values=known_secret_values())
    return {'id': identity, 'role': role, 'detail': detail, 'characters': len(detail)}


def export_work_records(root: Path, role: str, task_id: str = '') -> Iterator[str]:
    yield f'# 工作记录 · {_NAMES[role]}\n\n'
    mission = ''
    for event in _events(root):
        if event.get('type') == 'life.mission.started':
            mission = str(event.get('item_id') or event.get('mission_id') or '')
        owner = str(event.get('item_id') or event.get('mission_id') or mission)
        if _role(event) != role or (task_id and owner != task_id):
            continue
        text = _public_text(event)
        if text:
            safe = redact_secrets_text(text, known_values=known_secret_values())
            yield f'---\n\n{safe}\n\n'
