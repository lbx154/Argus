# Argus best practices

This page explains how to get good work out of Argus and why each
recommendation holds. The reasons come from the code paths that read your
input; the examples come from three real projects run on one machine on
2026-09-30 (a GPU roofline research campaign, a damped-oscillator task, and a
first task on a fresh local install, not the hosted browser trial), with times
and costs taken from their logs. Nothing here is a quota; where a number appears it is a measurement, not a rule.

Read [getting started](getting-started.md) first if you have not run a task
yet.

## Writing an objective

The objective is read by four different consumers, and writing for all of
them is what makes an objective good:

1. **The Manager** decides from your text alone whether this is a
   conversation, a small local job it can do itself, or team work for the
   Planner, Engineer and Reviewer. It also decides the lifetime: finite,
   casually worded work defaults to *bounded* (stop when done); only text that
   expresses ongoing intent becomes a *standing* campaign that keeps generating
   work.
2. **The Manager again** picks the vertical (research, software, math, ...)
   from the objective; there is no keyword classifier and no flag to force it,
   so name the kind of work in plain words.
3. **The Planner** decomposes it into tasks, each with an acceptance check.
   What you leave unsaid, it will decide for you.
4. **The Reviewer** judges every round against it and against the vertical's
   stage checklist. The Reviewer runs read-only and cannot ask the Engineer;
   it can only check what the objective and the work directory let it check.

So an objective should say what must be true at the end, what evidence you
will accept, what the scope and constraints are, and what form the result
takes. Compare the two objectives that ran on this machine.

The campaign objective (`objective-roofline.txt`, project `s-02d3c282`):

> 做一项小规模但真实的研究：在本机 4 块 NVIDIA RTX A6000 上，PyTorch 方阵乘法的实际吞吐（TFLOPS）随矩阵规模（512 到 16384）和精度（FP32、TF32、BF16、FP16）如何变化，与理论峰值的差距能否用一个简单的 roofline 式模型（计算强度与显存带宽）解释。要求：真实实验、每种配置多次重复取中位数并报告离散度，拟合模型并给出误差，画图，最后写成一篇 4 页以内的短论文（含方法、结果、讨论、复现说明），并通过内部评审。所有数字必须来自本机真实运行，不允许估算或编造。

Every clause did work. "小规模但真实的研究" and "短论文" put it in the `research`
vertical (`pipeline : vertical=research` in the status). The hardware and the
ranges fixed the scope, so the Planner did not have to guess a sweep. "多次重复取中位数并报告离散度"
and "拟合模型并给出误差" became things the Reviewer could check: the draft's
`results.tex` reports 17,280 samples, medians, and the fit's error on a held-out
confirmation set. "通过内部评审" is why a `paper` round came back
`replan_requested` instead of being accepted on the Engineer's word. And
"所有数字必须来自本机真实运行，不允许估算或编造" is the sentence that lets the Reviewer
refuse a plausible-looking number that has no raw file behind it.

The small objective (`objective-oscillator.txt`, project `s-4a34a674`):

> 用 numpy 数值求解一维阻尼谐振子 x'' + 2γx' + ω0² x = 0（取 ω0=2π, 分别取欠阻尼 γ=0.5、临界 γ=2π、过阻尼 γ=10），与解析解逐点比较给出最大误差，画出三条曲线，把方法、数值结果表和图写进本目录的 README.md。所有数字必须来自真实运行。

It names the equation, the three parameter values, the comparison ("与解析解逐点比较给出最大误差"),
the deliverable and its location. Because the acceptance check was in the
text, the task ran once, was sent back once, and was done after 12 minutes and
$0.91.

The browser message from the trial (project `s-67fb6d62`) shows the casual end
of the scale:

> 帮我在这台机器的 GPU 上测一下 PyTorch 矩阵乘在 fp16 和 fp32 下的 TFLOPS，写个脚本跑出真实数字，整理成表格。

"写个脚本跑出真实数字" and "整理成表格" were enough for a bounded, self-contained job
that replied with a per-GPU table three minutes later. What it did not say
(matrix sizes, repeats) the Engineer chose: 4096/8192/16384 and seven timed
batches. If those choices matter to you, say them.

Things to avoid in an objective, and why:

- **Work that needs your credentials, your money, or an irreversible or
  public action.** Those are authority boundaries: whatever the autonomy
  setting, a task that reaches one stops and asks. Put the credential in
  place first (the cockpit accepts credentials and stores them in the
  capability vault) or keep the objective short of the boundary.
- **A goal with no checkable end.** "Make it better" leaves the Reviewer
  nothing to refuse, so rounds continue until a round cap or you intervene.
