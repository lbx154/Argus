#!/usr/bin/env node
// A project from the outside, as a person runs one: start `argus`, give it a request through
// the web cockpit's API as the page does, and wait until the work rests. Prints the Manager's
// letters to the operator and the project's status from its record.
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const [request, workdir] = process.argv.slice(2);
const argus = spawn('argus', ['--port', '8799'], { stdio: ['ignore', 'pipe', 'inherit'], shell: process.platform === 'win32' });
let url, token;
for await (const chunk of argus.stdout) {
  [, url, token] = /Argus is at (http\S+?)\/\?token=(\S+)/.exec(String(chunk)) ?? [];
  if (url) break;
}
const api = async (path, body) => {
  const headers = { authorization: `Bearer ${decodeURIComponent(token)}`, ...body ? { 'content-type': 'application/json' } : {} };
  const response = await fetch(`${url}${path}`, body ? { method: 'POST', headers, body: JSON.stringify(body) } : { headers });
  if (!response.ok) throw new Error(`${path}: ${response.status} ${await response.text()}`);
  return response.json();
};
try {
  const { sid } = await api('/api/daemons', { workdir });
  const controller = new AbortController();
  const stream = await fetch(`${url}/api/projects/${sid}/stream?replay=40&view=ui`, {
    headers: { authorization: `Bearer ${decodeURIComponent(token)}` }, signal: controller.signal,
  });
  if (!stream.ok) throw new Error(`Event stream: ${stream.status}`);
  const reading = (async () => {
    const decoder = new TextDecoder();
    let buffer = '';
    for await (const chunk of stream.body) {
      buffer += decoder.decode(chunk, { stream: true });
      let end;
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const data = buffer.slice(0, end).split('\n').find(line => line.startsWith('data: '));
        buffer = buffer.slice(end + 2);
        if (data) {
          const event = JSON.parse(data.slice(6));
          if (event.type === 'ui.argus') console.log(event.text);
        }
      }
    }
  })();
  try {
    await api(`/api/projects/${sid}/message`, { text: request });
    while ((await api('/api/projects')).projects.find(row => row.id === sid).daemon_alive) await sleep(200);
  } finally {
    controller.abort();
    await reading.catch(error => { if (error.name !== 'AbortError') throw error; });
  }
  const record = join(process.env.ARGUS_HOME || join(homedir(), '.argus'), 'projects', sid, 'project.json');
  console.log(`status: ${JSON.parse(readFileSync(record, 'utf8')).status}`);
} finally {
  argus.kill();
}
