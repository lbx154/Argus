// The daily message's time is kept in UTC (HH:MM); the operator sees and gives it in their own time.

const pad = (value: number): string => String(value).padStart(2, '0');

/** HH:MM in UTC as the browser's local time of day. */
export function localTime(utc: string, now = new Date()): string {
  const [hours, minutes] = utc.split(':').map(Number) as [number, number];
  const at = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hours, minutes));
  return `${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

/** A local time of day (H:MM or HH:MM) in UTC as HH:MM; null when it is not a time of day. */
export function utcTime(local: string, now = new Date()): string | null {
  const match = /^\s*([01]?\d|2[0-3])[:：]([0-5]\d)\s*$/.exec(local);
  if (!match) return null;
  const at = new Date(now.getFullYear(), now.getMonth(), now.getDate(), Number(match[1]), Number(match[2]));
  return `${pad(at.getUTCHours())}:${pad(at.getUTCMinutes())}`;
}

/** A project's name as the page shows it: the chief's, till it has one, is "Chief". */
export const shownName = (project: { display_name?: string; chief?: boolean }, zh: boolean, unnamed: string): string =>
  project.display_name?.trim() || (project.chief ? zh ? '总负责人' : 'Chief' : unnamed);

/** What Argus says to the chief each day, unless the operator wrote other words. */
export const dailyText = (zh: boolean): string => zh
  ? '到每天来信的时间了：先看各项目从上次来信到现在的进展，哪些在等我；再按我的关注方向找新的论文、项目和消息。写成今天的信给我。'
  : "It is time for today's letter: first how each project has moved since the last one, and what waits for me; then what is new in the directions I follow. Write it to me as today's letter.";

/** What the operator may ask of the chief: each a need they tick, none typed. */
export const NEEDS = ['progress', 'papers', 'github', 'news', 'rest', 'often'] as const;
export type Need = (typeof NEEDS)[number];
/** The needs ticked to begin with. */
export const FIRST_NEEDS: readonly Need[] = ['progress', 'papers', 'github', 'rest'];

export const needLabel = (zh: boolean, need: Need): string => ({
  progress: zh ? '各项目的进展，哪些在等我拍板' : 'How each project moves, and what waits for my decision',
  papers: zh ? '我方向上的 arXiv 新论文' : 'New arXiv papers in my directions',
  github: zh ? 'GitHub 上相关项目的新版本、新仓库' : 'New releases and repositories on GitHub',
  news: zh ? '相关博客、新闻和行业动态' : 'Blogs, news and what the field is doing',
  rest: zh ? '项目做完、卡住或等我回答时，马上告诉我' : 'Tell me at once when a project finishes, is stuck, or waits for me',
  often: zh ? '每隔 4 小时看一眼，有值得说的就告诉我' : 'Look every 4 hours, and tell me when something is worth it',
})[need];

/** What Argus says to the chief every few hours. */
export const everyText = (zh: boolean): string => zh
  ? '到看一眼的时间了：从上次到现在，各项目和我关注的方向上有没有值得马上告诉我的？有就简短写给我；没有就只回一句"暂无新事"。'
  : 'Time for a look: since the last one, is there anything in the projects or my directions worth telling me now? If so, write it briefly; if not, answer only "Nothing new".';

/** What Argus says to the chief when another project's work comes to rest. */
export const restText = (zh: boolean): string => zh
  ? '有个项目刚停下来（下面是哪个项目、现在的状态）。看看它的记录：做完了就用两三句告诉我结果和成果在哪；卡住或在等我，就告诉我卡在哪、需要我做什么。'
  : 'A project has just come to rest (which, and how, below). Read its record: if it finished, tell me in two or three sentences what came of it and where; if it is stuck or waits for me, what on and what it needs from me.';

/** The chief's first message, from the needs the operator ticked and the directions, if they named any. */
export const chiefRequest = (zh: boolean, needs: readonly Need[], directions: string): string => {
  const asked = needs.filter(need => need !== 'rest' && need !== 'often').map(need => `- ${needLabel(zh, need)}`).join('\n');
  const named = directions.trim();
  return zh
    ? `你是我在 Argus 里的总负责人，替我照看所有项目、盯着我关心的方向，有值得说的就主动告诉我。

每天的信里我要：
${asked}

${named ? `我关心的方向：${named}` : '我没写关心的方向：先从我现有的项目推断，在第一封信开头列出来请我确认；我还没有项目的话，就先问我。'}

每条新东西用一两句说清楚是什么、为什么值得我看，附链接。在工作目录里记下推过的东西，以后不重复。现在先写第一封。`
    : `You are my chief in Argus: you look after all my projects, follow the directions I care about, and tell me of your own accord when something is worth it.

In each day's letter I want:
${asked}

${named ? `The directions I follow: ${named}` : 'I named no directions: infer them from my projects and list them at the head of the first letter for me to confirm; if I have no projects yet, ask me first.'}

A sentence or two on each new thing, what it is and why it is worth my time, with the link. Keep a note in the workspace of what you have sent, so nothing comes twice. Write the first letter now.`;
};