- **Numbers you already know the answer to.** The roles will try to
  reproduce them; that is the point, and it costs rounds.

On the command line the objective must travel with `--continuous`
(`--objective` alone is refused), and `--objective-file` keeps long text out of
shell quoting. Add `--bounded` when the objective is finite; without it the
worker keeps proposing follow-up work after the project is declared done.

## Avoiding unproductive experiment reruns

Start with the cheapest check that can actually reject an unready experiment:
the required tool must work in the execution environment, inputs must exist,
and shared preparation must succeed. A readiness pass is not a scientific
result. Keep dependencies inside an existing script or Make target where
possible, and propagate failures instead of launching their dependents.

When preparation is already a background job, a dependent submission can bind
its successful receipt:

```bash
python -m argus.tools.subagent submit --task-id experiment \
  --command "python run_experiment.py" --depends-on preparation
```

Replace the command and prerequisite with those of your project. The
prerequisite must already be `done` with an actual exit code of zero in the
same project's task registry. Missing, running, or failed prerequisites cause
submission to return exit code 1 with `state: blocked`; no dependent job is
created. This is not a queue that starts automatically when preparation
finishes. The worker checks the recorded prerequisite `run_id` again before
execution, including after resource admission. A replaced prerequisite is
rejected. `--no-preflight` cannot disable this dependency check.

Resubmitting the same task with the same command requires
`--rerun-reason "what changed, or why an independent replay is necessary"`.
The new receipt and report retain that explanation and the previous
`run_id`. A changed command is recorded as such without another explanation.
Neither a reason string nor a different task name proves that the inputs
changed. Reuse accepted evidence whose inputs are unchanged, and preserve
failed attempts instead of overwriting them.

Argus keeps its job logs and receipts per run; arbitrary output files written
by your command still need separate attempt directories. The runtime does not
infer hidden dependencies inside scripts or certify their numerical results.
Engineer and Reviewer share the distinction between readiness and validation;
producer-written logs cannot replace an independent checker or external
observation required by the acceptance contract.

## Changing direction while it runs

Argus gives you three ways to speak to a running project. They are not
interchangeable, because they enter at different points of the loop.

| You want to | Command line | Cockpit | Web API | What happens |
|---|---|---|---|---|
| Add guidance the next round should read | `argus --notify "text"` | `/nudge text` | `POST /api/projects/{sid}/nudge` | the text is queued in the project's durable inbox and spliced into the next Engineer round's prompt as operator guidance |
| Hold guidance until a later stage | `argus --notify "text" --notify-stage paper` | | | same, delivered only when the project reaches that stage (aliases such as `writeup` are canonicalized; unknown stages are refused) |
| Change the direction of the current task, pause or abort | (type it) | type it, or `/abort` | `POST /api/projects/{sid}/message` | the Manager classifies the message: `STEER` records a new direction for the active task, `PAUSE` stops the campaign, `ABORT` ends the task, `NO_DISPATCH` stops new work |
| Set a rule for the whole project | (type it) | type it | `POST .../message` | text that sounds standing ("always", "never", "from now on", "do not ask") is stored as a project-wide directive; other amendments are scoped to the current task |
| Answer a question the task stopped on | `argus --answer "text"` (`--answer-item ID` if several wait) | answer in the pending-question prompt | `POST /api/projects/{sid}/backlog/{item_id}/answer` | the paused task is replaced by a continuation whose objective carries your answer as authority, and the worker is restarted if needed |
| Ask something without touching the run | `argus --ask "question"` | `/ask question` | `route_override: "chat"` on `/message` | the Manager answers from project state; nothing is queued |

Why the nudge is deliberately weak: it is guidance the next round *reads*, not
an order the runtime *enforces*. The inbox is durable (a queue under the
project state that survives restarts, acknowledges only after delivery, and
answers HTTP 429 when full), but the Engineer decides what to do with the
text. That is the right tool for "prefer torch.compile off" or "the paper must
cite X"; it is the wrong tool for "stop".

Why `--answer` is not a nudge: a task that stopped to ask a question is
`paused` until the question is answered. Clearing the question and resuming
the same task looks like it works but does not: the task re-reads the
objective that made it ask and asks again (the code comment on `--answer`
records a campaign that burned five attempts that way). The answer path
therefore enqueues a continuation whose objective states your answer, so the
next round reads what it was told.

Why a typed message is classified rather than obeyed literally: the same box
carries greetings, questions, new tasks, settings changes and controls. The
Manager first decides whether the message is conversation or work, and only
when a task is active and the text clearly redirects it does the second check
turn it into a `STEER`. Questions, criticism and suggestions are not steering;
if you mean "change course", say so in the imperative.

When Argus stops on its own to ask you is governed by one setting:

