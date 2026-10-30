import type { Root } from 'hast';
import type { Parent, Root as MarkdownRoot, Text } from 'mdast';
import type { PluggableList } from 'unified';
import type { VFile } from 'vfile';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math-extended';
import rehypeKatex from 'rehype-katex';

// What touches underscore emphasis that is part of a path: a separator
// before it, a separator or a file extension after it.
const PATH_SEPARATOR = /[\\/]/;
const PATH_AFTER = /^(?:[\\/]|\.\w)/;

/**
 * A path keeps its underscores. CommonMark counts `/` and `.` as punctuation,
 * so the `__` of "timefmt/__init__.py" opens and closes strong emphasis and the
 * link reads "timefmt/**init**.py"; the parser has no switch for underscores
 * alone. Emphasis written with underscores inside a path goes back to the text
 * it was written as.
 */
function remarkPathUnderscores() {
  return (tree: MarkdownRoot, file: VFile) => {
    const source = String(file.value);
    const restore = (parent: Parent) => {
      parent.children = parent.children.flatMap((child) => {
        if ('children' in child) restore(child);
        const start = child.position?.start.offset ?? -1;
        const end = child.position?.end.offset ?? -1;
        const inPath = PATH_SEPARATOR.test(source.charAt(start - 1)) || PATH_AFTER.test(source.slice(end, end + 2));
        if ((child.type !== 'emphasis' && child.type !== 'strong') || source[start] !== '_' || !inPath) return [child];
        const marker: Text = { type: 'text', value: child.type === 'strong' ? '__' : '_' };
        return [marker, ...child.children, { ...marker }];
      });
    };
    restore(tree);
  };
}

/** A bare URL ends at Chinese punctuation: it and what follows stay in the surrounding prose. */
function remarkUrlPunctuation() {
  return (tree: MarkdownRoot, file: VFile) => {
    const source = String(file.value);
    const trim = (parent: Parent) => {
      parent.children = parent.children.flatMap(child => {
        if ('children' in child) trim(child);
        if (child.type !== 'link' || child.children.length !== 1 || child.children[0]?.type !== 'text') return [child];
        const text = child.children[0];
        const written = source.slice(child.position?.start.offset, child.position?.end.offset);
        // Explicit Markdown links and <autolinks> retain the URL their author chose.
        if (written !== text.value) return [child];
        // A bare URL runs on to the next space, so the sentence after it may follow the punctuation.
        const cut = text.value.search(/[。，；：！？、）》」』】〕〉]/);
        if (cut < 0) return [child];
        const rest = text.value.slice(cut);
        text.value = text.value.slice(0, cut);
        child.url = child.url.slice(0, -rest.length);
        return [child, { type: 'text', value: rest } as Text];
      });
    };
    trim(tree);
  };
}

export const markdownRemarkPlugins: PluggableList = [
  remarkGfm,
  remarkUrlPunctuation,
  [remarkMath, { backslashDelimiters: true, singleDollarTextMath: false }],
  remarkPathUnderscores,
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

function rehypeExcerptMath() {
  return (tree: Root, file: VFile) => {
    // Cards have no room for the error notice; KaTeX keeps a failed formula's source.
    rehypeKatex({ ...mathOptions, macros: { ...mathMacros } })(tree, file);
  };
}

/** Math for one-line excerpts (map cards, notices): formulas render, nothing is added around them. */
export const excerptRehypePlugins: PluggableList = [rehypeExcerptMath];
