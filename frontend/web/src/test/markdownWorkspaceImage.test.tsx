import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { expect, it, vi } from 'vitest';

const blob = vi.hoisted(() => vi.fn(() => new Promise<Blob>(() => undefined)));
vi.mock('../i18n', () => ({ useI18n: () => ({ locale: 'zh-CN', t: (key: string) => key }) }));
vi.mock('../api', () => ({ api: { artifactBlob: blob } }));
import { MarkdownContent } from '../components/MarkdownContent';

it('loads a relative image from the workspace before the artifact list lists it', () => {
  let tree!: ReactTestRenderer;
  act(() => { tree = create(<MarkdownContent sid="s-1" artifacts={[]}>{'![曲线](figures/plot.png)'}</MarkdownContent>); });
  expect(blob).toHaveBeenCalledWith('s-1', 'figures/plot.png', false, expect.anything(), undefined);
  expect(tree.root.findAllByType('img')).toHaveLength(0);
  blob.mockClear();
  act(() => { tree.update(<MarkdownContent sid="s-1" artifacts={[]}>{'![外链](https://example.com/a.png)'}</MarkdownContent>); });
  expect(blob).not.toHaveBeenCalled();
});


it('loads a Markdown image from the same delivery as its document', () => {
  let tree!: ReactTestRenderer;
  blob.mockClear();
  act(() => { tree = create(<MarkdownContent sid="s-1" delivery="d8" artifacts={[]}>{'![曲线](figures/plot.png)'}</MarkdownContent>); });
  expect(blob).toHaveBeenCalledWith('s-1', 'figures/plot.png', false, expect.anything(), 'd8');
  act(() => tree.unmount());
});
