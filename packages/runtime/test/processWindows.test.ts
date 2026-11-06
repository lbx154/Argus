import assert from 'node:assert/strict';
import { once } from 'node:events';
import { copyFileSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer, type AddressInfo, type Socket } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { executeProcess, type ProcessExit } from '../src/process.js';

// A real native tree, with a detached child and grandchild. The PID/socket evidence is collected
// by the test, not discovered by scanning unrelated processes or by waiting a fixed interval.
for (const stdinOwned of [false, true]) test(`Windows cancellation uses system taskkill and reaps a live tree (${stdinOwned ? 'stdin-owned' : 'independent'} CLI)`, { skip: process.platform !== 'win32', timeout: 20_000 }, async t => {
  const root = mkdtempSync(join(tmpdir(), 'argus tree 中文 '));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  copyFileSync(process.execPath, join(root, 'taskkill.exe')); // wrong program on PATH
  const path = process.env.PATH;
  process.env.PATH = root;
  t.after(() => { if (path === undefined) delete process.env.PATH; else process.env.PATH = path; });
  const server = createServer().listen(0, '127.0.0.1');
  await once(server, 'listening');
  const sockets: Socket[] = [];
  const pids: number[] = [];
  const closed: Promise<unknown>[] = [];
  t.after(() => {
    for (const pid of pids) try { process.kill(pid); } catch { /* owned fixture already ended */ }
    for (const socket of sockets) socket.destroy();
    server.close();
  });
  const controller = new AbortController();
  const port = (server.address() as AddressInfo).port;
  server.on('connection', socket => {
    sockets.push(socket);
    socket.on('error', () => {});
    closed.push(new Promise<void>(resolve => socket.once('close', () => resolve())));
    socket.once('data', bytes => {
      pids.push(Number(bytes.toString()));
      if (pids.length === 2) controller.abort();
    });
  });
  const connect = `require('node:net').connect(${port}, '127.0.0.1', function () { this.write(String(process.pid)); });`;
  const grandchild = connect;
  const child = `${connect}\nrequire('node:child_process').spawn(process.execPath, ['-e', ${JSON.stringify(grandchild)}], { stdio: 'ignore', detached: true }).unref();`;
  const source = `require('node:child_process').spawn(process.execPath, ['-e', ${JSON.stringify(child)}], { stdio: 'ignore', detached: true }).unref(); ${stdinOwned ? 'process.stdin.resume();' : 'setInterval(() => {}, 1000);'}`;
  const input = async function* (): AsyncGenerator<string> { await new Promise<void>(resolve => controller.signal.addEventListener('abort', () => resolve(), { once: true })); }();
  let result: ProcessExit | undefined;
  for await (const event of executeProcess({ executable: process.execPath, args: ['-e', source], cwd: root, input: stdinOwned ? input : '', signal: controller.signal })) {
    if (event.type === 'exit') result = event;
  }
  assert.equal(pids.length, 2);
  assert.equal(result?.stopKind, 'cancelled');
  assert.equal(result?.error, 'Run cancelled.');
  // The runtime's returned receipt must not hide a surviving descendant.
  for (const pid of pids) assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
  await Promise.all(closed);
});

test('Windows has no containment of ignored-pipe descendants after their CLI has exited', { skip: process.platform !== 'win32', timeout: 20_000 }, async t => {
  const server = createServer().listen(0, '127.0.0.1');
  await once(server, 'listening');
  let pid: number | undefined;
  let job: Socket | undefined;
  t.after(() => {
    if (pid) try { process.kill(pid); } catch { /* owned fixture already ended */ }
    job?.destroy();
    server.close();
  });
  const connected = new Promise<void>(resolve => server.once('connection', socket => {
    job = socket;
    socket.on('error', () => {});
    socket.once('data', bytes => { pid = Number(bytes.toString()); resolve(); });
  }));
  const source = `require('node:child_process').spawn(process.execPath, ['-e', ${JSON.stringify(`require('node:net').connect(${(server.address() as AddressInfo).port}, '127.0.0.1', function () { this.write(String(process.pid)); });`)}], { stdio: 'ignore', detached: true }).unref();`;
  let result: ProcessExit | undefined;
  for await (const event of executeProcess({ executable: process.execPath, args: ['-e', source], cwd: process.cwd(), input: '' })) {
    if (event.type === 'exit') result = event;
  }
  await connected;
  assert.equal(result?.code, 0);
  assert.equal(result?.stopKind, null);
  assert.ok(pid);
  assert.doesNotThrow(() => process.kill(pid!, 0), 'this limitation must not be reported as Windows job-object isolation');
  const closed = new Promise<void>(resolve => job!.once('close', () => resolve()));
  process.kill(pid);
  await closed;
});
