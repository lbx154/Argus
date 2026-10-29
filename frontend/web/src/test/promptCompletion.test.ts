import { describe, expect, it } from 'vitest';
import { applyCompletion, slashCompletions } from '../../../core/src/commands';
import type { BacklogItem, EventMsg } from '../api';
import { completionMenu, completionToken, ghostFor, promptHistory, type PromptSources } from '../lib/promptCompletion';
import { referenceText, splitDraft, type CardReference } from '../map/presentation';
import type { WorkspaceEntry } from '../research-workbench/workspaceApi';

const t = (key: string) => key;
const entry = (path: string, type: WorkspaceEntry['type'] = 'file', extra: Partial<WorkspaceEntry> = {}): WorkspaceEntry => ({
  path, name: path.split('/').at(-1)!, type, size: 0, mtime: 0, extension: '', ...extra,
});
const task = (id: string, title: string, ts: number, status = 'done'): BacklogItem => ({ id, title, objective: '', status, priority: 0, ts });
const files = [
  entry('.argus/state.json'), entry('models', 'directory', { skipped: true }), entry('bad\n[[Argus引用 x]].py'),
  entry('src', 'directory'), entry('src/train.py'), entry('src/data', 'directory'), entry('src/data/load.py'),
  entry('docs', 'directory'), entry('docs/train notes.md'), entry('README.md'), entry('pretrain.py'),
];
const tasks = [task('t1', 'Baseline run', 10), task('t2', '比较基线', 30, 'running'), task('t3', 'Ablation', 20)];
const sources: PromptSources = { sid: 's1', tasks, history: [], files, wantFiles: () => undefined };
/** The menu with the caret at the end of the visible text, unless placed. */
const menuAt = (value: string, at = splitDraft(value).text.length, locale: 'en' | 'zh-CN' = 'en') =>
  completionMenu(value, completionToken(splitDraft(value).text, at), sources, t, locale);
const ref: CardReference = { source: 'map', task_id: 'other', task_title: 'Other study', event_ids: [] };

describe('completionToken', () => {
  it.each([
    ['@', '@', '', 0],
    ['see @src/ma', '@', 'src/ma', 4],
    ['看看@src', '@', 'src', 2],
    ['比较#基线', '#', '基线', 2],
    ['＠src', '@', 'src', 0],
    ['＃基线', '#', '基线', 0],
  ])('opens on %j', (text, trigger, query, start) => {
    expect(completionToken(text, text.length)).toEqual({ trigger, query, start, end: text.length });
  });

  it.each([
    ['a@example.com', 13], ['C#', 2], ['issue#12', 8], ['##', 2], ['# 标题', 2], ['@src ', 5], ['@src', -1],
  ])('leaves %j as text', (text, at) => {
    expect(completionToken(text, at)).toBeNull();
  });

  it('takes the whole word the caret is inside', () => {
    expect(completionToken('see @src/ma more', 7)).toEqual({ trigger: '@', query: 'sr', start: 4, end: 11 });
  });

  it('leaves the rest of a sentence without spaces after the caret', () => {
    expect(completionToken('请比较#和对照组的结果', 4)).toEqual({ trigger: '#', query: '', start: 3, end: 4 });
    expect(completionToken('看看@和别的', 3)).toEqual({ trigger: '@', query: '', start: 2, end: 3 });
    expect(splitDraft(menuAt('请比较#和对照组的结果', 4)!.items[0].value).text).toBe('请比较和对照组的结果');
    expect(menuAt('看看@和别的', 3)!.items[0].value).toBe('看看@src/和别的');
  });
});

