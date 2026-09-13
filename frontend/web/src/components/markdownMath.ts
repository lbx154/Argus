import type { Root } from 'hast';
import type { PluggableList } from 'unified';
import type { VFile } from 'vfile';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math-extended';
import rehypeKatex from 'rehype-katex';

export const markdownRemarkPlugins: PluggableList = [
  remarkGfm,
  [remarkMath, { backslashDelimiters: true, singleDollarTextMath: false }],
];

const mathOptions = { output: 'htmlAndMathml' as const };
const mathMacros = { '\\Sha': '\\mathord{\\text{Ш}}' };

function rehypeMathWithNotice({ zh }: { zh: boolean }) {
  return (tree: Root, file: VFile) => {
    // A processor can render several documents; \gdef must stay in this one.
    const renderMath = rehypeKatex({ ...mathOptions, macros: { ...mathMacros } });
    const firstMessage = file.messages.length;
    renderMath(tree, file);
    // Unsupported macros can render red text without a .katex-error element.
    const errors = file.messages.slice(firstMessage).filter(message => message.source === 'rehype-katex').length;
    if (!errors) return;
    tree.children.unshift({
      type: 'element', tagName: 'div',
      properties: {
        role: 'note', 'data-math-error-count': errors,
        className: ['my-2', 'rounded-md', 'border', 'border-line', 'px-3', 'py-2', 'text-xs', 'text-ink-dim'],
      },
      children: [{ type: 'text', value: zh
        ? `有 ${errors} 处公式暂时无法正确显示，原式已保留。`
        : `${errors} formula${errors === 1 ? '' : 's'} cannot currently be displayed correctly. The original expression${errors === 1 ? ' is' : 's are'} preserved.`,
      }],
    });
  };
}

export function markdownRehypePlugins(zh: boolean): PluggableList {
  return [[rehypeMathWithNotice, { zh }]];
}
