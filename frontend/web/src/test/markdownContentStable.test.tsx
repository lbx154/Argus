import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { expect, it, vi } from 'vitest';
import { MarkdownContent } from '../components/MarkdownContent';

vi.mock('../i18n', () => ({ useI18n: () => ({ locale: 'zh-CN', t: (key: string) => key }) }));

it('keeps the same nodes when the page re-renders with equal content', () => {
  const text = '# 标题\n\n第一段，见 [报告](report.md)。';
  const open = vi.fn();
  let tree!: ReactTestRenderer;
  act(() => {
    tree = create(<MarkdownContent artifacts={[{ path: 'report.md' }]} onOpenArtifact={open}>{text}</MarkdownContent>);
  });
  const heading = tree.root.findByType('h1');
  const link = tree.root.findByType('a');
  // A poll hands down new array and callback identities with the same content.
  act(() => {
    tree.update(<MarkdownContent artifacts={[{ path: 'report.md' }]} onOpenArtifact={(path) => open('again', path)}>{text}</MarkdownContent>);
  });
  expect(tree.root.findByType('h1')).toBe(heading);
  expect(tree.root.findByType('a')).toBe(link);
  tree.root.findByType('a').props.onClick({ preventDefault() {} });
  expect(open).toHaveBeenCalledWith('again', 'report.md');
});
