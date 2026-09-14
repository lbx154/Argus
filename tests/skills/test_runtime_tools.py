import threading
import time
from pathlib import Path

import pytest

from argus_skill.skills.runtime_tools import RuntimeToolService
from argus_skill.skills.store import Skill
from argus_skill.wiki.schema import WikiPage, serialize_page

SOURCE = "def run(value):\n    return [row['id'].zfill(4) for row in value]\n"


def service(root, *, role='engineer', writable=True, call_id='call-one'):
    return RuntimeToolService(root, skills_root=root / 'skills', role=role,
                              call_id=call_id, writable=writable)


def proposal():
    return {
        'name': 'normalize-identifiers', 'expected_revision': 0, 'source': SOURCE,
        'cases': [{'input': [], 'expected': []}, {'input': [{'id': '7'}], 'expected': ['0007']}],
        'evidence': ['one', 'two'],
        'skill': {'name': 'Normalize identifiers', 'description': 'Pad textual record identifiers to four digits.',
                  'content': 'Use run_learned_tool with normalize-identifiers after checking the source IDs are strings. Preserve longer IDs; this is unsuitable for arithmetic quantities.'},
        'wiki': {'title': 'Identifier transformation', 'description': 'String ID behavior and limits.',
                 'content': 'The normalize-identifiers tool accepts a JSON list of objects with string id fields. Cases cover empty input and a one-digit ID. The task observations one and two motivate the string-preservation requirement; missing or numeric IDs are outside this contract.'},
    }


def observed(tool):
    for name in ('one', 'two', 'three'):
        tool.dispatch('observe', {'id': name + '|provider-signature', 'tool': 'read'})


def publish(tool):
    observed(tool)
    candidate = tool.dispatch('propose', proposal())
    assert candidate['status'] == 'candidate'
    return tool.dispatch('run', {'name': 'normalize-identifiers', 'input': [{'id': '19'}]})


def test_same_turn_use_publishes_skill_wiki_and_next_turn_reuses_code(tmp_path):
    first = service(tmp_path)
    observed(first)
    first.dispatch('propose', proposal())
    assert first.dispatch('list', {}) == {'tools': []}
    assert not (tmp_path / 'skills').exists()
    result = first.dispatch('run', {'name': 'normalize-identifiers', 'input': [{'id': '19'}]})
    assert result['output'] == ['0019'] and result['published'] and result['revision'] == 1
    skill = Path(result['skill_path']).read_text()
    wiki = Path(result['wiki_path']).read_text()
    assert skill.count('\n---\n') == 1 and 'run_learned_tool' in skill
    assert 'outside this contract' in wiki
    assert 'pages/normalize-identifiers.md' in Path(result['wiki_path']).parent.parent.joinpath('INDEX.md').read_text()
    first.close()
    second = service(tmp_path, call_id='call-two')
    assert second.dispatch('list', {})['tools'][0]['revision'] == 1
    reused = second.dispatch('run', {'name': 'normalize-identifiers', 'input': [{'id': '0000'}]})
    assert reused['output'] == ['0000'] and not reused['published']
    assert service(tmp_path / 'other-project').dispatch('list', {}) == {'tools': []}


def test_actual_use_failure_does_not_publish_a_test_only_candidate(tmp_path):
    tool = service(tmp_path)
    observed(tool)
    tool.dispatch('propose', proposal())
    with pytest.raises(ValueError, match='KeyError'):
        tool.dispatch('run', {'name': 'normalize-identifiers', 'input': [{}]})
    assert tool.pending is None
    assert tool.dispatch('list', {}) == {'tools': []}
    assert not (tmp_path / 'skills').exists()