```bash
export ARGUS_SKILL_AUTONOMY_MODE=pragmatic    # default
```

`pragmatic` recovers technical problems (a failed test, a timeout, a route
that did not work) without asking and stops only at authority boundaries:
credentials, payment or a bigger budget, deleting or force-pushing shared
state, publishing or sending outward, and changes to an acceptance contract
you own. `cautious` asks on every explicit question the Reviewer raises.
`autonomous` still stops at those same boundaries; it only removes the
remaining discretionary pauses. Pick `cautious` for the first run of a new
vertical, when you want to see what it would have asked.

Two real cases show what interjection looks like in practice.

*The follow-up.* In the trial's browser project, after the first table
arrived, the second message at 07:03:44Z was

> 现在项目里都有什么文件？把 bf16 也补测一下，更新表格。

Three minutes later the reply listed the project's files and gave the updated
table with a BF16 column. That message is a new bounded task in the same
project, not a steer of a running one; the earlier task had already finished.
Most "changes of direction" on small tasks are like this, and the message box
is the right place for them.

*The pause that was not a question.* The roofline campaign's log shows, 43
seconds after launch, a round ending with "The work was paused before the
Engineer finished this round because provider charges are awaiting
reconciliation or explicit risk approval, so this round was not judged".
Nothing was asked of the operator; the cost check had refused an unpriced
call. The operator's response was two CLI commands recorded in
`daemon.commands.jsonl`: a `drain` at 08:16:12Z and a `start` at 08:21:19Z
after changing the cost policy. Over the following two hours the campaign ran
without a single operator message: all twenty entries in its inbox were
background-job reports the runtime queued for itself. A precise objective is
what made that possible.

## Choosing the backend and model

Argus resolves the backend and model for each role separately, and every
resolution follows the same order:

1. an environment variable for that role (`ARGUS_SKILL_ENGINEER_BACKEND`,
   `ARGUS_SKILL_ENGINEER_MODEL`, ...),
2. the shared variable (`ARGUS_SKILL_RUNNER_BACKEND`, `ARGUS_SKILL_MODEL`),
3. the same names in the persisted settings file `~/.argus-skill/config.json`,
   which is what `argus --setup`, `/backend`, `/config key=value` and a
   natural-language "把模型换成 X" write,
