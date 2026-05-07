---
name: Write Autonomous-Agent Try-Out Instructions
description: Produce concrete, copy-pasteable shell instructions that let a human user benchmark argus-skill life chat in fully autonomous mode (no human-in-the-loop) and audit the result objectively.
category: agent-eval-protocol
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-07T07:21:00.000000+00:00
task_history:
  - "user asked: '给完整的试用指令，我要测试，另外你写个skill，教一下你自己怎么写试用指令，我想要的是测评agent在没有人类托管的情况下，独立完成任务的性能'"
---

## Title
Write Autonomous-Agent Try-Out Instructions

## Description
Produce concrete, copy-pasteable shell instructions that let a human user
benchmark `argus-skill life chat` in **fully autonomous mode** (no
human-in-the-loop), with explicit success criteria and an audit trail the
human can inspect after the run.

## Category
agent-eval-protocol

## When to use
- The user wants to "试用" / "评测" / "test" argus-skill themselves.
- They explicitly want to measure **autonomous** performance — i.e. the
  agent must take a task and finish it (or fail it) **without a human
  babysitting between rounds**, mid-run injects, or hand-holding.
- They want concrete commands they can copy-paste, not high-level prose.
- They want an **audit hook** to verify "the agent really did this on its
  own" (journal entries, exit codes, file diffs, test results).

## When NOT to use
- The user just wants documentation of features → use the README, not an
  eval protocol.
- The user wants a one-shot `argus-skill run "..."` task → that's not
  the autonomous-life-loop scope; this skill is specifically for the
  `life chat` self-iterating mode.
- The user wants performance numbers on a fixed benchmark (SWE-Bench,
  Terminal-Bench) → those have their own runners under `benchmarks/`.

## Procedure (template)

Every autonomous try-out instruction set MUST contain these six blocks,
in order. Skipping any is a defect.

### 1. Pre-flight isolation
Before anything else, isolate the test from the user's normal state:
```bash
export ARGUS_SKILL_LIFE_DIR=/tmp/argus-life-eval-$(date +%s)
export ARGUS_SKILL_SKILLS_DIR=/tmp/argus-skill-eval-skills
export OPENAI_API_KEY=...                # user fills in
which codex || (echo "install codex CLI first"; exit 1)
```
- Disposable LIFE_DIR so the test doesn't pollute `~/.argus-skill/life`.
- Fresh skills dir if you want to measure cold-start; **omit it** if you
  want to measure with the prefilled cache (the realistic case).
- Hard-fail early on missing prerequisites — autonomous runs that crash
  five minutes in are wasted budget.

### 2. Concrete autonomous task
Pick a task that is:
- **Verifiable by `pytest` / `make test` / a deterministic shell exit
  code** — autonomous = no judging by you reading prose.
- **Self-contained** in a known working directory (preferably a fresh
  git clone or a scratch dir the agent can mutate).
- **Multi-step but bounded** (5–15 min wall time, ≤ \$1 spend) so a
  cap-busting run terminates within a reasonable test session.

Document the task in one sentence; that becomes the free-text input.

Example shape: *"Implement function X in module Y so that
`pytest path/to/test_y.py -q` exits 0."*

### 3. Hard caps
Always set both:
- `--per-mission-cap-usd <small>` — kills a runaway mission
- `--daily-cap-usd <small>` — kills a runaway loop
With memory backend you can skip caps; with codex backend never skip
them. Show the user the math: "with X tokens at Y \$/Mtok this caps at Z".

### 4. The autonomous incantation itself
The minimal autonomous flow uses `life run --once` (NOT chat) so the
process is fully non-interactive:
```bash
echo '<one-sentence task>' | argus-skill life chat <<EOF
<paste the task as free text — runs immediately on codex by default>
/quit
EOF
```
OR the cleaner non-REPL form:
```bash
argus-skill life backlog add 'one-sentence-title' --objective '<full task>'
argus-skill life run --once \
  --backend codex \
  --per-mission-cap-usd 1.00 \
  --daily-cap-usd 5.00
```
Always pick the form that requires **zero human input after launch**.
The command should be runnable under `nohup` / `tmux` and survive an
SSH disconnect.

### 5. Success criteria (objective, scriptable)
Always provide **at least two** independent checks the human can run
after the agent finishes:
```bash
# 1. The agent declared success
argus-skill life journal | tail -5 | grep -q 'status=success'

# 2. The world actually changed correctly
cd <workdir>; pytest <test_path> -q
echo "exit=$?"
```
A run that passes (1) but fails (2) is a **fake success** and exposes
either reward hacking or verifier gaps. Always include this cross-check.

### 6. Audit trail commands
End every protocol with the exact commands to inspect what happened:
```bash
argus-skill life status
argus-skill life journal -n 20
argus-skill life backlog list --all
ls -la "$ARGUS_SKILL_SKILLS_DIR"             # which skills were learned
git -C <workdir> log --oneline -5             # what the agent committed
git -C <workdir> diff HEAD~1                  # the actual code change
cat "$ARGUS_SKILL_LIFE_DIR/journal.jsonl" | jq '.cost_usd' | paste -sd+ | bc
```
The user MUST be able to answer "what did the agent do, in detail,
without me being there" purely from these post-hoc commands.

## Common mistakes to avoid
- Telling the user to use `chat` and type at the prompt → that's
  human-in-the-loop, defeats the point. Use a heredoc or `life run`.
- Defaulting to `--backend memory` in autonomous tests → memory is a
  stub, it doesn't actually do work; the eval is meaningless.
- Forgetting cost caps → a buggy loop can spend \$100 overnight.
- "Verify by reading the journal" → the journal is the agent's own
  self-report; verify with an external command (pytest, file diff).
- Vague tasks like "improve the codebase" → no objective exit code,
  agent will declare success on anything.
- Running in `~/` or the repo root → the agent might mutate user state.
  Always isolate to `/tmp/...` or a scratch git clone.
- Skipping the dry-run with `--backend memory` → at least one smoke
  pass with the stub catches plumbing breakage before you spend tokens.

## Output format the user expects
A single fenced block per phase:
1. **Setup** (env vars, prereqs)
2. **Smoke test** (memory backend, ≤ 30 s, must succeed)
3. **Real run** (codex backend, the autonomous task)
4. **Verify** (independent objective checks)
5. **Audit** (post-hoc inspection commands)

Each block must be copy-pasteable verbatim. No "[REPLACE THIS]"
placeholders unless absolutely required, and when required, label them
loudly.

## Done definition
A try-out instruction set is "done" when:
- A user with the repo cloned and `OPENAI_API_KEY` set can run the
  whole protocol top-to-bottom **without making any decisions**
  in-flight.
- The verify step exits non-zero **iff** the agent actually failed the
  task (no silent passes from the agent's self-report alone).
- Total spend is bounded by an explicit cap the user set up-front.
