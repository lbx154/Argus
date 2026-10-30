import { useState, type ComponentProps } from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatBox } from '../components/ChatBox';
import { promptCompletionOptionId } from '../components/PromptCompletionMenu';
import type { PromptSources } from '../lib/promptCompletion';
import { referenceText, splitDraft, type CardReference } from '../map/presentation';
import type { WorkspaceEntry } from '../research-workbench/workspaceApi';

const entry = (path: string, type: WorkspaceEntry['type'] = 'file'): WorkspaceEntry => ({
  path, type, size: 0, mtime: 0,
});
const ref: CardReference = { source: 'map', task_id: 'other', task_title: 'Other study', event_ids: [] };

let renderer: ReactTestRenderer | undefined;
let node: {
  selectionStart: number; selectionEnd: number; style: Record<string, string>;
  scrollHeight: number; clientHeight: number; clientWidth: number;
  getClientRects: () => unknown[]; addEventListener: () => void; removeEventListener: () => void;
  focus: ReturnType<typeof vi.fn>; setSelectionRange: ReturnType<typeof vi.fn>;
};
let changes: string[];
let send: ReturnType<typeof vi.fn<() => boolean>>;
let sources: PromptSources;
/** The model requests in flight, answered by hand. */

function Harness(props: Partial<ComponentProps<typeof ChatBox>> & { initial?: string }) {
  const [value, setValue] = useState(props.initial ?? '');
  const [selection, setSelection] = useState(0);
  return <ChatBox value={value} onChange={(next) => { changes.push(next); setValue(next); }} onSend={send}
    disabled={false} pending={false} attachments={[]} onAttachmentsChange={() => undefined}
    completionSelection={selection} onCompletionSelectionChange={setSelection} completion={sources} {...props} />;
}

const mount = (props: Partial<ComponentProps<typeof ChatBox>> & { initial?: string } = {}) => {
  act(() => {
    renderer = create(<Harness {...props} />, {
      createNodeMock: (element) => element.type === 'textarea' ? node : { style: {} },
    });
  });
  focus();
};
const host = (test: (instance: ReactTestInstance) => boolean) => renderer!.root.findAll((instance) => typeof instance.type === 'string' && test(instance));
const textarea = () => renderer!.root.findByType('textarea');
const composer = () => host((instance) => String(instance.props.className).includes('conversation-composer'))[0];
const listbox = () => host((instance) => instance.props.role === 'listbox')[0];
const options = () => host((instance) => instance.props.role === 'option');
const ghost = () => host((instance) => instance.props.className === 'composer-ghost')[0];
const status = () => host((instance) => instance.type === 'span' && instance.props.role === 'status')[0];
const focus = () => act(() => composer().props.onFocusCapture());
const FILES_LABEL = 'File and folder suggestions. Tab inserts the first, or the one the arrows pick.';
/** The selection event the textarea fires once its caret settles, after a keystroke or a move. */
const select = (text: string, caret: number) => act(() => {
  node.selectionStart = node.selectionEnd = caret;
  textarea().props.onSelect({ currentTarget: { value: text } });
});
/** What the textarea does on a keystroke: the DOM already holds the text and caret. */
const type = (text: string, caret = text.length) => act(() => {
  node.selectionStart = node.selectionEnd = caret;
  textarea().props.onChange({ target: { value: text } });
});
const press = (key: string, extra: Record<string, unknown> = {}) => {
  const event = {
    key, shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, keyCode: 0, defaultPrevented: false,
    nativeEvent: { isComposing: false }, preventDefault: vi.fn(() => { event.defaultPrevented = true; }), ...extra,
  };
  act(() => textarea().props.onKeyDown(event));
  return event;
};