def test_regression_failure_keeps_previous_code_and_rollback_restores_all_documents(tmp_path):
    publish(service(tmp_path))
    next_turn = service(tmp_path, call_id='two')
    observed(next_turn)
    bad = proposal()
    bad.update(expected_revision=1, source='def run(value):\n    return ["bad"]\n',
               cases=[{'input': 1, 'expected': ['bad']}, {'input': 2, 'expected': ['bad']}])
    with pytest.raises(ValueError, match='expected output'):
        next_turn.dispatch('propose', bad)
    assert next_turn.dispatch('run', {'name': bad['name'], 'input': [{'id': '8'}]})['output'] == ['0008']
    improved = proposal()
    improved.update(expected_revision=1, source="def run(value):\n    return [str(row['id']).zfill(4) for row in value]\n",
                    cases=[{'input': [{'id': 2}], 'expected': ['0002']}, {'input': [], 'expected': []}])
    improved['wiki']['content'] = 'String and integer IDs are supported. Null and missing IDs remain outside the verified contract.'
    next_turn.dispatch('propose', improved)
    receipt = next_turn.dispatch('run', {'name': bad['name'], 'input': [{'id': 15}]})
    assert receipt['revision'] == 2
    restored = next_turn.dispatch('rollback', {'name': bad['name'], 'expected_revision': 2})
    assert restored['revision'] == 3
    assert Path(restored['wiki_path']).read_text() == serialize_page(WikiPage(**proposal()['wiki']))
    with pytest.raises(ValueError):
        service(tmp_path).dispatch('run', {'name': bad['name'], 'input': [{'id': 15}]})


def test_candidate_cannot_overwrite_a_concurrent_revision_or_document_edit(tmp_path):
    a, b = service(tmp_path), service(tmp_path, call_id='two')
    for tool in (a, b):
        observed(tool)
        tool.dispatch('propose', proposal())
    receipt = a.dispatch('run', {'name': proposal()['name'], 'input': [{'id': '8'}]})
    with pytest.raises(ValueError, match='stale revision'):
        b.dispatch('run', {'name': proposal()['name'], 'input': [{'id': '9'}]})
    assert b.pending is None
    changed = proposal()
    changed['expected_revision'] = 1
    b.dispatch('propose', changed)
    skill = Path(receipt['skill_path'])
    skill.write_text('Operator edit during validation')
    with pytest.raises(ValueError, match='changed during validation'):
        b.dispatch('run', {'name': proposal()['name'], 'input': [{'id': '9'}]})
    assert skill.read_text() == 'Operator edit during validation'
    assert b.dispatch('list', {})['tools'][0]['revision'] == 1


def test_evidence_limits_ownership_and_read_only_service(tmp_path):
    tool = service(tmp_path)
    with pytest.raises(ValueError, match='actual observation'):
        tool.dispatch('propose', proposal())
    observed(tool)
    duplicate = proposal()
    duplicate['evidence'] = ['one', 'one|signature']
    with pytest.raises(ValueError, match='distinct'):
        tool.dispatch('propose', duplicate)
    tool.dispatch('propose', proposal())
    with pytest.raises(ValueError, match='three proposals'):
        tool.dispatch('propose', proposal())
    tool.dispatch('run', {'name': proposal()['name'], 'input': [{'id': '4'}]})
    readonly = service(tmp_path, role='reviewer', writable=False)
    assert readonly.dispatch('run', {'name': proposal()['name'], 'input': []})['output'] == []
    with pytest.raises(ValueError):
        readonly.dispatch('propose', proposal())
    with pytest.raises(ValueError):
        readonly.dispatch('rollback', {'name': proposal()['name'], 'expected_revision': 1})
    manager = service(tmp_path, role='manager')
    observed(manager)
    update = proposal()
    update['expected_revision'] = 1
    with pytest.raises(ValueError, match='owning role'):
        manager.dispatch('propose', update)


def test_withdrawn_tool_keeps_revision_discoverable_for_a_corrected_contract(tmp_path):
    tool = service(tmp_path)
    receipt = publish(tool)
    tool.dispatch('rollback', {'name': proposal()['name'], 'expected_revision': 1})
    assert not Path(receipt['skill_path']).exists()
    assert not Path(receipt['wiki_path']).exists()
    entry = service(tmp_path).dispatch('list', {})['tools'][0]
    assert entry['revision'] == 2 and not entry['available']
    corrected = service(tmp_path)
    observed(corrected)
    spec = proposal()
    spec['expected_revision'] = entry['revision']
    corrected.dispatch('propose', spec)
    assert corrected.dispatch('run', {'name': spec['name'], 'input': []})['revision'] == 3


