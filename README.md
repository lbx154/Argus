<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/svg/argus-logo-horizontal-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/brand/svg/argus-logo-horizontal.svg">
  <img src="docs/assets/brand/svg/argus-logo-horizontal.svg" width="420" alt="Argus">
</picture>

### Persistent, reviewed autonomy for research and engineering

Long-running agent work that can plan, execute, verify, pause, and continue beyond a single model turn.

**Preview v0.1.2 · Preview channel for upcoming Argus updates.**

[![GitHub Stars](https://img.shields.io/github/stars/lbx154/Argus?style=flat-square)](https://github.com/lbx154/Argus/stargazers)
[![License](https://img.shields.io/github/license/lbx154/Argus?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![arXiv](https://img.shields.io/badge/arXiv-2608.05144-b31b1b?style=flat-square&logo=arxiv&logoColor=white)](https://arxiv.org/abs/2608.05144)

[Website](https://argusbot.cn) · [Video Demo](https://www.youtube.com/watch?v=i8Qy9HCboQE) · [Technical Report · arXiv:2608.05144](https://arxiv.org/pdf/2608.05144) · [WeChat Community](#wechat-community) · **English** / [简体中文](README.zh-CN.md)

`Manager` → `Planner` → `Engineer` ⇄ `Reviewer`

</div>

---

> [!IMPORTANT]
> **Repository status:** This is the Argus preview repository. The official
> release is maintained at
> **[microsoft/ArgusAgent](https://github.com/microsoft/ArgusAgent)**. Updates
> are synchronized between both repositories; Watch or Star either repository
> to follow the project.

## The Driver–Harness Model

> **In short.** Today's AI agents can *do* things — edit files, run commands, compile
> code, run experiments. What they cannot do is decide what to work on next, judge
> whether the last result was any good, and know when to stop and ask a human. A person
> has to sit there and do that, which is why the work stops when that person goes to bed.
>
> **Argus does that job instead**, splitting it across four roles deliberately not
> allowed to do each other's jobs: a **Manager** that decides when the project moves
> forward and what the system keeps, a **Planner** that picks the next task, an
> **Engineer** that does the work, and a **Reviewer** that is the only one allowed to say
> "this is finished" — and which cannot edit anything, so it cannot quietly fix the
> evidence it is judging.
>
> Because the worker cannot grade its own work, you do not have to watch it. Across 27
> campaigns and 1,548 hours it needed a human research decision about **once every 310
> hours**, spending **95–99%** of its available time working. It also gets better at a
> subject as it goes, without ever retraining the model.

### What this actually looks like

MiniMax-H3 generates video with synchronized sound. Its weights are about **62 GiB**.
You have a MacBook with **24 GB** of memory. You ask Argus to make it run, and you go
to sleep.

1. **Planner** sets the shape of the problem. Shrinking the model is off the table — a
   shrunk model is a different model, so the answer would be worthless. The real goal:
   never have all of it in memory *at once*.
2. **Engineer** takes it apart: 50 blocks of ~**1.29 GB**, which must run strictly in
   order. So it builds a loader that keeps only **two blocks resident**: load two,
   compute, discard, load the next two. Disk holds 62 GiB; memory never does.
3. Along the way it finds something no documentation mentions. The text encoder has 64
   layers, but this model only ever reads the output of **layer 50** — and the last 14
   are not merely wasted work, running them *changes* what the model is conditioned on.
   So it runs exactly 50 and stops.
4. **Reviewer** does not check whether a video came out. It checks whether the *claim*
   survives: still the original full-precision weights, or quietly compressed? Attention
   exact, or approximated? Blocks skipped? Order changed? Any one would make "runs the
   full model" false.
5. It passes: **1344×768, 124 frames, 24 FPS, 5.17 s of stereo audio, in 47 min 58.7 s,
   peaking near 15.8 GB** — about a quarter of the model's own size, on a laptop.
6. **Manager** decides what the system keeps, split by how far it generalizes:
   *stream weights through a fixed-size window* is not specific to this model, so it
   belongs in the **Skill** library; *this model reads `hidden_states[50]`* is true only
   here, so it belongs in the **Wiki**.
7. You wake up to a public repo with the exact commands, pinned checkpoint hashes, and
   the generated video published with its SHA256 — so you can check you got the same
   bytes.

**Step 4 is the whole point.** Compressing the model would have finished this in an
afternoon, and in a headline the two results look identical. That shortcut was
unavailable because the party that did the work is not the party allowed to certify it
— so the repo states plainly what it did *not* do: no sparse attention approximation,
no skipped blocks, no low-bit reconstruction, no reordered computation.

<sub>Source: **[Argus-AiTeam/minimax-h3-mac](https://github.com/Argus-AiTeam/minimax-h3-mac)**
— MacBook Pro (Mac16,8), Apple M4 Pro, 24 GB unified memory.</sub>

---

A language model is an engine; an agent **harness** is the drivetrain that couples its
output to files, shells, compilers, GPUs, and test suites. Most of the last two years
of agent engineering went into building good harnesses, and the results are real.

But a car with no one at the wheel does not go anywhere worth going. Something has to
choose the destination, judge whether the last turn was right, and know when to pull
over and ask. That is the **Driver** — the part nobody automated. In every deployed
agent system we know of a person drives, which also answers how long it can be driven:
nobody drives for eight days without stopping. When the operator goes to dinner the
project stops, because the only component authorized to say *"that is not right, do
this instead"* has gone home.

**Human intelligence is discrete.** It arrives in bursts bounded by attention and
sleep, and a strong coding agent driven turn by turn sustains about **one hour** before
it needs its operator back. The work that matters has the opposite shape: a
**dense-intelligence task** sustains reasoning, tool use, verification, and revision
continuously until a measurable result exists, and will not hold still between bursts.

**Driver intelligence is dense, and it compounds.** The campaign's clock and the
calendar's clock become the same clock, and a premise adopted on day one is still under
revision on day eight by the same accumulated state — rather than re-established each
morning by a person reading yesterday's log.

### Four questions, four owners

A Driver answers four questions, over and over, and they nest — each is asked about the
answer to the one before it. Automating some but not all fails predictably: Q2 alone
gives an agent that executes a plan nobody checked; without Q3 it solves the same
problem the same way forever; without Q4 it spends a credential because no rule said
that decision was not its to make. So each is owned by a **different party**.

| | The question | Owner | May **not** |
|---:|---|---|---|
| **Q1** | **Is this done, and is it any good?** Not whether a command exited zero, but whether it discharges the obligation that motivated it. | **Reviewer** — independently checks correctness, evidence, limitations, completion; may return `blocked` | Edit anything. It runs **read-only** |
| **Q2** | **What is worth doing next?** Given everything now known, including what just failed. | **Planner** — decomposes research state into bounded tasks and defines the evidence each must produce | Move the campaign to the next stage |
| **Q3** | **How does the system get better at this?** And does that lesson hold for this project, this field, or everywhere? | **Manager** — owns stage transitions, and decides whether a lesson stays project-local, enters a vertical's contract, or becomes global | Perform the work it is admitting |
| **Q4** | **Does this need a person?** Which obstacles belong to someone with authority the machine does not have. | **A fixed boundary** — credentials, payment, irreversible actions, and publication always stop for a human, in every autonomy mode | Be re-decided by model judgement |

The **Engineer** owns none of them, and that is the design: it implements, researches,
runs experiments, and proposes what the round taught — but it may not declare its own
work complete.

Each question is answerable only if the one before it was answered honestly: a system
that cannot tell finished from good cannot tell which lesson is worth keeping, and one
that keeps the wrong lessons gets confidently worse. They are hard only where there is
no score — given a test to turn green or a leaderboard to climb, all four collapse into
it. **The Driver's seat is not a separate problem from the missing score. It is what
the missing score leaves behind.**

This inverts the usual reading of verification. Independent review is normally a quality
filter: run the work, then check it. Here it is structural — the reason a person must
watch a conventional agent is that the component doing the work is also the component
declaring it finished. Separate those two, deny the certifier any ability to edit what
it certifies, and let it refuse, and the human can leave the room. **Independent review
is not a quality feature. Separating who does the work from who may certify it is the
load-bearing wall that makes unattended operation possible at all.**

### Evidence-driven, not goal-driven

The seat could not be delegated earlier for a reason specific to research work: **the
objective itself moves.** A mathematical campaign rarely ends in the theorem it set out
to prove; a software request is often underspecified until a candidate implementation
exposes what was missing; in chip design and materials research, the measurement that
would settle the question is frequently the thing under construction.

What a domain expert writes at the outset is not a specification to be obeyed — it is
their best hypothesis, formed with the least information anyone will ever have about the
problem. Prior systems treat departure from it as *goal drift*, a failure mode to
suppress; that assumes the target was right, and when it was not, suppressing the
departure preserves the error. **If the objective is wrong, drifting from it is the
correct behavior.**

The difficulty is that a principled revision and a rationalized failure look identical
in the final artifact. So Argus holds the objective as a revisable hypothesis and admits
revision only when it is backed by evidence, crosses an explicit role boundary, and is
recorded with its justification — a rationalizing system can produce the narrative but
not the refuting measurement. Nor is there a manufactured score: informative failure
counts as progress, and an experiment that never ran can never be recorded as a refuted
idea.

### Self-evolution: Wiki and Skills, with the weights frozen

Argus executes a sequence of **bounded missions** against durable project state. Model
parameters never move, yet later missions begin from a changed search policy rather
than merely a longer transcript. We call this **verification-gated fixed-model runtime
self-evolution**: *gated* because a candidate becomes reusable only with task-native
evidence and an authorized commit; *fixed-model* because the weights never move.

A complete update cycle has four parts, and anything that does not survive all four is
not counted as self-evolution: **(1)** a trajectory produces a candidate; **(2)** the
responsible role checks it against artifacts and task-native evidence; **(3)** the
authorized owner commits, revises, or rejects it; **(4)** a later mission retrieves it.

**Knowledge is two surfaces, and neither substitutes for the other.**

| | **Wiki** | **Skills** |
|---|---|---|
| Records | What a domain *turned out to be like* | Procedures that can be *matched to later tasks* |
| Authored by | Engineer, from reviewed outcomes | Engineer, after its own task completes |
| Committed by | **Reviewer** | **Manager** placement review |
| Persistent form | Source-linked semantic pages | Versioned, layered skill library |

A procedure with no account of why it works cannot be revised when conditions change; a
finding no mission can act on is inert. Neither is automatically correct — entries are
revised, archived, or retired when later results contradict them.

**Skills are scoped to where they were shown to hold.** The Manager placement review —
never the author — puts each admitted skill in one of three layers:

| Layer | Admitted when | Effect |
|---|---|---|
| `project` | It worked here | Stays with this project |
| `vertical` | It proved general to the domain | Written into that field's contract — every future campaign in that domain inherits it |
| `global` | It survived beyond any one domain | Available everywhere |

**Knowledge is not Memory.** Knowledge is *established and reusable*: certified, scoped,
intended to outlive the campaign. **Memory** — journal, backlog, artifacts, per-role
rolling context — is what the runtime needs to *keep working*, and takes no
certification pass because it records what happened rather than what is true. Losing
Memory costs a mission its bearings; admitting bad Knowledge corrupts every mission that
later reuses it.

None of this implies monotonic improvement: some missions commit no reusable state, and
retained state can go stale. A project can stop, resume, survive a runtime replacement,
and continue from its latest verified position.

### Verticals: domain depth without touching the core

A **vertical** is a domain package declaring what counts as evidence in a field — its
stages, tools, evidence requirements, and completion standards. Domain expertise and
decision authority are separated architecturally.

A vertical may **raise** the evidence bar and never lower it, and it cannot reach the
authority boundary at all: across **53,871 lines** of vertical code there are **zero**
references to the autonomy mode, the operator-escalation path, or the approval
boundary, because that logic lives in the core. A vertical cannot grant itself
approval, nor let an Engineer certify its own work where policy requires a Reviewer.

**24 verticals** ship against a **130,362-line core that does not change when a domain
is added.** The smallest costs **108 lines**; the median is 775.

What a specialist writes is a **seed, not a ceiling** — campaigns running under a
vertical promote sharpened checks back into its contract. A vertical may also declare
`PROTECTED_ITEM_IDS`, a floor of gates the promotion path restores against later edits,
so the checks a domain considers irreducible cannot be optimized away by the same
process that adds new ones. Growth is permitted upward from the seed; the floor does
not move.

→ **[Build your own Vertical](#build-your-own-vertical)**

### Does the seat stay empty?

The claim is measurable, so we measure it. Across **27 campaigns**, **1,548 wall-clock
hours**, and **306,691 logged events**, Argus raised **38** requests for a human — one
every **40.7 hours**. What they asked for is more informative than the rate:

| What the interruption asked for | Share |
|---|---:|
| Broken infrastructure — failed GPU driver, corrupted container storage, auth outage | 34% |
| Credentials, budget, or authorization — the boundary behaving as designed | 26% |
| Missing context files | 18% |
| **Research judgment** — work in hand, could not decide how to proceed | **13%** (5 requests in 1,548 hours) |
| Framework defects | 8% |

With work in hand, duty cycle runs **95.1–98.7%**, against a ceiling of 33% for any
harness whose Driver has to sleep. Where it falls short, the interruption logs record
failed drivers and corrupted storage: **the binding constraint on unattended operation
is infrastructure availability, not agent autonomy.**

Full derivations, campaign inventory, and stated limitations are in the
[technical report](https://arxiv.org/pdf/2608.05144).

**Native backends:** `GitHub Copilot CLI` · `Pi` · `OpenAI Codex CLI` · `Claude Code` · `OpenCode` · `Grok Build` · `Qoder` · `DeepSeek Harness`

**Harbor evaluation:** Harbor Framework can invoke the complete bounded Argus
Manager/Planner/Engineer/Reviewer runtime as a custom agent. See
**[Harbor integration](docs/harbor.md)**.

**Coding-agent plugin:** use the packaged MCP bridge and host-specific Skills
without changing the core runtime. See **[Plugin quick start](docs/plugin.md)**.

## WeChat community

Scan the QR code to join the Argus community. Click the image to open it at full
size. If the printed expiry date has passed, open an Issue and ask the
maintainers for the latest code.

<p align="center">
  <a href="docs/assets/argus-wechat-group.jpg">
    <img src="docs/assets/argus-wechat-group.jpg" width="360" alt="Argus WeChat community QR code">
  </a>
</p>

## Quick Install

Choose the section for your operating system. Do not mix commands between
platforms. All platforms need Node.js **22.12+** from
[nodejs.org](https://nodejs.org/en/download) and one authenticated Agent CLI.
Reuse the CLI you already work in; Argus does not require a separate account.
Docker is not required for a normal Argus installation; it is only an optional
prerequisite for the separate Harbor evaluation integration.

> [!TIP]
> **Recommended: let the Code Agent you already use install and verify Argus.**
> Copy the prompt in the Agent-assisted section below. The manual commands remain
> available for users who prefer to install each step themselves.

| Agent CLI | Backend | Install | Authenticate |
|---|---|---|---|
| GitHub Copilot CLI | `copilot` | `npm install -g @github/copilot` | `copilot login` |
| OpenAI Codex CLI | `codex` | `npm install -g @openai/codex@latest` | `codex login` |
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` | Run `claude`, then `/login` |
| Pi | `pi` | `npm install -g --ignore-scripts @earendil-works/pi-coding-agent` | Run `pi`, then `/login` |
| OpenCode | `opencode` | [Official install](https://opencode.ai/docs/) | `opencode auth login` |
| Grok Build | `grok` | [Official install](https://x.ai/cli) | `grok login` |
| Qoder CLI | `qoder` | `npm install -g @qoder-ai/qodercli` | `qodercli login` |
| DeepSeek Harness | `dsh` | `npm install -g @deepseek-ai/dsh` | Configure `DEEPSEEK_API_KEY` or the dsh Models page |

The public preview is installed directly from the current GitHub archive until
the first PyPI release is published.

### Recommended: Agent-assisted installation

Send this prompt to an already installed Code Agent:

```text
Read https://github.com/lbx154/Argus/blob/main/docs/agent-install.md and install
Argus using the section for this operating system. Prefer the Agent CLI running
this conversation as the Argus backend. Do not create a venv on Windows or
macOS; keep the documented venv on Linux. Run setup through its real Agent-turn
smoke test, then run `argus doctor --deep --advisor auto`. Before account login,
sudo, or global configuration changes, explain why and wait for approval. Never
ask me to paste a password, token, or API key into the conversation.
```

The agent follows the **[installation execution contract](docs/agent-install.md)**.

### Windows 10/11 — direct pip, no virtual environment

Install Python 3.11+ from [python.org](https://www.python.org/downloads/windows/)
and select **Add Python to PATH** in the installer. Then open a new PowerShell:

```powershell
py --version
node --version
py -m pip install --upgrade pip
py -m pip install --upgrade --force-reinstall "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
$Scripts = py -c "import sysconfig; print(sysconfig.get_path('scripts'))"
$Argus = Join-Path $Scripts "argus.exe"
if (-not (Test-Path $Argus)) { throw "Argus entry point not found at $Argus" }
$env:Path = "$Scripts;$env:Path"
& $Argus --version
& $Argus --setup
& $Argus doctor --deep --advisor auto
& $Argus --status
& $Argus
```

Calling `$Argus` proves setup is not accidentally using another stale
installation. `$env:Path` also makes plain `argus` available in the current
PowerShell. The troubleshooting section covers persistent PATH repair.

`argus doctor` is an active repair command. By default it launches an installed
Agent CLI in the real Argus directories with tools enabled, lets the Agent
inspect and fix the machine, then reruns deterministic checks. Use
`argus doctor --advisor none --verify` for a no-model verification.
The active repair may take several minutes because it performs a real Agent
turn; it is not a quick version check.

Windows currently supports installation, Manager chat, pairing, Web/TUI,
terminal-scoped daemon control, and native durable subagents. On native Windows,
a detached worker owns direct or supervised long commands, persists registry and
log state, and uses bounded process-tree cleanup; WSL2 remains optional rather
than required for this path. The Windows Desktop installer is documented separately in
**[Windows Desktop](docs/windows-desktop.md)**.

### macOS — managed command install, no manual virtual environment

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed,
then:

```bash
uv --version
node --version
uv tool install --force --python 3.12 \
  "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
ARGUS_BIN="$(uv tool dir --bin)/argus"
test -x "$ARGUS_BIN"
"$ARGUS_BIN" --version
uv tool update-shell
"$ARGUS_BIN" --setup
"$ARGUS_BIN" doctor --deep --advisor auto
"$ARGUS_BIN" --status
"$ARGUS_BIN"
```

`ARGUS_BIN` works immediately even when uv's tool directory was not previously
on PATH. `uv tool update-shell` makes plain `argus` available in a new terminal.
`uv tool` already owns the isolated environment; do not create another venv.

### Linux — isolated source venv

Linux servers keep an explicit venv so Python, CUDA tooling, and long-running
process ownership remain reproducible. Install Python 3.11+, Git, Node.js
22.12+, and your distribution's `python3-venv` package first:

```bash
git clone https://github.com/lbx154/Argus.git "$HOME/Argus"
cd "$HOME/Argus"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
ARGUS_BIN="$HOME/Argus/.venv/bin/argus"
"$ARGUS_BIN" --version
"$ARGUS_BIN" --setup
"$ARGUS_BIN" doctor --deep --advisor auto
"$ARGUS_BIN" --status
"$ARGUS_BIN"
```

Private-preview collaborators use
`https://github.com/lbx154/argus-skill.git` in the Linux clone command. On
Windows/macOS, install a private wheel or authenticated private archive rather
than putting a GitHub token in shell history.

Do not rely on a globally installed `argus` on Linux. In a new shell, use
`$HOME/Argus/.venv/bin/argus` (or activate that venv explicitly). If venv
creation reports that `ensurepip` is unavailable, install the distribution's
`python3-venv` package and rerun the command.

### Backend notes

Use `copilot`, `pi`, `codex`, `claude`, `opencode`, `grok`, `qoder`, or `dsh`
for `--backend`. Setup adopts a model from the selected CLI's own catalog when
one is available; otherwise it keeps that CLI's native default. It does not
inject an OpenAI model id into Claude Code, Pi, OpenCode, Grok, Qoder, or dsh.
If you have an OpenAI-compatible endpoint, setup installs Pi when needed and
configures it directly:

```bash
ARGUS_SETUP_API_KEY=... argus --setup --non-interactive \
  --api-url https://api.example.com/v1 \
  --api-model model-id
```

For Grok Build, install and authenticate the official xAI CLI first:

```bash
curl -fsSL https://x.ai/cli/install.sh | bash
grok login
argus --setup --non-interactive --backend grok
```

`XAI_API_KEY` is also supported for headless environments. Argus uses Grok's
native headless JSON stream, resumes sessions by ID, and keeps role prompts out
of process arguments.
In PowerShell, use a backtick instead of `\` for line continuation.

#### Choosing a provider on the multi-provider CLIs

Pi and OpenCode are provider-agnostic fronts: which account they bill depends on
what you authenticated them against (a native DeepSeek key, Anthropic, Azure, a
local vLLM, a Copilot proxy). Argus passes your configured model id straight
through, so a bare id like `deepseek-chat` is resolved by the CLI itself.

Name the provider when a bare id is ambiguous or when the CLI requires it:

```bash
# Pi — only needed when two authenticated catalogs carry the same model id
export ARGUS_SKILL_PI_PROVIDER=deepseek

# OpenCode — required: `opencode run --model` only accepts provider/id
export ARGUS_SKILL_OPENCODE_PROVIDER=deepseek
```

Both are also settable from the cockpit `/config` view, and persist across
restarts once set there.

`argus --doctor` reads the CLI's authenticated catalog and tells you when the
configured provider is not one you hold a key for, or when a model id you
selected is not on offer.

Use `argus --config-help` to inspect each role's effective model and where it
came from. Catalog commands are backend-specific, for example
`pi --list-models`, `opencode auth list`, and `qodercli --list-models`.

Full details, including the breaking change for Pi deployments that relied on
the old implicit `github-copilot` prefix: **[backend providers](docs/backend-providers.md)**.

### Launch

Windows and macOS can use `argus` after PATH setup. On Linux, replace `argus`
below with `$HOME/Argus/.venv/bin/argus` unless the venv is active.

```bash
argus
```

```bash
argus doctor                         # Agent-driven inspection and repair
argus doctor --advisor none --verify # deterministic verification, no model call
argus --status                       # inspect the current runtime
```

## Interfaces

### Windows Desktop

The Windows x64 source tree includes an Electron host that supervises a frozen
copy of the same Argus runtime and opens the existing Web cockpit—there is no
separate Desktop fork of Manager, Workbench, or the WebAPI. Source setup,
security boundaries, verification, and packaging commands are documented in
**[Windows Desktop](docs/windows-desktop.md)**.

### Terminal cockpit

```bash
argus
```

Use the terminal cockpit to talk to the Manager, follow live work, inspect state, and resume projects.
Without an explicit `--port`, Argus reuses a compatible backend or selects the
first available port starting at `8799` when another program or stale backend
occupies it. On Windows, a plain `argus` launch also opens the Web UI; use
`argus --no-open` for the terminal cockpit only.

### Web UI

Start Argus and open the Web UI in your default browser:

```bash
argus --web
```

Preferred address: [http://127.0.0.1:8799](http://127.0.0.1:8799); Argus advances
to the next available port when needed.

The Web UI follows the browser language on first launch and supports English
and Simplified Chinese. Use the language button in the session sidebar to
switch; the selection is saved in the browser.

```bash
argus --web --web-port 8800  # use another port
```

#### Remote server over SSH

On the server:

```bash
argus --web
```

On your computer:

```bash
ssh -L 8799:127.0.0.1:8799 user@server
```

Then open [http://127.0.0.1:8799](http://127.0.0.1:8799) locally.

<details>
<summary><strong>Direct LAN access</strong></summary>

A non-loopback bind is always protected by a bearer token. If
`ARGUS_SKILL_WEB_TOKEN` is set it is used; otherwise one is minted for that run:

```bash
argus --web --web-host 0.0.0.0 --web-port 8799
```

This prints the address other devices can reach, the token, and a QR code.
Set the token yourself to keep one across restarts:

```bash
export ARGUS_SKILL_WEB_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

To serve without a token — only behind your own authenticating proxy — set
`ARGUS_SKILL_WEB_ALLOW_INSECURE=1`.

</details>

### From a phone

Telegram, Feishu/Lark, and the web UI all work from a phone. The two chat bots
dial out, so a daemon behind NAT needs no tunnel and no public URL:

```bash
# Feishu / Lark — WebSocket long connection, no request URL to configure
pip install 'argus-skill[feishu]'
export ARGUS_SKILL_ENABLE_FEISHU=1
export ARGUS_SKILL_FEISHU_APP_ID=cli_xxx ARGUS_SKILL_FEISHU_APP_SECRET=xxx

# Telegram
export ARGUS_SKILL_ENABLE_TELEGRAM=1
export ARGUS_SKILL_TELEGRAM_BOT_TOKEN=... ARGUS_SKILL_TELEGRAM_CHAT_ID=...
```

Both bots serve the same commands (`/add`, `/status`, `/nudge`, `/backlog`, …).
The web UI is installable to the home screen and pairs by scanning the QR code
printed by `argus --web --web-host 0.0.0.0`.

See **[docs/mobile.md](docs/mobile.md)** for the full setup.

## Advanced usage

Argus is designed to be changed, not merely configured.

### Autonomy level

The default `pragmatic` mode handles recoverable engineering choices—timeouts, failed tests, benchmark sizing, and technical routes—without interrupting you. It asks only for credentials, more spending, irreversible/outward-facing actions, or changes to an operator-owned acceptance boundary.

```bash
export ARGUS_SKILL_AUTONOMY_MODE=cautious    # ask on every explicit question
export ARGUS_SKILL_AUTONOMY_MODE=pragmatic   # default: recover technical issues
export ARGUS_SKILL_AUTONOMY_MODE=autonomous  # maximize reversible execution
```

The Web configuration view and `/config` expose the same setting.

### Adapt the runtime

If you are an agent enthusiast, deploy Argus locally and make the complete loop fit the way you work. Tune role prompts, workflow boundaries, review policy, tools, and operating conventions; connect your own infrastructure; preserve the behavior you care about with tests.

One worked engineering case is **[exploration without local hill climbing](docs/exploration-without-local-hill-climbing.md)**: how report-only research, high-risk mechanism portfolios, single-run screening, and strict final claims were separated after an MI300X serving campaign exposed overly conservative incentives.

### Build your own Vertical

A Vertical gives your field its own stages, Skills, datasets, tools, evidence expectations, evaluation methods, and completion criteria. Planning and review can then follow the real standards of your domain instead of a generic process.

The `math` vertical is the worked example: three stages, a content-addressed
evidence store, Lean-backed mechanical verification, and an explicit rule for
which kind of check is allowed to settle which kind of question. See
**[mathematical research](docs/research-mathematics.md)**.

### Use another agent as the outer layer

GitHub Copilot, Pi, Codex, Claude Code, OpenCode, Grok Build, OpenClaw, or Hermes can be the environment from which you invoke Argus, inspect its state, operate its local CLI or Web/API surface, and continue improving the deployment.

- **Native Argus backends:** GitHub Copilot CLI, Pi, Codex CLI, Claude Code, OpenCode, Grok Build, Qoder, DeepSeek Harness
- **External agent operators:** OpenClaw, Hermes, or any agent that can use a shell or HTTP API

For durable missions, install or adapt the portable
[`argus-runtime-orchestration` Agent Skill](integrations/agent-skills/argus-runtime-orchestration/SKILL.md).
It defines the two-party operator model, the active `Needs you` intervention loop,
host-specific adapters, evidence boundaries, and closeout checks.

Useful entry points:

```bash
argus doctor
argus --status
argus --web
```

The most capable setup is often an Argus instance deliberately adapted to your own ambitious field and way of working.

## Update

Windows:

```powershell
py -m pip install --upgrade --force-reinstall "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
$Argus = Join-Path (py -c "import sysconfig; print(sysconfig.get_path('scripts'))") "argus.exe"
& $Argus --version
& $Argus doctor --advisor none --verify
```

macOS:

```bash
uv tool install --force --python 3.12 \
  "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
"$(uv tool dir --bin)/argus" --version
"$(uv tool dir --bin)/argus" doctor --advisor none --verify
```

Linux source checkout:

```bash
"$HOME/Argus/.venv/bin/argus" update
"$HOME/Argus/.venv/bin/argus" --version
"$HOME/Argus/.venv/bin/argus" doctor --advisor none --verify
```

The Linux source command refuses dirty or detached checkouts, fast-forwards the
configured upstream, and refreshes the editable installation when the revision
changes. Argus detects stale local WebAPI and daemon processes and replaces them
at a controlled task boundary. Update verification is deterministic and does
not spend a model call.

## Uninstall

```powershell
# Windows
py -m pip uninstall argus-skill
```

```bash
# macOS
uv tool uninstall argus-skill
```

On Linux, stop Argus, preserve any work you need, then remove the
`$HOME/Argus` checkout and its `.venv`. Package removal intentionally leaves
runtime state under `$HOME/.argus-skill` untouched on every platform; delete
that directory only when you also want to remove projects, configuration, and
logs.

## Installation troubleshooting

- Confirm which executable the shell is using: `Get-Command argus -All` on
  PowerShell, or `type -a argus` on macOS/Linux. Its `argus --version` release
  id should change after an update.
- On macOS, use `"$(uv tool dir --bin)/argus"` immediately. Run
  `uv tool update-shell` once and open a new terminal for plain `argus`.
- On Windows, recover the exact Scripts directory with
  `$Scripts = py -c "import sysconfig; print(sysconfig.get_path('scripts'))"`.
  Add it to the current window with `$env:Path = "$Scripts;$env:Path"`. For new
  windows, use the Python installer’s **Modify** action and enable
  **Add Python to PATH** rather than creating a venv.
- On Linux, use `$HOME/Argus/.venv/bin/argus`; a global `argus` may be an older
  installation. Install `python3-venv` if `python3 -m venv` lacks `ensurepip`.
- Use `argus doctor --advisor none --verify` for deterministic diagnostics.
  Use `argus doctor` when you want an installed Agent to inspect and repair
  Argus directly.
- Use `argus --config-help` to check the effective backend/model before blaming
  setup or authentication.

## What Argus has done so far

A partial record — the campaigns we have finished measuring. Everything below is
grouped by **who decides whether the result counts**, and none of those deciders is
Argus.

### Open artifacts you can inspect

| Artifact | What it is |
|---|---|
| **[ACE-2](https://github.com/Argus-AiTeam/ace-2)** | A Qwen2.5-0.5B W4A8 inference accelerator whose spec, RTL, verification environment, and physical-flow evidence have no human author of record. 18/18 Layer-0 operators exact; **13,914/13,914** runtime commands over 1,240,410,384 simulator cycles; SKY130 mapped synthesis at **0.614 mm²** (operator cap 2.0), **+0.6966 ns** setup slack, WNS/TNS **0.00 ns**, 100 MHz. The certificate enumerates its own exclusions: no routed timing, no power signoff, no DRC/LVS, no GDS or tapeout, no silicon. |
| **[minimax-h3-mac](https://github.com/Argus-AiTeam/minimax-h3-mac)** | MiniMax-H3's BF16 diffusion transformer is ~62 GiB. Not shrunk — run. On an **M4 Pro with 24 GB** unified memory via MLX block-wise streaming: 1344×768, 124 frames, 24 FPS, 5.17 s stereo audio, **47 min 58.7 s** end to end, **~15.8 GB** peak. |
| **[minimax-h3-desktop](https://github.com/Argus-AiTeam/minimax-h3-desktop)** | Full FL2VA fidelity on **one RTX A6000**. BF16 warm baseline 1,792.202 s (N=10); Turbo 8-step **290.998 s, 6.159×**, adopted as the practical default; Sol-Attn r=8 at +15.203% over 10/10 pairs; 30-second final-AV formal N=10 at **+4.326%**. Candidates that failed the quality gate are published as *rejected*, not folded into the headline. |
| **[ComfyUI-MiniMax-H3-MLX](https://github.com/Argus-AiTeam/ComfyUI-MiniMax-H3-MLX)** | MiniMax-H3 video and stereo-audio nodes for ComfyUI on Apple Silicon. |
| **[FlashDA](https://github.com/SJTU-DENG-Lab/FlashDA/tree/feature/dllm-fa4-adaptation)** · **[Diffulex](https://github.com/SJTU-DENG-Lab/Diffulex)** | Diffusion LMs do not use causal attention. Argus was given one campaign — **21.85 hours** of model compute — to carry six mask families (block-causal, prefix-full, prefix-causal, prefix-hole, sliding-window, cache-only, plus compositions) into a current-generation **FlashAttention-4 CuTe DSL** kernel, with **Diffulex** as the executable iteration environment. **19/19** parity cases pass across SM80 and SM90, paged and dense, and CUDA Graph replay is bit-identical to direct invocation. On **H200/SM90** it reaches **92–95% of native FA4** and runs **1.61–2.57× faster than the Diffulex Triton backend** — both sides CUDA-Graph captured, like for like. Total model spend: under **80 CNY**, with 2 human interruptions across an 87.7-hour span. |

FlashDA is built on the excellent FlashAttention-4 / CuTe DSL work by
[Tri Dao](https://github.com/tridao) and collaborators. Feedback, reproductions,
and ports beyond SM90 are very welcome — the dLLM adaptation lives on the
[`feature/dllm-fa4-adaptation`](https://github.com/SJTU-DENG-Lab/FlashDA/tree/feature/dllm-fa4-adaptation)
branch, and its full measurement protocol, rejected routes, and per-scenario
latencies are in
[`EXPERIMENT_RESULTS.md`](https://github.com/SJTU-DENG-Lab/FlashDA/blob/feature/dllm-fa4-adaptation/EXPERIMENT_RESULTS.md).

The instructive part is not the endpoint. The campaign's early dense-plus-block-sparsity
route was not slightly worse but **4.9–29.6× slower** than native. The result came from
recognising that the data path was wrong and abandoning it — and that rejected route is
retained in the skill library with its evidence, rather than discarded.

### Judged by maintainers who owe us nothing

| Submission | Outcome |
|---|---|
| **[`sgl-project/sglang#35038`](https://github.com/sgl-project/sglang/pull/35038)** — native SenseNova U1 multimodal generation and interleave serving | 36 files, **+11,263/−72**, 14 commits, across five workstreams usually staffed separately. 1,116 tensors load with zero missing or unknown; VQA exact on **160/160** generated tokens; concurrency-8 exact **8/8**; **5.108×** throughput at batch size 8. A cross-batch determinism defect was found, localised, and fixed. One engineer with a turn-by-turn coding agent invested **60+ hours** without completing it; a blind agent run stopped at 1 h 21 min with a draft carrying no real weights; Argus completed it inside a **24.14-hour** envelope. *Open, under review.* |
| **[`fla-org#1045`](https://github.com/fla-org/flash-linear-attention/pull/1045)** — TileLang RWKV6 forward-intra backend | **Merged.** 1.18× forward, 1.21× fwd+bwd on H100 NVL. No inline change requests. Its description states plainly that the implementation, optimisation loop, validation, and performance evidence were completed autonomously — and an outside maintainer accepted that sentence along with the code. |
| **[`fla-org#1109`](https://github.com/fla-org/flash-linear-attention/pull/1109)** — SM100 backward-autotuning illegal memory access | **Merged.** Two lines, no speedup to report: before the fix the test file could not run to completion; after it, **76 tests pass**. The maintainer independently re-derived that 24 autotune candidates survive the filter and approved with no required changes. |
| **[`fla-org#1128`](https://github.com/fla-org/flash-linear-attention/pull/1128)** — four TileLang kernels for KDA training | 1.29× over Triton across four stages on B200. The best single stage reached 1.541×, but dispatch was enabled only for the one workload with both verified correctness and a repeatable end-to-end gain, measuring 1.078–1.099×. The larger number was available and was not used. *Open.* |
| **[`fla-org#1114`](https://github.com/fla-org/flash-linear-attention/pull/1114)** — parallelized long-sequence `AttnRes` reduction | 1.102× geomean across five bf16 shapes on B200, best case 1.237×. The submission reports its **worst** row, 1.033×, alongside the mean. *Open.* |

### Scored by official harnesses

| Arena | Result |
|---|---|
| SWE-Bench Pro (731 tasks) | **≈78%** vs **59%** for direct Copilot — same model on both sides (GPT-5.5/xhigh through Copilot) — and **35** tasks declared `blocked` rather than reported as unsupported successes |
| SOL-ExecBench | Rank **#6 globally**; 7 kernels placed top-3; beat the #1 entrant on 2 |
| MLE-Bench Lite | **69.2%** medal rate (9/13 graded): 3 gold, 3 silver, 3 bronze, against Kaggle leaderboards |
| AARRI-Bench | **63/82 (76.8%)** vs 68.3% paper best |
| nanochat (B200 / H100) | 0.9636 / 0.9855 BPB vs 0.9646 / 0.9879 human best |
| nanoGPT speedrun | **79.77 s** vs 80.18 s same-device human record |
| Math-reasoning data synthesis | 28.0 gap vs 20.83 / 8.33 / 6.25 baselines, under a frozen solver |
| FlashAttention-4 for diffusion LLMs | See **FlashDA** above — 19/19 bit-exact parity cases decided by an fp32 reference, not by a score |

### Decided by external checkers and reviewers

- **MOF generation** — chemical control at 92.5 / 100.0 / 74.5%, AUC 0.594 → 0.833, verified by the external `MOFChecker`. The admitted method is *smaller* than the published method it replaces, which is not an outcome a score-optimizing system tends to reach.
- **Erdős–Gyárfás** — six proof-backed frontier updates against a proof checker, with one falsified route retained as evidence rather than deleted.
- **Research writing** — six paper pipelines carried to submission across 254 missions, including 16 Stage rollbacks under review; 41 de-duplicated public artifacts across six programs.

### The runtime on itself

- **Parameter-invariant self-evolution:** at maturity, **21% fewer tokens** and **15% less active time** per solved SWE-Bench Pro task than at startup — with model weights unchanged throughout.
- **Parameter-changing axis:** a from-scratch 1B pretraining stack built and run end to end on 8×B200.
- **Endurance:** longest single campaign **8.1 days**; 4 campaigns over a week; largest single trace 61,797 events.

> [!NOTE]
> Every number above is reproduced from the technical report or from the linked
> public repository, and each carries the scope conditions stated there. Where a
> result is narrow — one shape, one GPU generation, one demonstrated scope — the
> source says so, and so do we.