beforeEach(() => {
  const listeners = { addEventListener() {}, removeEventListener() {} };
  vi.stubGlobal('document', { ...listeners, activeElement: null });
  vi.stubGlobal('window', { ...listeners, innerWidth: 800 });
  vi.stubGlobal('getComputedStyle', () => ({ fontFamily: 'Geist', fontSize: '15px', fontWeight: '400', lineHeight: '28px', letterSpacing: 'normal', marginTop: '4px', marginBottom: '4px' }));
  node = {
    selectionStart: 0, selectionEnd: 0, style: {}, scrollHeight: 28, clientHeight: 28, clientWidth: 400,
    getClientRects: () => [{}], addEventListener: () => undefined, removeEventListener: () => undefined, focus: vi.fn(),
    setSelectionRange: vi.fn((start: number, end: number) => { node.selectionStart = start; node.selectionEnd = end; }),
  };
  changes = [];
  send = vi.fn(() => false);
  sources = {
    sid: 's1', history: ['请比较两个基线的结果'], wantFiles: vi.fn(),
    tasks: [{ id: 't2', title: '基线对比', status: 'done', priority: 0 }],
    files: [entry('src', 'directory'), entry('src/train.py'), entry('src/tools', 'directory'), entry('src/tools/trace.py'), entry('train.md')],
  };
});
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  vi.unstubAllGlobals();
});

