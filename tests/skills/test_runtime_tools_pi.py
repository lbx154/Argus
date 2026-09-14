"""Ordinary Argus backend -> actual Pi loop -> local scripted provider, no paid APIs."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from argus_skill.adapters.agent_cli_backend import AgentCliBackend
from argus_skill.core.models import RunnerOptions

PI_CLI = os.environ.get('ARGUS_PI_TEST_CLI')


@pytest.mark.skipif(not PI_CLI, reason='Set ARGUS_PI_TEST_CLI to exercise the actual Pi agent loop')
def test_users_normal_pi_turn_learns_and_next_turn_reuses(tmp_path, monkeypatch):
    state, workspace, config = tmp_path / 'state', tmp_path / 'workspace', tmp_path / 'pi'
    workspace.mkdir()
    config.mkdir()
    for index in range(3):
        (workspace / f'ids-{index}.txt').write_text('7\n08\n0000\n')
    tool_spec = {
        'name': 'pad-string-ids', 'expected_revision': 0,
        'source': 'def run(value):\n    return [item.zfill(4) for item in value]\n',
        'cases': [{'input': [], 'expected': []}, {'input': ['7'], 'expected': ['0007']}],
        'evidence': ['observed-0', 'observed-1'],
        'skill': {'name': 'Pad string IDs', 'description': 'Zero-pad textual identifiers to four positions.',
                  'content': 'Use run_learned_tool with pad-string-ids for string lists. Verify field meaning before padding; do not apply to arithmetic values.'},
        'wiki': {'title': 'String ID tool scope', 'description': 'JSON string inputs and empty-list behavior.',
                 'content': 'Source ids-0.txt and ids-1.txt contain textual identifiers. Empty input and single-digit strings pass the declared examples; no numeric input behavior is established.'},
    }
    actions = [
        *[('read', {'path': str(workspace / f'ids-{i}.txt')}, f'observed-{i}') for i in range(3)],
        ('evolve_runtime', tool_spec, 'candidate'),
        ('run_learned_tool', {'name': 'pad-string-ids', 'input': ['08', '0000']}, 'use-current-task'),
        (None, 'Current task complete.', None),
        ('list_learned_tools', {}, 'discover-next-turn'),
        ('run_learned_tool', {'name': 'pad-string-ids', 'input': ['9']}, 'reuse-next-turn'),
        (None, 'Next task complete.', None),
    ]
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            index = len(requests)
            requests.append(body)
            name, value, call_id = actions[index] if index < len(actions) else (None, 'Stop fixture.', None)
            delta = {'role': 'assistant', 'content': value} if name is None else {
                'role': 'assistant', 'tool_calls': [{'index': 0, 'id': call_id, 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(value)}}],
            }
            chunks = [
                {'id': f'fixture-{index}', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'model',
                 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                {'id': f'fixture-{index}', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'model',
                 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if name else 'stop'}],
                 'usage': {'prompt_tokens': 50, 'completion_tokens': 10, 'total_tokens': 60}},
            ]
            encoded = (''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    (config / 'models.json').write_text(json.dumps({'providers': {'runtime-fixture': {
        'baseUrl': f'http://127.0.0.1:{server.server_port}/v1', 'api': 'openai-completions',
        'apiKey': 'local-fixture', 'models': [{'id': 'model', 'reasoning': False}],
    }}}))
    monkeypatch.setenv('ARGUS_SKILL_HOME', str(tmp_path / 'global'))
    monkeypatch.setenv('ARGUS_SKILL_COST_CONTROL', 'off')
    monkeypatch.setenv('ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING', '1')
    monkeypatch.setenv('ARGUS_SKILL_ENGINEER_TURN_MAX_SECONDS', '20')
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(config))
    monkeypatch.setenv('PI_HARNESS_PROFILE', 'argus')
    monkeypatch.setenv('PI_OFFLINE', '1')
    backend = AgentCliBackend(backend='pi', runner_bin=PI_CLI)
    backend.set_usage_context(project_root=state, global_root=tmp_path / 'global', mission_id='normal-task')
    try:
        for text in ('Normalize textual IDs while retaining a reusable method.', 'Normalize another ID using project tools.'):
            result = backend.run_exec(prompt=text, run_label='engineer-r1',
                options=RunnerOptions(model='runtime-fixture/model', working_dir=str(workspace)))
            assert result.exit_code == 0, result.fatal_error or result.stderr_lines
        assert len(requests) == len(actions)
        names = {item['function']['name'] for item in requests[0]['tools']}
        assert {'evolve_runtime', 'run_learned_tool', 'list_learned_tools'} <= names
        responses = [message for request in requests for message in request['messages'] if message['role'] == 'tool']
        used = next(message for message in responses if message.get('tool_call_id') == 'use-current-task')
        reused = next(message for message in responses if message.get('tool_call_id') == 'reuse-next-turn')
        assert '0008' in str(used['content']) and 'published' in str(used['content'])
        assert '0009' in str(reused['content'])
        record = json.loads((state / 'runtime-tools/pad-string-ids.json').read_text())
        assert record['revision'] == 1
        assert (state / 'skills/engineer/runtime-pad-string-ids.md').is_file()
        assert (state / '.autors/runtime/wiki/pages/pad-string-ids.md').is_file()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
