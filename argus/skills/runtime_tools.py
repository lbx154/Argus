"""Project-owned tool revisions, validated and reused inside ordinary role turns."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from ..core.file_lock import exclusive_file_lock
from ..core.role_tool_bridge import require_fields
from ..wiki.bootstrap import init_wiki
from ..wiki.schema import WikiPage, serialize_page
from ..wiki.store import WikiStore, _atomic_write_text
from .runtime_worker import MAX_BYTES, validate_source
from .store import Skill, SkillStore

WORKER = Path(__file__).with_name('runtime_worker.py')


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True)


def _name(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', value):
        raise ValueError('use a semantic tool name with lowercase letters, digits and hyphens')
    return value


class RuntimeToolService:
    def __init__(self, root: Path, *, skills_root: Path, role: str, call_id: str, writable: bool):
        self.root = root.resolve()
        self.directory = self.root / 'runtime-tools'
        self.skills_root = skills_root.resolve()
        self.role, self.call_id, self.writable = role, call_id, writable
        self.observations: dict[str, str] = {}
        self.pending = None
        self.proposals = 0
        self.published = False
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.process_lock = threading.Lock()
        self.processes: set[subprocess.Popen] = set()

    def close(self) -> None:
        with self.process_lock:
            self.closed.set()
            for process in self.processes:
                if process.poll() is None:
                    process.kill()

    def _path(self, name: str) -> Path:
        path = self.directory / f'{_name(name)}.json'
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('runtime tool state must remain inside this project')
        return path

    def _load(self, name: str):
        path = self._path(name)
        if not path.exists():
            return None
        if path.stat().st_size > 512 * 1024:
            raise ValueError('runtime tool state exceeds its size limit')
        record = json.loads(path.read_text(encoding='utf-8'))
        spec = record.get('spec')
        if spec and hashlib.sha256(spec['source'].encode()).hexdigest() != record['source_sha256']:
            raise ValueError('runtime source changed outside its revision; restore the stored record before reuse')
        return record

    def _documents(self, name: str) -> tuple[Path, Path]:
        skill = self.skills_root / self.role / f'runtime-{name}.md'
        wiki = self.root / '.autors/runtime/wiki/pages' / f'{name}.md'
        if not skill.resolve().is_relative_to(self.skills_root) or not wiki.resolve().is_relative_to(self.root):
            raise ValueError('tool knowledge must remain inside its project scope')
        return skill, wiki

    def _run(self, source: str, value):
        encoded = _json({'source': source, 'input': value}).encode()
        if len(encoded) > MAX_BYTES:
            raise ValueError('source and input together must fit within 64 KiB')
        # No provider tokens, bridge capabilities, user configuration or import paths.
        environment = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR') if key in os.environ}
        with tempfile.TemporaryDirectory(prefix='argus-runtime-tool-') as directory:
            with self.process_lock:
                if self.closed.is_set():
                    raise ValueError('role turn ended')
                process = subprocess.Popen(
                    [sys.executable, '-I', '-S', str(WORKER)], cwd=directory, env=environment,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                self.processes.add(process)
            try:
                output, _error = process.communicate(encoded, timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise ValueError('runtime tool exceeded four seconds') from None
            finally:
                with self.process_lock:
                    self.processes.discard(process)
            if self.closed.is_set():
                raise ValueError('role turn ended')
            try:
                response = json.loads(output)
            except (ValueError, UnicodeError):
                raise ValueError('runtime worker failed or exceeded resource limits') from None
            if process.returncode or 'error' in response:
                raise ValueError(response.get('error', 'runtime worker failed'))
            return response['output']

    def dispatch(self, operation: str, payload: dict) -> dict:
        if operation == 'cancel':
            require_fields(payload, set())
            self.close()
            return {'status': 'cancelled'}
        with self.lock:
            if self.closed.is_set():
                raise ValueError('role turn ended')
            if operation == 'observe':
                require_fields(payload, {'id', 'tool'}, required={'id', 'tool'})
                reference, tool = str(payload['id']).split('|', 1)[0], str(payload['tool'])
                if len(reference) > 128 or len(tool) > 128:
                    raise ValueError('observation identifier is too long')
                # Bounded per-turn evidence; never copy potentially sensitive tool output.
                if len(self.observations) < 256:
                    self.observations[reference] = tool
                return {'status': 'observed'}
            if operation == 'list':
                require_fields(payload, set())
                tools = []
                for path in sorted(self.directory.glob('*.json'))[:32]:
                    record = self._load(path.stem)
                    if record:
                        tools.append({'name': path.stem, 'revision': record['revision'],
                                      'available': bool(record['spec']),
                                      'description': record['spec']['skill']['description'] if record['spec'] else 'Withdrawn; use the current revision to propose a corrected contract.',
                                      'source_path': str(path)})
                return {'tools': tools}
            if operation == 'propose':
                return self._propose(payload)
            if operation == 'run':
                require_fields(payload, {'name', 'input'}, required={'name', 'input'})
                name = _name(payload['name'])
                pending = self.pending if self.pending and self.pending['name'] == name else None
                current = self._load(name)
                spec = pending['spec'] if pending else current and current['spec']
                if not spec:
                    raise ValueError('tool is unavailable; list project tools first')
                try:
                    output = self._run(spec['source'], payload['input'])
                except ValueError:
                    if pending:
                        self.pending = None
                    raise
                if pending:
                    try:
                        receipt = self._commit(name, spec, pending['expected_revision'], pending['documents'])
                    finally:
                        self.pending = None
                    self.published = True
                else:
                    receipt = {'revision': current['revision'], 'published': False}
                return {'name': name, 'output': output, **receipt}
            if operation == 'rollback':
                require_fields(payload, {'name', 'expected_revision'}, required={'name', 'expected_revision'})
                if not self.writable:
                    raise ValueError('this role cannot revise runtime tools')
                name = _name(payload['name'])
                current = self._load(name)
                if not current:
                    raise ValueError('tool is unavailable')
                previous = current['history'][-1] if current['history'] else None
                return self._commit(name, previous, payload['expected_revision'])
            raise ValueError('unknown runtime tool operation')

    def _propose(self, payload: dict) -> dict:
        required = {'name', 'expected_revision', 'source', 'cases', 'evidence', 'skill', 'wiki'}
        require_fields(payload, required, required=required)
        if not self.writable or self.published or self.proposals >= 3:
            raise ValueError('at most three proposals and one publication per writable role turn')
        self.proposals += 1
        self.pending = None
        name = _name(payload['name'])
        evidence = payload['evidence']
        if not isinstance(evidence, list) or not 2 <= len(evidence) <= 8 or any(not isinstance(item, str) for item in evidence):
            raise ValueError('cite two to eight observation IDs')
        evidence = list(dict.fromkeys(item.split('|', 1)[0] for item in evidence))
        if len(evidence) < 2 or any(item not in self.observations for item in evidence):
            raise ValueError('cite two distinct actual observation IDs')
        work = [tool for tool in self.observations.values() if tool not in {'evolve_runtime', 'list_learned_tools', 'rollback_runtime'}]
        if len(work) < 3:
            raise ValueError('observe at least three work tool results before proposing a change')
        current = self._load(name)
        self._check_revision(current, payload['expected_revision'])
        if current and current['owner'] != self.role:
            raise ValueError('only the owning role may revise this tool')
        validate_source(payload['source'])
        skill, wiki = payload['skill'], payload['wiki']
        for fields, label, title in ((skill, 'skill', 'name'), (wiki, 'wiki', 'title')):
            require_fields(fields, {title, 'description', 'content'}, required={title, 'description', 'content'})
            if any(not isinstance(value, str) or not value.strip() or len(value) > 6000 for value in fields.values()):
                raise ValueError(f'{label} fields must contain bounded, non-empty text')
            if len(fields[title]) > 128 or len(fields['description']) > 512:
                raise ValueError('titles and descriptions must be concise')
        Skill(**skill).render()
        serialize_page(WikiPage(**wiki))
        cases = payload['cases']
        if not isinstance(cases, list) or not 2 <= len(cases) <= 8:
            raise ValueError('provide two to eight input/expected cases including a boundary case')
        for case in cases:
            require_fields(case, {'input', 'expected'}, required={'input', 'expected'})
        if len({_json(case['input']) for case in cases}) != len(cases):
            raise ValueError('test inputs must be distinct')
        # New revisions must also preserve every recorded previous contract case.
        checks = list({(_json(case['input']), _json(case['expected'])): case
                       for case in [*(current['spec']['cases'] if current and current['spec'] else []), *cases]}.values())
        if len(checks) > 16:
            raise ValueError('a tool contract is limited to 16 accumulated regression cases')
        for case in checks:
            if _json(self._run(payload['source'], case['input'])) != _json(case['expected']):
                raise ValueError('candidate failed an expected output; previous revision stays active')
        spec = {key: payload[key] for key in ('source', 'cases', 'skill', 'wiki')}
        spec['cases'] = checks
        spec['evidence'] = evidence
        if len(_json(spec).encode()) > MAX_BYTES:
            raise ValueError('source, regression cases and knowledge must fit within a 64 KiB tool revision')
        documents = {path: path.read_bytes() if path.exists() else None for path in self._documents(name)}
        if not current and any(value is not None for value in documents.values()):
            raise ValueError('a document already owns this semantic path; improve its existing tool or choose another name')
        self.pending = {'name': name, 'spec': spec, 'expected_revision': payload['expected_revision'],
                        'documents': documents}
        return {'status': 'candidate', 'name': name, 'checks_passed': len(checks),
                'next': 'Use run_learned_tool on this task input. Only a successful use publishes the tool and its Skill/Wiki.'}

    @staticmethod
    def _check_revision(current, expected) -> None:
        if type(expected) is not int or expected < 0 or expected != (current['revision'] if current else 0):
            raise ValueError('stale revision; list current project tools before changing one')

    def _commit(self, name, spec, expected_revision, expected_documents=None) -> dict:
        self._path(name)  # Scope-check before making any directory or lock file.
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / '.lock').open('a+') as handle, exclusive_file_lock(handle, timeout_seconds=5), self.process_lock:
            current = self._load(name)
            self._check_revision(current, expected_revision)
            if current and current['owner'] != self.role:
                raise ValueError('only the owning role may revise this tool')
            if not current and len(list(self.directory.glob('*.json'))) >= 32:
                raise ValueError('project already has 32 runtime tools; improve an existing tool')
            if self.closed.is_set():
                raise ValueError('role turn ended')
            skill_path, wiki_path = self._documents(name)
            if expected_documents is not None and any(
                (path.read_bytes() if path.exists() else None) != data for path, data in expected_documents.items()
            ):
                raise ValueError('Skill or Wiki changed during validation; review the new content and propose again')
            wiki_root = init_wiki('runtime', base=self.root)
            index = wiki_root / 'INDEX.md'
            destinations = [skill_path, wiki_path, index, self._path(name)]
            previous = {path: path.read_bytes() if path.exists() else None for path in destinations}
            record = {'revision': expected_revision + 1, 'owner': self.role, 'call_id': self.call_id,
                      'source_sha256': hashlib.sha256(spec['source'].encode()).hexdigest() if spec else None,
                      'spec': spec, 'history': [*(current['history'] if current else []),
                                              *([current['spec']] if current else [])][-3:]}
            try:
                if spec:
                    SkillStore(self.skills_root / self.role).save(Skill(**spec['skill'], path=skill_path.name))
                    WikiStore(wiki_root).write_page(wiki_path.name, WikiPage(**spec['wiki']))
                    title = spec['wiki']['title'].replace('\n', ' ').replace('[', '\\[').replace(']', '\\]')
                    link = f'- [{title}](pages/{name}.md)'
                else:
                    skill_path.unlink(missing_ok=True)
                    wiki_path.unlink(missing_ok=True)
                    link = ''
                lines = [line for line in index.read_text(encoding='utf-8').splitlines()
                         if f'](pages/{name}.md)' not in line and line != '_No Wiki pages yet._']
                _atomic_write_text(index, '\n'.join([*lines, *([link] if link else [])]).rstrip() + '\n')
                _atomic_write_text(self._path(name), _json(record) + '\n')
            except BaseException:
                for path, data in previous.items():
                    if data is None:
                        path.unlink(missing_ok=True)
                    else:
                        _atomic_write_text(path, data.decode())
                raise
        return {'revision': record['revision'], 'published': bool(spec),
                'skill_path': str(skill_path), 'wiki_path': str(wiki_path)}