@pytest.mark.parametrize('source', [
    "def run(value) return value",
    "import os\ndef run(value): return os.environ",
    "from subprocess import run\ndef run(value): return run(value)",
    "from urllib.request import urlopen\ndef run(value): return urlopen(value)",
    "def run(value): return value.__class__.__mro__",
    "def run(value): return open(value).read()",
    "def run(value): return eval(value)",
    "from re import sub\ndef run(value): return sub.__globals__",
    "from json import __builtins__\ndef run(value): return value",
])
def test_worker_has_no_file_network_process_or_reflection_api(tmp_path, source):
    with pytest.raises(ValueError):
        service(tmp_path)._run(source, 'provider tokens and agent commands are inaccessible')


def test_worker_supports_pure_imports_and_bounds_output_and_time(tmp_path):
    tool = service(tmp_path)
    assert tool._run('from re import sub\ndef run(value): return sub(" +", " ", value)', 'a   b') == 'a b'
    with pytest.raises(ValueError, match='output exceeds'):
        tool._run('def run(value): return "x" * 70000', None)
    started = time.monotonic()
    with pytest.raises(ValueError):
        tool._run('def run(value):\n    while True: pass', None)
    assert time.monotonic() - started < 6


def test_turn_cancellation_kills_the_active_worker(tmp_path):
    tool = service(tmp_path)
    errors = []

    def run():
        try:
            tool._run('def run(value):\n    while True: pass', None)
        except ValueError as error:
            errors.append(str(error))

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 2
    while not tool.processes and time.monotonic() < deadline:
        time.sleep(0.01)
    tool.close()
    thread.join(timeout=2)
    assert not thread.is_alive() and not tool.processes
    assert errors == ['role turn ended']


def test_document_failure_rolls_back_both_knowledge_and_active_revision(tmp_path, monkeypatch):
    from argus_skill.wiki.store import WikiStore

    first = service(tmp_path)
    receipt = publish(first)
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    next_turn = service(tmp_path)
    observed(next_turn)
    update = proposal()
    update['expected_revision'] = 1
    update['skill']['content'] += '\nAn additional scope note.'
    next_turn.dispatch('propose', update)

    def fail(*_args, **_kwargs):
        raise OSError('disk failure')

    monkeypatch.setattr(WikiStore, 'write_page', fail)
    with pytest.raises(OSError, match='disk failure'):
        next_turn.dispatch('run', {'name': proposal()['name'], 'input': []})
    assert Path(receipt['skill_path']).read_bytes() == before[Path(receipt['skill_path'])]
    assert next_turn.dispatch('list', {})['tools'][0]['revision'] == 1


@pytest.mark.parametrize('kind', ['skill', 'wiki'])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_persistence_rejects_duplicate_frontmatter(kind, newline):
    fields = proposal()[kind]
    fields['content'] = newline.join(['---', 'name: nested', 'description: duplicate', '---', 'Body'])
    with pytest.raises(ValueError, match='omit frontmatter'):
        Skill(**fields).render() if kind == 'skill' else serialize_page(WikiPage(**fields))


def test_runtime_state_and_knowledge_symlinks_cannot_escape_project(tmp_path):
    root, outside = tmp_path / 'project', tmp_path / 'other'
    root.mkdir()
    outside.mkdir()
    (root / 'runtime-tools').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='inside this project'):
        publish(service(root))
    (root / 'runtime-tools').unlink()
    (root / 'skills').mkdir()
    (root / 'skills/engineer').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='project scope'):
        publish(service(root))
    assert list(outside.iterdir()) == []


def test_oversized_revision_does_not_become_a_pending_candidate(tmp_path):
    tool = service(tmp_path)
    observed(tool)
    spec = proposal()
    spec['source'] = 'def run(value): return value'
    spec['cases'] = [{'input': 'a' * 20000, 'expected': 'a' * 20000},
                     {'input': 'b' * 20000, 'expected': 'b' * 20000}]
    with pytest.raises(ValueError, match='64 KiB tool revision'):
        tool.dispatch('propose', spec)
    assert tool.pending is None and not (tmp_path / 'runtime-tools').exists()
