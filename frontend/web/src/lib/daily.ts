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

/** What Argus says to the chief each day, unless the operator wrote other words. */
export const dailyText = (zh: boolean): string => zh
  ? '到每天来信的时间了：先看各项目从上次来信到现在的进展，哪些在等我；再按我的关注方向找新的论文、项目和消息。写成今天的信给我。'
  : "It is time for today's letter: first how each project has moved since the last one, and what waits for me; then what is new in the directions I follow. Write it to me as today's letter.";

/** The chief's first message: who it is for the operator, and what they follow. */
export const chiefRequest = (zh: boolean, interests: string): string => zh
  ? `你是我在 Argus 里的总负责人，替我照看所有项目，并盯着我关心的研究方向。

我关心的方向：
${interests}

每天给我写一封信：
1. 各项目从上次来信到现在的进展，哪些在等我回答或拍板；
2. 我关心方向上的新东西：arXiv 新论文、博客和 RSS、GitHub 上相关仓库的新版本和新项目。每条一两句说清楚是什么、为什么值得我看，附链接。

在工作目录里记下推过的东西，下次不重复。现在先写第一封。`
  : `You are my chief in Argus: you look after all my projects and follow the research directions I care about.

The directions I follow:
${interests}

Write me a letter each day:
1. How each project has moved since the last letter, and what waits for my answer or decision.
2. What is new in my directions: new arXiv papers, blogs and RSS, new releases and new projects on GitHub. A sentence or two each on what it is and why it is worth my time, with the link.

Keep a note in the workspace of what you have sent, so nothing comes twice. Write the first letter now.`;