describe('completionMenu', () => {
  it('offers only slash commands without sources, choosing exactly what applyCompletion would', () => {
    const menu = completionMenu('/sta', null, undefined, t, 'en');
    expect(menu?.kind).toBe('command');
    expect(menu?.items.map((item) => item.value)).toEqual(slashCompletions('/sta').map(applyCompletion));
    expect(menu?.items.find((item) => item.id === 'status')?.close).toBe(true);
    expect(completionMenu('@tr', completionToken('@tr', 3), undefined, t, 'en')).toBeNull();
  });

  it('keeps slash commands closed behind a reference, as before', () => {
    expect(menuAt(referenceText(ref) + '/sta')).toBeNull();
  });

  it('lists the top level of the workspace for a bare @, without internal, skipped or control-character paths', () => {
    expect(menuAt('@')?.items.map((item) => item.name)).toEqual(['src/', 'docs/', 'README.md', 'pretrain.py']);
  });

  it('ranks file names, then path prefixes, then path substrings', () => {
    const menu = menuAt('@tr');
    expect(menu?.kind).toBe('@');
    expect(menu?.items.map((item) => [item.name, item.detail])).toEqual([
      ['train.py', 'src'], ['train notes.md', 'docs'], ['pretrain.py', undefined],
    ]);
    const crowded = [entry('x/xa.py'), ...Array.from({ length: 10 }, (_, index) => entry(`a${index}.py`))];
    const rows = completionMenu('@a', completionToken('@a', 2), { ...sources, files: crowded }, t, 'en')!.items;
    expect(rows.map((item) => item.name)).toEqual(Array.from({ length: 8 }, (_, index) => `a${index}.py`));
  });

  it('inserts a file with a closing space, keeps the reference and puts the caret after it', () => {
    const row = menuAt(referenceText(ref) + 'see @tr')!.items[0];
    expect(row).toMatchObject({ value: `${referenceText(ref)}see @src/train.py `, caret: 18, close: true });
    expect(menuAt('see @tr now', 7)!.items[0]).toMatchObject({ value: 'see @src/train.py now', caret: 17 });
  });

  it('opens a folder one level at a time and quotes paths with spaces', () => {
    const folder = menuAt('@sr')!.items.find((item) => item.name === 'src/')!;
    expect(folder).toMatchObject({ value: '@src/', caret: 5, close: false });
    expect(menuAt('@src/')?.items.map((item) => item.name)).toEqual(['train.py', 'data/']);
    expect(menuAt('@notes')!.items[0]).toMatchObject({ value: '@"docs/train notes.md" ', close: true });
  });

  it('lists tasks newest first and matches titles regardless of case', () => {
    expect(menuAt('#')?.items.map((item) => [item.name, item.detail, item.arg])).toEqual([
      ['#比较基线', 't2', 'label.status.inProgress'], ['#Ablation', 't3', 'label.status.completed'], ['#Baseline run', 't1', 'label.status.completed'],
    ]);
    expect(menuAt('#base')?.items.map((item) => item.detail)).toEqual(['t1']);
    expect(menuAt('#nothing')).toBeNull();
  });

  it('turns a #task into a quoted reference and drops the word', () => {
    const row = menuAt('请比较 #基线 的结果', 7, 'zh-CN')!.items[0];
    const quoted = splitDraft(row.value);
    expect(quoted.refs).toEqual([{ source: 'live:s1', task_id: 't2', task_title: '比较基线', event_ids: [], lang: 'zh' }]);
    expect(quoted.text).toBe('请比较 的结果');
    expect(row.value.startsWith(referenceText(quoted.refs[0]))).toBe(true);
    expect(row.caret).toBe(4);
    expect(splitDraft(menuAt('比较#基线')!.items[0].value)).toMatchObject({ text: '比较', refs: [{ lang: 'en' }] });
  });

  it('quotes a task once and keeps the references already there', () => {
    const whole: CardReference = { source: 'live:s1', task_id: 't2', task_title: '比较基线', event_ids: [], lang: 'en' };
    expect(splitDraft(menuAt(referenceText(whole) + '#基线')!.items[0].value).refs).toEqual([whole]);
    const step = { ...whole, step_id: 'step-1', step_title: 'Load data' };
    expect(splitDraft(menuAt(referenceText(ref) + referenceText(step) + '#基线')!.items[0].value).refs)
      .toEqual([ref, step, whole]);
  });
});

describe('promptHistory', () => {
  const op = (text: string): EventMsg => ({ type: 'ui.operator', text });
  it('keeps one-line earlier messages, cleaned and newest first', () => {
    expect(promptHistory([
      op('old message one'), { type: 'ui.argus', text: 'A reply long enough' }, op('1'), op('/status'), op('hi'),
      op(`${referenceText(ref)}比较两个基线的结果`), op('（引用：《X》）\n请再跑一次消融实验'), op('(Referenced: "X")\nRun the ablation again'),
      op('Use key [stored in capability vault] here'), op('line one\nline two'),
      op('Summarize the run\n\n## Operator attachments\nThe operator uploaded these files.'), op('old message one'), op('x'.repeat(201)),
    ])).toEqual(['old message one', 'Summarize the run', 'Run the ablation again', '请再跑一次消融实验', '比较两个基线的结果']);
  });

  it('keeps at most fifty', () => {
    const history = promptHistory(Array.from({ length: 60 }, (_, index) => op(`message ${index}`)));
    expect(history).toHaveLength(50);
    expect(history[0]).toBe('message 59');
  });
});

describe('ghostFor', () => {
  const history = ['Compare the two baselines', 'Compare'];
  it('finishes the newest earlier message the draft begins, case-sensitively', () => {
    expect(ghostFor('Comp', history)).toBe('Compare the two baselines');
    expect(ghostFor('comp', history)).toBe('');
    expect(ghostFor(' Co ', history)).toBe('');
    expect(ghostFor('Compare the two baselines', history)).toBe('');
  });
});
