from __future__ import annotations

import json
from pathlib import Path

from argus.webapi.work_records import export_work_records, full_work_record


def write_events(path: Path, rows: list[dict]):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))


def test_full_record_restores_tail_beyond_snapshot_limit_and_rotated_history(tmp_path):
    detail = '项目检查。\n' * 1500 + '最后一行必须保留。'
    write_events(tmp_path / 'events.jsonl.2', [{'type': 'engineer.progress', 'kind': 'agent_message',
                                             'agent_layer': 'engineer', 'message_id': 'long-message',
                                             'event_id': 'archived', 'text': detail}])
    write_events(tmp_path / 'events.jsonl', [{'type': 'engineer.progress', 'kind': 'agent_message',
                                           'message_id': 'latest', 'text': 'A different record.'}])
    result = full_work_record(tmp_path, 'engineer', 'engineer:long-message')
    assert result['detail'] == detail and result['characters'] > 4000
    assert full_work_record(tmp_path, 'engineer', 'archived')['detail'].endswith('最后一行必须保留。')
    assert full_work_record(tmp_path, 'engineer', 'missing') is None


def test_full_record_cache_invalidates_and_fragments_keep_full_content(tmp_path):
    path = tmp_path / 'events.jsonl'
    first = {'type': 'engineer.progress', 'kind': 'agent_message', 'message_id': 'message', 'text': 'First part.'}
    write_events(path, [first])
    assert full_work_record(tmp_path, 'engineer', 'engineer:message')['detail'] == 'First part.'
    write_events(path, [first, {**first, 'fragment_mode': 'append', 'text': 'Last part.'}])
    assert full_work_record(tmp_path, 'engineer', 'engineer:message')['detail'] == 'First part.\nLast part.'


def test_private_reasoning_and_provider_data_are_never_exported(tmp_path):
    write_events(tmp_path / 'events.jsonl', [
        {'type': 'engineer.progress', 'kind': 'reasoning', 'event_id': 'private', 'text': 'PRIVATE SCRATCHPAD'},
        {'type': 'agent.io.complete', 'event_id': 'provider', 'text': 'PRIVATE TRANSPORT'},
        {'type': 'engineer.progress', 'kind': 'agent_message', 'event_id': 'public', 'agent_layer': 'engineer', 'text': 'Public result.\nNEXT_OWNER=reviewer'},
    ])
    assert full_work_record(tmp_path, 'engineer', 'private') is None
    assert full_work_record(tmp_path, 'engineer', 'provider') is None
    text = ''.join(export_work_records(tmp_path, 'engineer'))
    assert 'Public result.' in text and 'PRIVATE' not in text and 'NEXT_OWNER' not in text


def test_export_contains_more_than_twenty_four_records_and_keeps_task_filter(tmp_path):
    rows = [{'type': 'life.mission.started', 'item_id': 'task-a'}]
    rows += [{'type': 'engineer.progress', 'agent_layer': 'engineer', 'kind': 'tool_result',
              'text': f'工具结果 {index}。' + '详情。' * 2000, 'event_id': str(index)} for index in range(50)]
    rows += [{'type': 'life.mission.started', 'item_id': 'task-b'},
             {'type': 'engineer.progress', 'agent_layer': 'engineer', 'kind': 'agent_message', 'text': 'OTHER TASK'}]
    write_events(tmp_path / 'events.jsonl', rows)
    exported = ''.join(export_work_records(tmp_path, 'engineer', 'task-a'))
    assert '工具结果 0。' in exported and '工具结果 49。' in exported
    assert exported.count('详情。') == 100000
    assert 'OTHER TASK' not in exported


def test_incomplete_and_oversized_records_cannot_be_reinterpreted_as_events(tmp_path):
    path = tmp_path / 'events.jsonl'
    good = {'type': 'engineer.progress', 'agent_layer': 'engineer', 'event_id': 'ok', 'text': 'Saved record.'}
    path.write_bytes(b'x' * (1024 * 1024 + 5) + b'\n' + json.dumps(good).encode() + b'\n' + json.dumps({**good, 'event_id': 'torn'}).encode())
    assert full_work_record(tmp_path, 'engineer', 'ok')['detail'] == 'Saved record.'
    assert full_work_record(tmp_path, 'engineer', 'torn') is None


def test_work_record_http_auth_and_full_download(tmp_path):
    from fastapi.testclient import TestClient

    from argus.webapi.server import create_app

    folder = tmp_path / 'projects/s-work'
    folder.mkdir(parents=True)
    detail = '完整记录。' * 1800 + '末尾仍在。'
    write_events(folder / 'events.jsonl', [{'type': 'engineer.progress', 'kind': 'agent_message',
                                         'agent_layer': 'engineer', 'message_id': 'message', 'text': detail}])
    with TestClient(create_app(global_root=tmp_path, auth_token='test-only')) as client:
        path = '/api/projects/s-work/work-record?role=engineer&record_id=engineer:message'
        assert client.get(path).status_code == 401
        response = client.get(path, headers={'Authorization': 'Bearer test-only'})
        assert response.status_code == 200
        assert response.json()['detail'] == detail
        response = client.get('/api/projects/s-work/work-log/download?role=engineer', headers={'Authorization': 'Bearer test-only'})
        assert response.status_code == 200 and 'attachment' in response.headers['content-disposition']
        assert detail in response.text
        assert client.get('/api/projects/s-missing/work-record?role=engineer&record_id=x', headers={'Authorization': 'Bearer test-only'}).status_code == 404
