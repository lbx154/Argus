import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { MarkdownExcerpt } from '../components/MarkdownExcerpt';
import { MarkdownContent } from '../components/MarkdownContent';

it('renders card formatting without exposing Markdown markers or nesting links/buttons', () => {
  const html = renderToStaticMarkup(<button><MarkdownExcerpt>{'## **验证结果**\n\n- `A*` 通过\n- [报告](REPORT.md)\n\n| 策略 | 耗时 |\n| --- | --- |\n| 最短路 | 12 |\n\nRESULT=30 组验证通过'}</MarkdownExcerpt></button>);
  expect(html).toContain('<strong>验证结果</strong>');
  expect(html).toContain('<code>A*</code>');
  expect(html).not.toContain('**'); expect(html).not.toContain('##'); expect(html).not.toContain('RESULT=');
  expect(html).not.toContain('<a '); expect(html.match(/<button/g)).toHaveLength(1);
  expect(html).not.toContain('| ---'); expect(html).toContain('30 组验证通过');
});

it('renders full GFM, equations, code and delivery links using the shared reader', () => {
  const source = '# 城市验证\n\n**结论** 与 *限制*\n\n- [x] 通过\n\n| 方法 | 距离 |\n| --- | --- |\n| A* | 12 |\n\n```js\nconst cost = 12;\n```\n\n\\(d = v t\\)\n\n[打开报告](REPORT.md)';
  const html = renderToStaticMarkup(<MarkdownContent artifacts={[{ path: 'REPORT.md' }]} onOpenArtifact={() => {}}>{source}</MarkdownContent>);
  expect(html).toContain('<table'); expect(html).toContain('<th'); expect(html).toContain('<strong');
  expect(html).toContain('<em>'); expect(html).toContain('type="checkbox"'); expect(html).toContain('<pre');
  expect(html).toContain('katex'); expect(html).toContain('data-artifact-path="REPORT.md"');
  expect(html).not.toContain('| ---');
});
