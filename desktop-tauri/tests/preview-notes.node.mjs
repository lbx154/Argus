import assert from 'node:assert/strict';
import { test } from 'node:test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { capturePreviewIdentity, PREVIEW_IDENTIFIER, PREVIEW_DATA_DIRECTORY, previewReadme, validatePreviewReview } from '../scripts/preview-notes.mjs';

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'argus-preview-provenance-'));
  const git = (...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  git('init', '--initial-branch=preview');
  writeFileSync(join(root, 'README.md'), 'Synthetic preview identity\n', { flag: 'wx' });
  git('add', '--', 'README.md');
  git('-c', 'user.name=Preview fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture');
  const base = git('rev-parse', 'HEAD');
  git('update-ref', 'refs/remotes/origin/dev', base);
  return { root, git, base };
}

test('preview identity is bound to the actual clean commit, tree and frozen base', () => {
  const { root, base, git } = fixture();
  assert.deepEqual(capturePreviewIdentity(root, base), {
    base_sha: base, candidate_sha: base, source_tree_sha: git('rev-parse', 'HEAD^{tree}'),
    identifier: PREVIEW_IDENTIFIER, data_directory: PREVIEW_DATA_DIRECTORY, reuses_previous_preview_profile: true,
  });
  assert.equal(capturePreviewIdentity(root).base_sha, base);
});
for (const staged of [false, true]) test(`dirty reviewed inputs rejected (staged=${staged})`, () => {
  const { root, base, git } = fixture();
  writeFileSync(join(root, 'README.md'), 'Synthetic change\n');
  if (staged) git('add', '--', 'README.md');
  assert.throws(() => capturePreviewIdentity(root, base));
});
test('untracked product inputs rejected while isolated audit files remain allowed', () => {
  const { root, base } = fixture();
  mkdirSync(join(root, 'validation'), { recursive: true });
  writeFileSync(join(root, 'validation/evidence.json'), '{}', { flag: 'wx' });
  assert.equal(capturePreviewIdentity(root, base).candidate_sha, base);
  mkdirSync(join(root, 'frontend/web/dist'), { recursive: true });
  writeFileSync(join(root, 'frontend/web/dist/new.js'), 'synthetic', { flag: 'wx' });
  assert.throws(() => capturePreviewIdentity(root, base), /Untracked product inputs/);
});
test('preview base must be a real full ancestor SHA', () => {
  const { root } = fixture();
  assert.throws(() => capturePreviewIdentity(root, 'dev'), /full frozen commit/);
  assert.throws(() => capturePreviewIdentity(root, '0'.repeat(40)));
});

const identity = { base_sha: 'b'.repeat(40), candidate_sha: 'c'.repeat(40), source_tree_sha: 'd'.repeat(40),
  identifier: PREVIEW_IDENTIFIER, data_directory: PREVIEW_DATA_DIRECTORY, reuses_previous_preview_profile: true };
const manifest = { release_id: '0.1.7+fixture', source_digest: 'a'.repeat(64) };
const review = { ...identity, ...manifest, tasks: { A1: '完成', A5: '未合入：DEFERRED_NO_EQUIVALENCE' },
  checks: [{ name: 'Synthetic example only', result: 'not a real test result' }], pending: ['真实 Windows Toast 待手测'] };
for (const key of ['candidate_sha', 'base_sha', 'release_id', 'source_digest']) test(`review evidence rejects a mismatching ${key}`, () => {
  assert.equal(validatePreviewReview(review, identity, manifest), review);
  assert.throws(() => validatePreviewReview({ ...review, [key]: 'wrong' }, identity, manifest), /does not match/);
});
test('notes accurately disclose identities, reused profile and manual/native scope', () => {
  const notes = previewReadme({ version: '0.1.7', manifest, identity, manualPreview: true, review, builtAt: 'synthetic date' });
  for (const value of [identity.base_sha, identity.candidate_sha, identity.source_tree_sha, manifest.source_digest,
    PREVIEW_IDENTIFIER, PREVIEW_DATA_DIRECTORY, '不能与旧预览并行运行', 'DEFERRED_NO_EQUIVALENCE', '真实 Windows Toast 待手测',
    '不是正式安装包', 'A 输入草稿', 'B 时点击 A', '知识库入口', '停止本地后端并退出']) assert(notes.includes(value), value);
  assert(!notes.includes('5b25300287296c40a8d6c58306a3923dd26c2937'));
});
test('missing source review is never presented as all checks passed', () => {
  const notes = previewReadme({ version: '0.1.7', manifest, identity, manualPreview: true, review: null, builtAt: 'synthetic date' });
  assert(notes.includes('未附带源码回归审查记录'));
});
test('existing native/copy/frozen checks still precede packaging and signing stays disabled', () => {
  const source = readFileSync(new URL('../scripts/build-preview.mjs', import.meta.url), 'utf8');
  assert(source.includes("'build', '--no-bundle', '--no-sign', '--features', 'preview'"));
  for (const check of ['copyVerified(source, target)', '--verify-frozen-runtime', 'smoke-host.py', 'generate_manifest',
    'check_artifacts', 'verifiedIdentity.candidate_sha !== identity.candidate_sha']) {
    assert(source.indexOf(check) > 0 && source.indexOf(check) < source.indexOf('CreateFromDirectory'));
  }
});
