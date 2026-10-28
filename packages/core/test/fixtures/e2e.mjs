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
let url;
for await (const chunk of argus.stdout) if ((url = /Argus is at (http\S+?)\/(\s|\?)/.exec(String(chunk))?.[1])) break;
const api = async (path, body) => {
  const response = await fetch(`${url}${path}`, body ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) } : {});
  if (!response.ok) throw new Error(`${path}: ${response.status} ${await response.text()}`);
  return response.json();
};
try {
  const { sid } = await api('/api/daemons', { workdir });
  await api(`/api/projects/${sid}/message`, { text: request });
  while ((await api('/api/projects')).projects.find(row => row.id === sid).daemon_alive) await sleep(200);
  for (const event of (await api(`/api/projects/${sid}/events`)).events) if (event.type === 'ui.argus') console.log(event.text);
  const record = join(process.env.ARGUS_HOME || join(homedir(), '.argus'), 'projects', sid, 'project.json');
  console.log(`status: ${JSON.parse(readFileSync(record, 'utf8')).status}`);
} finally {
  argus.kill();
}
