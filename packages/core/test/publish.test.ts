import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { spawnSync } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { isRemote, publish, PROGRESS_BRANCH, remoteUrl } from '../src/publish.js';

const run = (cwd: string, ...args: string[]): string => {
  const done = spawnSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', ...args], { cwd, encoding: 'utf8' });
  assert.equal(done.status, 0, `git ${args.join(' ')}: ${done.stderr}`);
  return done.stdout.trim();
};

async function repos(t: TestContext): Promise<{ work: string; remote: string }> {
  const root = await mkdtemp(join(tmpdir(), 'argus-publish-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const work = join(root, 'work');
  const remote = join(root, 'remote.git');
  run(root, 'init', '-q', '--bare', remote);
  run(root, 'init', '-q', '-b', 'main', work);
  writeFileSync(join(work, 'a.rs'), 'fn a() {}\n');
  run(work, 'add', '-A');
  run(work, 'commit', '-q', '-m', 'start');
  run(work, 'branch', 'argus/t1');
  return { work, remote };
}

test('a remote is a GitHub owner/repo, a URL or a path', () => {
  assert.equal(remoteUrl('noah-verification/virtiofs-argus-proof'), 'https://github.com/noah-verification/virtiofs-argus-proof.git');
  assert.equal(remoteUrl('/srv/x.git'), '/srv/x.git');
  for (const good of ['owner/repo', 'https://github.com/o/r.git', 'git@github.com:o/r.git', '/srv/r.git']) assert.ok(isRemote(good), good);
  for (const bad of ['repo', '--upload-pack=x', 'a b/c']) assert.ok(!isRemote(bad), bad);
});

test("publishing pushes every branch and Argus's page to a branch of its own, leaving the working tree alone", async t => {
  const { work, remote } = await repos(t);
  const first = await publish(work, remote, { 'PROGRESS.md': '# t1 open\n' });
  assert.equal(first.error, undefined);
  assert.deepEqual([...first.pushed].sort(), ['argus/t1', PROGRESS_BRANCH, 'main'].sort());
  assert.equal(run(remote, 'show', `${PROGRESS_BRANCH}:PROGRESS.md`), '# t1 open');
  assert.equal(run(work, 'status', '--porcelain'), '');
  // Nothing new: nothing is committed or pushed.
  assert.deepEqual((await publish(work, remote, { 'PROGRESS.md': '# t1 open\n' })).pushed, []);
  // A new page is the next commit of the page's history.
  writeFileSync(join(work, 'b.rs'), 'fn b() {}\n');
  run(work, 'add', '-A');
  run(work, 'commit', '-q', '-m', 'b');
  const second = await publish(work, remote, { 'PROGRESS.md': '# t1 closed\n' });
  assert.deepEqual([...second.pushed].sort(), [PROGRESS_BRANCH, 'main'].sort());
  assert.equal(run(remote, 'rev-list', '--count', PROGRESS_BRANCH), '2');
  assert.equal(run(remote, 'rev-parse', 'main'), run(work, 'rev-parse', 'main'));
});

test('a branch that went back in history is refused, never forced, and the others still go', async t => {
  const { work, remote } = await repos(t);
  await publish(work, remote, { 'PROGRESS.md': 'one\n' });
  run(work, 'checkout', '-q', '--orphan', 'other');
  writeFileSync(join(work, 'c.rs'), 'fn c() {}\n');
  run(work, 'add', '-A');
  run(work, 'commit', '-q', '-m', 'rewritten');
  run(work, 'branch', '-f', 'main', 'other');
  const result = await publish(work, remote, { 'PROGRESS.md': 'two\n' });
  assert.ok(result.rejected.some(line => line.startsWith('main')), JSON.stringify(result));
  assert.ok(result.pushed.includes('other') && result.pushed.includes(PROGRESS_BRANCH), JSON.stringify(result));
  assert.notEqual(run(remote, 'rev-parse', 'main'), run(work, 'rev-parse', 'main'));
});

test('a remote that cannot be reached says why', async t => {
  const { work } = await repos(t);
  const result = await publish(work, join(work, 'missing.git'), { 'PROGRESS.md': 'x\n' });
  assert.deepEqual([result.pushed, result.rejected], [[], []]);
  assert.ok(result.error, JSON.stringify(result));
});