describe('@ and # in the main composer', () => {
  it('completes a file path with Tab and puts the caret after it', () => {
    mount();
    type('@tr');
    expect(sources.wantFiles).toHaveBeenLastCalledWith(true);
    expect(listbox().props['aria-label']).toBe(FILES_LABEL);
    expect(textarea().props['aria-activedescendant']).toBeUndefined();
    expect(press('Tab').preventDefault).toHaveBeenCalled();
    expect(changes.at(-1)).toBe('@src/train.py ');
    expect(node.setSelectionRange).toHaveBeenLastCalledWith(14, 14);
    expect(listbox()).toBeUndefined();
    expect(sources.wantFiles).toHaveBeenLastCalledWith(false);
  });

  it('keeps a chosen folder open to narrow the next level', () => {
    mount();
    type('@sr');
    press('Tab');
    expect(changes.at(-1)).toBe('@src/');
    expect(options().map((option) => option.props.children[0].props.children)).toEqual(['train.py', 'tools/']);
  });

  it('quotes a #task as a reference and drops the word', () => {
    mount({ initial: referenceText(ref) });
    type('请看 #基线');
    expect(listbox().props['aria-label']).toBe('Task suggestions. Tab quotes the first, or the one the arrows pick.');
    press('Tab');
    const draft = splitDraft(changes.at(-1)!);
    expect(draft.text).toBe('请看 ');
    expect(draft.refs).toEqual([ref, { source: 'live:s1', task_id: 't2', task_title: '基线对比', event_ids: [], lang: 'en' }]);
  });

  it('sends on Enter until the arrows pick a row', () => {
    mount();
    type('@tr');
    // Nothing looks chosen for Enter; the first row shows that Tab takes it.
    expect(options().map((option) => option.props['aria-selected'])).toEqual([false, false, false]);
    expect(options()[0].findByType('kbd').props.children).toBe('Tab');
    press('Enter');
    expect(send).toHaveBeenCalledWith('@tr', []);
    press('ArrowDown');
    expect(options().map((option) => option.props['aria-selected'])).toEqual([false, true, false]);
    expect(options()[0].findAllByType('kbd')).toHaveLength(0);
    expect(textarea().props['aria-activedescendant']).toBe(promptCompletionOptionId('file-1'));
    press('Enter');
    expect(changes.at(-1)).toBe('@src/tools/trace.py ');
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('opens a menu only where typing left the caret, so a moved caret still sends on Enter', () => {
    mount();
    const draft = '@tr 再看 #基线';
    type(draft, 3);
    select(draft, 3); // the keystroke's own selection event
    expect(listbox().props['aria-label']).toBe(FILES_LABEL);
    press('ArrowDown');
    select(draft, draft.length);
    expect(listbox()).toBeUndefined();
    expect(textarea().props['aria-activedescendant']).toBeUndefined();
    expect(press('ArrowUp').preventDefault).not.toHaveBeenCalled();
    press('Enter');
    expect(send).toHaveBeenCalledWith(draft, []);
    expect(changes).toEqual([draft]);
  });

  it('replaces only the typed word inside a sentence without spaces', () => {
    mount();
    type('请比较#和对照组的结果', 4);
    press('Tab');
    expect(splitDraft(changes.at(-1)!).text).toBe('请比较和对照组的结果');
  });

  it('leaves Shift+Tab to move focus while a menu is open', () => {
    mount();
    type('@tr');
    const event = press('Tab', { shiftKey: true });
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(changes.at(-1)).toBe('@tr');
    expect(listbox()).toBeDefined();
  });

  it('has no @ menu or gray text on the landing page', () => {
    mount({ completion: undefined });
    type('@tr');
    expect(listbox()).toBeUndefined();
    type('请比较');
    expect(ghost()).toBeUndefined();
    expect(textarea().props['aria-autocomplete']).toBe('list');
    expect(status()).toBeUndefined();
  });
});

describe('gray text from earlier messages', () => {
  it('finishes an earlier message; Tab takes it and keeps the references', () => {
    mount({ initial: referenceText(ref) });
    type('请比较');
    expect(textarea().props['aria-autocomplete']).toBe('both');
    expect(ghost().props['aria-hidden']).toBe('true');
    expect(ghost().props.children).toEqual(['请比较', expect.anything()]);
    expect(ghost().findByType('span').props.children).toBe('两个基线的结果');
    expect(press('Tab').preventDefault).toHaveBeenCalled();
    expect(changes.at(-1)).toBe(`${referenceText(ref)}请比较两个基线的结果`);
    expect(node.setSelectionRange).toHaveBeenLastCalledWith(10, 10);
    expect(ghost()).toBeUndefined();
  });

  it('announces the whole suggestion once while the typing follows it', () => {
    mount();
    type('请比较');
    const hint = 'Suggestion: 请比较两个基线的结果. Tab accepts, Esc dismisses.';
    expect(status().props.children).toBe(hint);
    type('请比较两个');
    expect(ghost().findByType('span').props.children).toBe('基线的结果');
    expect(status().props.children).toBe(hint);
    type('请比较三');
    expect(ghost()).toBeUndefined();
  });

  it('dismisses with Esc, and the suggestion stays away as the typing follows it', () => {
    mount();
    type('请比较');
    expect(press('Escape').preventDefault).toHaveBeenCalled();
    expect(ghost()).toBeUndefined();
    type('请比较两');
    expect(ghost()).toBeUndefined();
    expect(status().props.children).toBe('');
  });

  it('stays away while a reply runs', () => {
    mount({ pending: true });
    type('请比较');
    expect(ghost()).toBeUndefined();
  });

  it('hides once the textarea loses focus, during composition, off the end of the text and when the textarea scrolls', () => {
    mount();
    type('请比较');
    // Focus moves to the attach button: still inside the composer, but Tab and Esc no longer reach the textarea.
    act(() => composer().props.onBlurCapture({ currentTarget: { contains: () => true }, relatedTarget: {} }));
    act(() => textarea().props.onBlur());
    expect(ghost()).toBeUndefined();
    expect(status().props.children).toBe('');
    select('请比较', 3);
    expect(ghost()).toBeDefined();
    act(() => textarea().props.onCompositionStart());
    expect(ghost()).toBeUndefined();
    act(() => textarea().props.onCompositionEnd({ currentTarget: { value: '请比较' } }));
    expect(ghost()).toBeDefined();
    select('请比较', 1);
    expect(ghost()).toBeUndefined();
    node.scrollHeight = 200;
    node.clientHeight = 132;
    type('请比较 ');
    type('请比较');
    expect(ghost()).toBeUndefined();
  });

  it('ignores a Tab that belongs to the input method', () => {
    mount();
    type('请比较');
    const event = press('Tab', { nativeEvent: { isComposing: true } });
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(changes.at(-1)).toBe('请比较');
  });

  it('leaves Tab and Shift+Tab alone with no menu and no gray text', () => {
    mount();
    type('nothing like it');
    expect(press('Tab').preventDefault).not.toHaveBeenCalled();
    expect(press('Tab', { shiftKey: true }).preventDefault).not.toHaveBeenCalled();
  });

  it('sends only what was typed on Enter', () => {
    mount();
    type('请比较');
    press('Enter');
    expect(send).toHaveBeenCalledWith('请比较', []);
  });
});
