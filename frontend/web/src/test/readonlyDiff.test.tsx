import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CopyButton } from '../components/CopyButton';
import { classifyDiff, MAX_DIFF_CHARS, MAX_DIFF_LINES, ReadonlyDiffView } from '../research-workbench/components/ReadonlyDiffView';

const normal = 'diff --git a/demo.txt b/demo.txt\nindex 1111111..2222222 100644\n--- a/demo.txt\n+++ b/demo.txt\n@@ -1,2 +1,2 @@ title\n context\n-before\n+after\n';
const rename = 'diff --git a/old.txt b/new.txt\nsimilarity index 100%\nrename from old.txt\nrename to new.txt\n';
let view: ReactTestRenderer | undefined;
afterEach(() => { if (view) act(() => view!.unmount()); view = undefined; vi.unstubAllGlobals(); });
function text(node: ReactTestInstance): string { return node.children.map(child => typeof child === 'string' ? child : text(child)).join(''); }
function render(diff: string, truncated = false) {
  act(() => { view = create(<ReadonlyDiffView diff={diff} truncated={truncated} />); });
  return view!;
}

describe('A4 lossless bounded read-only diff', () => {
  it('distinguishes headers, hunks, additions, deletions and context', () => {
    expect(classifyDiff(normal)?.map(line => line.kind)).toEqual(['header', 'header', 'header', 'header', 'hunk', 'context', 'deletion', 'addition']);
    expect(classifyDiff(normal)?.map(line => line.text).join('')).toBe(normal);
  });
  it.each([normal, normal.replaceAll('\n', '\r\n'), normal.slice(0, -1)])('preserves all original line endings and EOF text %#', diff => {
    const rendered = render(diff);
    expect(text(rendered.root.findByType('pre'))).toBe(diff);
    expect(rendered.root.findByType(CopyButton).props.text).toBe(diff);
  });
  it('recognizes rename-only and multi-file diffs', () => {
    expect(classifyDiff(rename)?.every(line => line.kind === 'header')).toBe(true);
    expect(classifyDiff(normal + rename)?.map(line => line.text).join('')).toBe(normal + rename);
  });
  it('recognizes a plain unified diff and the no-final-newline marker', () => {
    const unified = '--- a/file\n+++ b/file\n@@ -1 +1 @@\n-before\n+after\n\\ No newline at end of file\n';
    expect(classifyDiff(unified)?.map(line => line.text).join('')).toBe(unified);
  });
  it('does not mislabel header-like content inside a hunk', () => {
    const diff = 'diff --git a/file b/file\n--- a/file\n+++ b/file\n@@ -1 +1 @@\n---like a header\n+++also content\n';
    expect(classifyDiff(diff)?.slice(-2).map(line => line.kind)).toEqual(['deletion', 'addition']);
  });
  it.each([
    '', 'not a recognized patch\n',
    'diff --git a/file b/file\nBinary files a/file and b/file differ\n',
    'diff --git a/file b/file\nGIT binary patch\nliteral 2\nabc\n',
    'diff --cc file\n@@@ combined hunk @@@\n',
    'diff --git a/file b/file\nunexpected diagnostic\n',
    normal + '\0',
    normal + 'x'.repeat(MAX_DIFF_CHARS),
    `diff --git a/file b/file\n@@ -1,${MAX_DIFF_LINES} +1,${MAX_DIFF_LINES} @@\n${' context\n'.repeat(MAX_DIFF_LINES)}`,
  ])('falls back to one original pre for empty, unknown, binary or oversized content %#', diff => {
    expect(classifyDiff(diff)).toBeNull();
    const rendered = render(diff, true);
    const pre = rendered.root.findByType('pre');
    expect(pre.props['data-diff-mode']).toBe('raw');
    expect(text(pre)).toBe(diff);
    expect(rendered.root.findAllByProps({ 'data-diff-kind': 'addition' })).toHaveLength(0);
    expect(text(rendered.root.findByProps({ role: 'status' }))).toContain('backend truncated');
  });
  it('renders HTML and script-looking changes as text, never markup', () => {
    const diff = 'diff --git a/file b/file\n--- a/file\n+++ b/file\n@@ -1 +1 @@\n-<img src=x onerror="alert(1)">\n+<script>globalThis.fixtureExecuted=1</script>\n';
    const html = renderToStaticMarkup(<ReadonlyDiffView diff={diff} />);
    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('&lt;img');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<img');
    expect(classifyDiff(diff)?.at(-1)?.kind).toBe('addition');
  });
  it('switches to original text and copies the input, not the colored DOM', async () => {
    const copy = vi.fn(async () => undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText: copy } });
    const diff = normal.replaceAll('\n', '\r\n');
    const rendered = render(diff);
    const toggle = rendered.root.findByProps({ 'aria-pressed': false });
    act(() => toggle.props.onClick());
    expect(rendered.root.findByType('pre').props['data-diff-mode']).toBe('raw');
    expect(text(rendered.root.findByType('pre'))).toBe(diff);
    await act(async () => { rendered.root.findByProps({ 'aria-label': 'Copy original diff' }).props.onClick(); });
    expect(copy).toHaveBeenCalledExactlyOnceWith(diff);
    act(() => rendered.root.findByProps({ 'aria-pressed': true }).props.onClick());
    expect(text(rendered.root.findByType('pre'))).toBe(diff);
  });
  it('keeps the backend truncation notice even for an incomplete recognizable hunk', () => {
    const diff = normal.replace('@@ -1,2 +1,2 @@', '@@ -1,9 +1,9 @@');
    const rendered = render(diff, true);
    expect(text(rendered.root.findByType('pre'))).toBe(diff);
    expect(rendered.root.findByType('pre').props['data-diff-mode']).toBe('highlighted');
    expect(text(rendered.root.findByProps({ role: 'status' }))).toContain('incomplete');
  });
});
