import type { Root } from 'hast';
import type { VFile } from 'vfile';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkRehype from 'remark-rehype';
import ReactMarkdown from 'react-markdown';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vitest';
import { MarkdownContent } from '../components/MarkdownContent';
import { markdownRemarkPlugins, markdownRehypePlugins } from '../components/markdownMath';
import { Markdown } from '../research-workbench/components/Common';

const language = vi.hoisted(() => ({ locale: 'en' }));
vi.mock('../i18n', () => ({ useI18n: () => ({ ...language, t: (key: string) => key }) }));
afterEach(() => { language.locale = 'en'; });

const surfaces = [['reader', MarkdownContent], ['workbench', Markdown]] as const;

it.each(surfaces)('%s renders fractions and the shared symbol macro with MathML and original TeX', (_name, Surface) => {
  const expression = String.raw`\frac{1}{2}+\Sha(E)`;
  const html = renderToStaticMarkup(<Surface>{`\\(${expression}\\)`}</Surface>);
  expect(html).toContain('<mfrac>');
  expect(html).toContain('Ш');
  expect(html).toContain(`<annotation encoding="application/x-tex">${expression}</annotation>`);
  expect(html).not.toContain('data-math-error-count');
  expect(html).not.toContain('mathcolor="#cc0000"');
});

for (const [name, Surface] of surfaces) {
  it.each([
    ['U+0005', '\u0005lambda'],
    ['U+000C', '\u000crac{1}{2}'],
    ['unknown macro', String.raw`\UnregisteredSymbol(E)`],
  ])(`${name} explains %s without guessing a replacement for its original expression`, (_kind, expression) => {
    const source = `Before \\(${expression}\\) after.`;
    const html = renderToStaticMarkup(<Surface>{source}</Surface>);
    expect(html).toContain('data-math-error-count="1"');
    expect(html).toContain('role="note"');
    expect(html).toContain('1 formula cannot currently be displayed correctly. The original expression is preserved.');
    expect(html).toContain(expression);
    expect(html).toContain('Before ');
    expect(html).toContain(' after.');
    if (_kind === 'unknown macro') {
      expect(html).not.toContain('katex-error');
      expect(html).toContain('mathcolor="#cc0000"');
    }
  });

  it(`${name} reports multiple failed formulas once in Chinese`, () => {
    language.locale = 'zh-CN';
    const source = String.raw`\(\FirstUnknown\) and \(\SecondUnknown\)`;
    const html = renderToStaticMarkup(<Surface>{source}</Surface>);
    expect(html.match(/data-math-error-count="2"/g)).toHaveLength(1);
    expect(html).toContain('有 2 处公式暂时无法正确显示，原式已保留。');
  });

  it(`${name} keeps document-defined macros within that document`, () => {
    const first = renderToStaticMarkup(<Surface>{String.raw`\(\gdef\PrivateMeaning{7}\gdef\Sha{LOCAL}\PrivateMeaning\) then \(\PrivateMeaning\)`}</Surface>);
    expect(first).not.toContain('data-math-error-count');
    const next = renderToStaticMarkup(<Surface>{String.raw`\(\Sha(E)\) and \(\PrivateMeaning\)`}</Surface>);
    expect(next).toContain('Ш');
    expect(next).not.toContain('LOCAL');
    expect(next).toContain('data-math-error-count="1"');
    expect(next).toContain(String.raw`\PrivateMeaning`);
  });
}

it('counts only new KaTeX diagnostics and leaves unrelated plugin messages intact', () => {
  let sources: Array<string | undefined> = [];
  const unrelated = () => (_tree: Root, file: VFile) => { file.message('Another plugin warning', { source: 'fixture' }); };
  const observe = () => (_tree: Root, file: VFile) => { sources = file.messages.map(message => message.source); };
  const html = renderToStaticMarkup(<ReactMarkdown remarkPlugins={markdownRemarkPlugins}
    rehypePlugins={[unrelated, ...markdownRehypePlugins(false), observe]}>{String.raw`\(\Unknown\)`}</ReactMarkdown>);
  expect(sources).toEqual(['fixture', 'rehype-katex']);
  expect(html).toContain('data-math-error-count="1"');
  expect(html).not.toContain('Another plugin warning');
});

it('isolates document macros when the same processor renders successive documents', () => {
  const processor = unified().use(remarkParse).use(markdownRemarkPlugins).use(remarkRehype)
    .use(markdownRehypePlugins(false)).use(function captureTree() {
      this.compiler = tree => JSON.stringify(tree);
    });
  const first = processor.processSync(String.raw`\(\gdef\PrivateMeaning{7}\gdef\Sha{LOCAL}\PrivateMeaning\) then \(\PrivateMeaning\)`);
  expect(String(first)).not.toContain('data-math-error-count');
  const next = processor.processSync(String.raw`\(\Sha(E)\) and \(\PrivateMeaning\)`);
  expect(String(next)).toContain('Ш');
  expect(String(next)).not.toContain('LOCAL');
  expect(String(next)).toContain('"data-math-error-count":1');
  expect(String(next)).toContain(String.raw`\PrivateMeaning`);
  expect(first.messages).toHaveLength(0);
  expect(next.messages.filter(message => message.source === 'rehype-katex')).toHaveLength(1);
});

it('preserves each surface’s existing links and code display', () => {
  const source = '[Report](REPORT.md)\n\n```js\nconst n = 2;\n```\n\n`' + String.raw`\frac{1}{2}` + '`';
  const reader = renderToStaticMarkup(<MarkdownContent artifacts={[{ path: 'REPORT.md' }]} onOpenArtifact={() => {}}>{source}</MarkdownContent>);
  expect(reader).toContain('data-artifact-path="REPORT.md"');
  expect(reader).toContain('copy.code');
  const workbench = renderToStaticMarkup(<Markdown className="existing-style">{source}</Markdown>);
  expect(workbench).toContain('class="markdown existing-style"');
  expect(workbench).toContain('target="_blank"');
  expect(workbench).toContain('lucide-external-link');
  expect(workbench).not.toContain('copy.code');
  for (const html of [reader, workbench]) {
    expect(html).toContain('language-js');
    expect(html).toContain('const n = 2;');
    expect(html).toContain(String.raw`\frac{1}{2}`);
    expect(html).not.toContain('<math');
    expect(html).not.toContain('data-math-error-count');
  }
});