4. the default (`codex` for the backend; `auto` for the model, meaning the
   backend's own default).

A `--backend` typed on a `--daemon` launch sits above all of these for that
daemon and is exported to its roles at boot, so a persisted choice can never
silently override what you typed. `argus --config-help` prints every setting
with its default, its current value and where the value came from
(`env`, `persisted`, `default`); `argus --config-snapshot` writes the same to
a file you can attach to a report.

| Setting | Default | Notes |
|---|---|---|
| `ARGUS_SKILL_RUNNER_BACKEND` | `codex` | `codex`, `claude`, `copilot`, `cursor`, `opencode`, `pi`, `grok`, `qoder`, `dsh` |
| `ARGUS_SKILL_<ROLE>_BACKEND` | inherits | `ENGINEER`, `REVIEWER`, `PLANNER`, `MANAGER`, `SUPERVISOR` |
| `ARGUS_SKILL_MODEL` | `auto` | a bare model id (`gpt-5.6-sol`, `copilot/opus-5`); free text reaches the CLI verbatim and every call then fails |
| `ARGUS_SKILL_<ROLE>_MODEL` | `auto` | `ENGINEER`, `REVIEWER`, `PLAN`, `MANAGER`, `SUPERVISOR` |
| `ARGUS_SKILL_FRONTDOOR_MODEL` | `auto` | the cheap classifier that reads every message (`gpt-5.4-mini` on Copilot) |
| `ARGUS_SKILL_<ROLE>_REASONING_EFFORT` | Engineer `xhigh`, others `high`, Supervisor `low` | `low`, `medium`, `high`, `xhigh`, `max` |
| `ARGUS_SKILL_PI_PROVIDER` / `ARGUS_SKILL_OPENCODE_PROVIDER` | unset | provider prefix for bare model ids on those two backends; on OpenCode the model is dropped without it |

Why the roles are separable: they do different jobs. The Engineer runs long
tool-using turns and benefits from the strongest model at high effort; the
Reviewer reads and judges; the front door classifies one short message per
turn and should be cheap. The roofline campaign ran on Copilot with
`ARGUS_SKILL_MODEL=gpt-5.6-sol`, and its `usage.jsonl` splits as: $29.22 on
`gpt-5.6-sol` across engineer, planner, reviewer, manager and supervision
calls; $0.16 on `gpt-5.4-mini` for classification, reflection and fact
extraction. Changing the front-door model would have saved nothing; changing
the Engineer's would have changed everything.

The same ledger explains where the money goes: 33.9 million input tokens
against 0.3 million output tokens after two hours. Long tool-using rounds
re-read context, which is why Argus by default sends the full task text only
on the first round of a session (`ARGUS_SKILL_COMPACT_CONTINUATION_PROMPTS`),
why the Engineer's agent CLI runs without the few tools of its own that it
never uses (`ARGUS_SKILL_ENGINEER_LEAN_TOOLS`), and why a precise objective
with the evidence already in the work directory costs less than a vague one
that makes the Engineer explore.

## Controlling spend

Argus records every provider call in `~/.argus-skill/projects/<id>/usage.jsonl`
(model, tokens, `cost_usd` and a `pricing_status` of `priced`, `partial`,
`unpriced` or `not_billed`). `argus --status` and `argus --watch` print
`spend    : today $X.XX across all projects`; the cockpit's cost panel shows the
project's own spend beside that figure, read from `GET /api/projects/costs`.
Late Copilot billing is reconciled when it arrives. A damaged ledger is repaired
in place: the damaged lines are set aside beside the journal and the complete
records are kept.

These figures are for reading, not for refusing work. Argus has no spending cap
of its own and never pauses a project because of cost. On your own subscription,
usage and limits are the provider's and are shown there; on an Argus-hosted
trial, the limit is the invitation's token allowance, enforced at the gateway
URL.

What Argus does limit is capacity, because one subscription CLI is shared by
every project on the host. `ARGUS_SKILL_PROVIDER_MAX_CONCURRENCY` (default `0`,
off) bounds concurrent provider processes and
`ARGUS_SKILL_PROVIDER_SLOT_WAIT_SECONDS` (default `45`) how long a call waits for
a slot. Both apply to every backend alike; no provider gets its own counter.
`ARGUS_SKILL_MAX_ACTIVE_DAEMONS` (default `64`) caps running daemons. `--mission-width` (default 2) is the
per-project counterpart; the roofline campaign used `1`, which is the right
choice when the tasks share four GPUs.

## Which tasks suit Argus

Argus is built for work that can be checked. The four-role loop only pays for
itself when there is evidence for the Reviewer to read and refuse.

It fits well when:

- **The result is measurable on the machine it runs on.** Every task in the
  three example projects was of this kind: a TFLOPS sweep, a numerical
  solution compared point-by-point with an analytic one, a script whose
  output table can be rerun. The Reviewer can open the raw files.
- **The work has stages and takes hours.** The roofline campaign moved from
  idea to experiment to a paper draft, launched background GPU jobs and
  waited for them, and had a draft sent back for rework, all without an
  operator message. That is what the daemon, the stage checklists and the
  independent review exist for.
- **There is a vertical for the field.** Seven ship with Argus (`research`,
  `software`, `math`, ...) and the store carries seventeen more. A vertical is
  what turns "done" into a checklist the Reviewer can apply; without one the
  Manager still routes the task, but the acceptance standard is generic. See
  [building a vertical](building-a-vertical.md).
- **You want it to keep going.** A standing objective with `--continuous` and
  no `--bounded` keeps proposing follow-up work in the same direction.

It fits poorly when:

- **You want an answer, not work.** `argus --ask` and `/ask` answer inline for
  a fraction of a cent and queue nothing. Sending a question through the task
  path costs a Manager turn plus, if it is misread as work, a Planner and
  Engineer round.
- **The task is smaller than the loop.** The oscillator task, a script most
  people would write in ten minutes, took 12 minutes and $0.91 because it went
  through Manager, Planner, an Engineer round, a Reviewer `continue`, a second
  round and a `done`. That overhead is worth it when you would otherwise not
  check the work; it is not worth it for a throwaway.
- **Finishing requires something only you can do.** Logging in somewhere,
  paying, publishing, deleting shared state, or approving a change to an
  acceptance contract stops the task by design in every autonomy mode. Plan
  the objective to end before that step, or do the step first.
- **"Done" cannot be written down.** If you cannot state what the Reviewer
  should check, it will accept or refuse on its own reading, and rounds will
  continue until a cap.
- **The evidence lives somewhere the roles cannot reach.** The Reviewer is
  read-only and works from the work directory and the paths you allow
  (`ARGUS_SKILL_REVIEWER_READ_DIRS`); a result that only exists in a service it
  cannot query cannot be certified.

A useful test before you start: write the sentence the Reviewer should be
able to say at the end ("the README reports the maximum error against the
analytic solution for all three damping cases, and each number matches the
saved run"). If you can write it, put it in the objective. If you cannot,
Argus will not be able to tell when it is finished either.
