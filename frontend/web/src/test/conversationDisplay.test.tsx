import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { EventStream } from './serverSnapshot';
import { MarkdownContent } from '../components/MarkdownContent';
import { api } from '../api';

vi.mock('../lib/motion', () => ({ useGsapMotion: () => {} }));
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it('shows operator Markdown literally without image or link nodes', () => {
  const text = '# 原文\n\n![分布](dist.svg)\n**粗体语法** [文件](report.md)';
  const html = renderToStaticMarkup(<EventStream events={[{ type: 'ui.operator', text, ts: 1 }]} connected showReasoning={false} />);
  expect(html).toContain(text);
  expect(html).not.toContain('<img');
  expect(html).not.toContain('<h1');
  expect(html).not.toContain('<strong');
});

it('shows only alt text when a remote image fails', () => {
  let view!: ReturnType<typeof create>;
  act(() => { view = create(<MarkdownContent>{'![分布](https://example.test/missing.svg)'}</MarkdownContent>); });
  act(() => view.root.findByType('img').props.onError());
  expect(view.toJSON()).toMatchObject({ type: 'p', children: [{ type: 'span', children: ['分布'] }] });
  act(() => view.unmount());
});

it('shows only alt text when a workspace image request fails', async () => {
  vi.spyOn(api, 'artifactBlob').mockRejectedValue(new Error('missing image'));
  let view!: ReturnType<typeof create>;
  await act(async () => { view = create(<MarkdownContent sid="saved-project">{'![分布](dist.svg)'}</MarkdownContent>); });
  expect(view.toJSON()).toMatchObject({ type: 'p', children: [{ type: 'span', children: ['分布'] }] });
  act(() => view.unmount());
});

it('adds a local calendar date only to messages outside today', () => {
  vi.useFakeTimers();
  const today = new Date(2026, 9, 30, 12, 0, 0);
  vi.setSystemTime(today);
  const html = renderToStaticMarkup(<EventStream events={[
    { type: 'ui.operator', text: 'Yesterday', ts: new Date(2026, 9, 29, 23, 59, 58).getTime() / 1000 },
    { type: 'ui.argus', text: 'Today', ts: new Date(2026, 9, 30, 10, 11, 12).getTime() / 1000 },
  ]} connected showReasoning={false} />);
  expect(html).toContain('<time>2026-10-29 23:59:58</time>');
  expect(html).toContain('<time>10:11:12</time>');
});
