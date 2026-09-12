// One colleague-voiced sentence per recognized piece of harness plumbing.
// `match` applies only to text that carries an explicit "Runner receipt:"
// tail — proof the record came from the harness. `bare` is an exact,
// start-anchored emitted opening (round_stop_signals.py, _idle_cycle.py) for
// receipts that arrive without the marker. Research prose that merely talks
// about budgets or quarantine policy must never be replaced by a canned line.
const HARNESS_NOTES: Array<{ match?: RegExp; bare?: RegExp; kind?: "interrupt" | "reviewer" | "round"; zh: string; en: string }> = [
  {
    // The operator stopped Argus mid-round. Not a verdict on anything.
    match: /External interrupt|daemon (?:stop|shutdown)|stopped by its operator/i,
    bare: /^(?:Engineer interrupted because daemon shutdown|Argus was stopped by its operator|Reviewer backend returned no complete judgment \([^)]*External interrupt)/,
    kind: "interrupt",
    zh: "运行被操作员停下，这一轮没有做完；这不是对工作本身的评价",
    en: "Argus was stopped by its operator before this round could finish; this says nothing about the work itself",
  },
  {
    match: /no complete judgment|before reaching a judgment|reached a conclusion|did not reach a conclusion/i,
    bare: /^(?:Reviewer backend (?:returned no complete judgment|ended before reaching a judgment)|The Reviewer's session ended before it reached a conclusion|The review did not reach a conclusion)/,
    kind: "reviewer",
    zh: "审阅者的会话在得出结论前就结束了，这一轮没有评审意见",
    en: "The Reviewer's session ended before it reached a conclusion, so this round was not judged",
  },
  {
    bare: /^Engineer backend failed before a trustworthy completed turn/,
    kind: "reviewer",
    zh: "模型服务在工程师做完之前断开了；Argus 会换一个新会话重试，审阅等到那时再做",
    en: "The model service dropped the Engineer's session before it finished; Argus retries in a fresh session and the review waits until then",
  },
  {
    bare: /^Retry in a fresh (?:\w+ )?session/,
    zh: "Argus 会换一个新会话再试一次",
    en: "Argus will try again in a fresh session",
  },
  {
    bare: /^Restart the daemon when ready/,
    zh: "重新启动后，Argus 会从项目留下的状态继续，选择下一项具体任务",
    en: "Once Argus is started again it continues from the saved project state and picks the next concrete task",
  },
  {
    bare: /^engineer round \d+ \((?:fresh|rotated|resumed)[^)]*\)\s*$/i,
    kind: "round",
    zh: "工程师开始了新的一轮工作",
    en: "The Engineer began a new round of work",
  },
  {
    match: /provider[\s-]?turn/i,
    bare: /^(?:One Engineer call|\d+ Engineer sessions in a row each) used (?:its|their) whole per-call provider-turn allowance/,
    zh: "继续换了个新会话接着做，之前的进展都在",
    en: "Continued in a fresh session; earlier progress is kept",
  },
  {
    match: /budget (limit|cap|exhausted)|blocking budget|预算上限/i,
    bare: /^Paused because this project reached its budget limit/,
    zh: "花费到了预算上限，先暂停；提高预算后可以继续",
    en: "Paused at the budget limit; work resumes once the budget is raised",
  },
  {
    match: /quarantin/i,
    bare: /^The task signature is quarantined out of planner rotation/,
    zh: "这个方向连续失败，先搁置，不再自动重试",
    en: "This direction kept failing and is set aside; it will not retry on its own",
  },
  {
    match:
      /backend[\s\S]{0,24}?(fail|unavailable|paused)|backend_failure|provider cooldown|configured model is unavailable/i,
    bare: /^(?:backend failure; retrying in a fresh|The backend has failed the same way \d+ times in a row)/,
    zh: "模型服务暂时不稳定，稍后会自动重试",
    en: "The model service was briefly unavailable; it retries after a short wait",
  },
];

/** Where a record's plain sentence ends and its technical tail begins. Records
 * written today end with "Technical record:" (or "技术记录：" in Chinese);
 * older ones end with "Runner receipt:", and they are kept as they are. */
export const TECHNICAL_MARKER = /Runner receipt:|Technical record:|技术记录[:：]/i;
export const TECHNICAL_MARKER_PREFIX = /^(?:Runner receipt|Technical record|技术记录)\s*[:：]\s*/i;

/** Split one record at its explicit technical marker. Message callers pass one line
 * at a time so later sections, such as a recorded next action, stay visible. */
export function splitHarnessRecord(text: string): { prose: string; receipt: string } {
  const raw = String(text || "").trim();
  const marker = raw.search(TECHNICAL_MARKER);
  return marker < 0 ? { prose: raw, receipt: "" }
    : { prose: raw.slice(0, marker).trim(), receipt: raw.slice(marker).trim() };
}

/** Say what a harness record means for the research; move the raw receipt aside. */
export function humanizeHarnessNote(
  text: string,
  zh: boolean,
): { summary: string; receipt: string; kind?: "interrupt" | "reviewer" | "round" } {
  const raw = String(text || "").trim();
  const { receipt: tail } = splitHarnessRecord(raw);
  const rule = tail
    ? HARNESS_NOTES.find((note) => note.match?.test(raw))
    : HARNESS_NOTES.find((note) => note.bare?.test(raw));
  if (rule) return { summary: zh ? rule.zh : rule.en, receipt: tail || raw, kind: rule.kind };
  return { summary: "", receipt: tail };
}

