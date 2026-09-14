import collections
import http.server
import json
import os
import socketserver
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from paths import CODE_ROOT, ROOT

if ROOT == CODE_ROOT:
    raise SystemExit('Set PILOT_ROOT to a new directory; published accounting is read-only')
protocol = json.loads((ROOT / 'protocol.json').read_text())
token_file = os.environ.get('PILOT_COPILOT_TOKEN_FILE')
if not token_file:
    raise SystemExit('Set PILOT_COPILOT_TOKEN_FILE to a private file containing your Copilot access token')
token = Path(token_file).read_text().strip()
if not token:
    raise SystemExit('The configured token file is empty')
lock = threading.Lock()
per_run = collections.Counter()
total_requests = 0
spent = 0.0
reserved = 0.0
log_path = ROOT / 'gateway-usage.jsonl'
if log_path.exists():
    for line in log_path.read_text().splitlines():
        row = json.loads(line)
        if row.get('forwarded'):
            total_requests += 1
            per_run[row['run']] += 1
            spent += row.get('cost_usd', 0)

class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def error(self, message):
        body = json.dumps({'error': {'message': message, 'type': 'experiment_budget'}}).encode()
        self.send_response(402)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        global total_requests, spent, reserved
        if self.path not in ['/responses', '/v1/responses']:
            self.error('Unsupported experiment endpoint')
            return
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        run = self.headers.get('X-Experiment-Run', 'unknown')
        call_limit = protocol['training_model_calls'] if run.startswith('train-') else protocol['heldout_model_calls']
        if run.startswith('review-'):
            call_limit = 12
        body['model'] = protocol['model']
        body['max_output_tokens'] = min(int(body.get('max_output_tokens') or 8192), 8192)
        encoded = json.dumps(body).encode()
        # Upper-bound reservation uses bytes as a conservative input-token bound.
        reserve = len(encoded) * 8 / 1e6 + body['max_output_tokens'] * 30 / 1e6
        with lock:
            if total_requests >= protocol['total_request_cap'] or per_run[run] >= call_limit:
                self.error('Experiment model-call limit reached')
                return
            if spent + reserved + reserve > protocol['total_cost_cap_usd']:
                self.error('Experiment cost cap reached')
                return
            total_requests += 1
            per_run[run] += 1
            reserved += reserve
            index = total_requests
        request = urllib.request.Request('https://api.githubcopilot.com/responses', data=encoded, headers={
            'Authorization': 'Bearer ' + token, 'User-Agent': 'GithubCopilot/1.0.84',
            'Copilot-Integration-Id': 'copilot-developer-cli', 'X-Initiator': 'agent',
            'Content-Type': 'application/json', 'Accept': 'text/event-stream',
        })
        started = time.time()
        row = {'request': index, 'run': run, 'started_at': started, 'forwarded': True}
        received = bytearray()
        try:
            response = urllib.request.urlopen(request, timeout=100)
            row['status'] = response.status
            self.send_response(response.status)
            self.send_header('Content-Type', response.headers.get('Content-Type', 'text/event-stream'))
            self.send_header('Connection', 'close')
            self.end_headers()
            while chunk := response.read1(65536):
                received.extend(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
            response.close()
        except urllib.error.HTTPError as error:
            row['status'] = error.code
            self.send_response(error.code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(error.read())
        except (BrokenPipeError, ConnectionResetError, TimeoutError) as error:
            row['transport_error'] = type(error).__name__
        finally:
            for line in bytes(received).decode(errors='replace').splitlines():
                if not line.startswith('data: {'):
                    continue
                try:
                    event = json.loads(line[6:])
                except ValueError:
                    continue
                if event.get('type') in ['response.completed', 'response.incomplete']:
                    response = event.get('response', {})
                    row['usage'] = response.get('usage', {})
                    row['copilot_usage'] = response.get('copilot_usage')
            usage = row.get('usage', {})
            inputs = usage.get('input_tokens', 0)
            cached = usage.get('input_tokens_details', {}).get('cached_tokens', 0)
            cache_write = usage.get('input_tokens_details', {}).get('cache_write_tokens', 0)
            outputs = usage.get('output_tokens', 0)
            long = inputs > 272000
            row['cost_usd'] = (max(0, inputs - cached - cache_write) * (8 if long else 4)
                               + cached * (.8 if long else .4) + cache_write * (10 if long else 5)
                               + outputs * (30 if long else 20)) / 1e6
            if row.get('transport_error') and not usage:
                row['cost_usd'] = reserve
                row['cost_basis'] = 'conservative reservation after incomplete response'
            else:
                row['cost_basis'] = 'model token-rate estimate'
            row['duration_seconds'] = time.time() - started
            with lock:
                reserved -= reserve
                spent += row['cost_usd']
                row['cumulative_cost_usd'] = spent
                with log_path.open('a') as stream:
                    stream.write(json.dumps(row) + '\n')
            self.close_connection = True

directory = ROOT / 'socket'
directory.mkdir(exist_ok=True)
path = directory / 'model.sock'
path.unlink(missing_ok=True)
with Server(str(path), Handler) as server:
    os.chmod(path, 0o666)
    (ROOT / 'gateway.pid').write_text(str(os.getpid()))
    print('Experiment gateway ready', flush=True)
    server.serve_forever()
