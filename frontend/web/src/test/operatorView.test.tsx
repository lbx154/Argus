import { fileURLToPath } from 'node:url';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vitest';
import { firstLine } from '../../../../packages/core/src/flow';
import { Store } from '../../../../packages/core/src/store';
import { conversationRows, projectRecord, readEvents, snapshot, type Live } from '../../../../packages/core/src/serve/views';
import { I18nProvider } from '../i18n';
import { EventStream } from './serverSnapshot';

// Real records the operator's view of was blind-rated (see fixtures/operator-view/README.md).
const store = new Store(fileURLToPath(new URL('./fixtures/operator-view', import.meta.url)));
const RUNS = { W1b: 'p-8abafb06', W2b: 'p-cd895d89', Rb: 'p-c3dcee05', W2a: 'p-e1c845b8', W2m2: 'p-0042b027' };
/** What the raters quoted against each run: team words on task cards, the Manager's process words. */
const QUOTED: Record<string, string[]> = {
  W1b: ['审核已通过', '按 software 技能的审核要求'],
  W2b: ['我先复核主要数字和使用入口', '我会复核专题中的影响数字和使用说明', '我会复核夜间、周末的计时边界', '18 项测试全部通过'],
  Rb: ['审阅已通过，报告和本地周报工具已完成复算验证', '独立复算已核对3726条导出明细'],
  W2a: ['Manager 的最新补充', '按 Planner 更新'],
  W2m2: ['我会复审这次权限恢复修复'],
};
const TASK_ID = /(?<![A-Za-z0-9_])t\d+(?![A-Za-z0-9_])/;
const ROLE_NAME = /\b(?:Manager|Planner|Engineer|Reviewer)\b/;
afterEach(() => vi.unstubAllGlobals());

/** The page's text as read without opening anything: a closed fold shows its summary alone. With
 * `gap` between elements, words stay apart; without, a sentence reads whole across its markup. */
const unopened = (html: string, gap: string): string => html
  .replace(/(<details(?![^>]*\sopen)[^>]*>\s*<summary[^>]*>[\s\S]*?<\/summary>)[\s\S]*?<\/details>/g, '$1</details>')
  .replace(/<!--[\s\S]*?-->/g, '').replace(/<[^>]+>/g, gap)
  .replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
const flat = (text: string): string => text.replace(/\s+/g, '');
/** Enough of a text's first line, as it reads rendered, to find it. */
const words = (text: string): string => flat(firstLine(text)).slice(0, 14);

it.each(Object.entries(RUNS))('%s: by default the operator reads its messages, the Manager\'s letters to it and who has which task, not the team\'s words', (name, id) => {
  vi.stubGlobal('localStorage', { getItem: () => 'zh-CN' });
  const record = readEvents(store, id);
  const project = store.load(id);
  const open = project.tasks.find(task => task.status === 'open');
  // Stopped as recorded, and as it was while an Engineer was writing on its open task.
  const lives: Live[] = [{ since: null, calls: [], requests: [] },
    ...open ? [{ since: 1, calls: [{ role: 'engineer' as const, task: open.id, started_ts: 1, words: '正在按 Planner 的 t3 要求核对。' }], requests: [] }] : []];
  for (const live of lives) {
    const now = live.since === null ? project : { ...project, status: 'working' as const };
    const rows = conversationRows(projectRecord(record), now, live);
    const html = renderToStaticMarkup(<I18nProvider><EventStream events={rows} snapshot={snapshot(store, now, live)} connected showReasoning={false} embedded /></I18nProvider>);
    const spaced = unopened(html, ' ');
    const read = flat(unopened(html, ''));
    for (const quote of QUOTED[name]!) {
      expect(record.some(event => (event.type === 'step' ? event.text : event.type === 'handoff' ? event.letter : '').includes(quote))).toBe(true);
      expect(read).not.toContain(flat(quote));
      // Kept, not lost: the reply's process folds, the team's letters open from the task.
      if (record.some(event => event.type === 'step' && event.text.includes(quote) && (event.role === 'manager' || event.role === 'planner'))) expect(html).toContain(quote);
    }
    expect(spaced).not.toMatch(TASK_ID);
    expect(spaced).not.toMatch(ROLE_NAME);
    expect(read).not.toContain('正在按Planner');
    record.forEach((event, line) => {
      // What the Manager said in a turn that handed work to the team is said to the operator: its
      // questions above all. Said before a letter to the operator, it was that letter's process.
      const letter = record.slice(line).find(later => later.type === 'handoff' && later.role === 'manager');
      if (event.type === 'step' && event.kind === 'message' && event.role === 'manager' && letter?.type === 'handoff') {
        if (letter.to === 'operator') expect(read).not.toContain(words(event.text));
        else expect(read).toContain(words(event.text));
      }
      if (event.type === 'message') expect(read).toContain(words(event.text));
      if (event.type === 'handoff' && event.role === 'manager' && event.to === 'operator') expect(read).toContain(words(event.letter));
      // Words between the team's roles, and the Planner's, stay out; a letter that opened a task
      // names it by its first line, the task's title.
      if (event.type === 'step' && event.kind === 'message' && event.role !== 'manager') expect(read).not.toContain(words(event.text));
      if (event.type === 'handoff' && event.role !== 'manager' && event.to !== 'operator' && !project.tasks.some(task => task.objective === event.letter)) {
        expect(read).not.toContain(words(event.letter));
      }
      // The Manager's letters to the team fold under the line saying whom it handed the work.
      const brief = event.type === 'handoff' && event.role === 'manager' && event.to !== 'operator' ? words(event.letter.split('\n').slice(1).join('\n')) : '';
      if (brief) expect(read).not.toContain(brief);
    });
    for (const task of project.tasks) {
      const title = words(task.title.replace(TASK_ID, '').replace(/^[\s\p{P}]+/u, ''));
      expect(read).toContain(`${task.status === 'closed' ? '已结束' : live.since === null ? '已停止' : '工程师在做'}：${title}`);
    }
  }
});
