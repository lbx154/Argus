# Contributing to argus-skill

Thanks for considering a contribution. argus-skill is a small, focused
codebase — the bar for new code is "earns its keep at the call sites
that already exist," not "looks plausible in isolation."

## Setup

```bash
git clone https://github.com/lbx154/argus-skill
cd argus-skill
pip install -e ".[dev]"
```

For the codex backend (real LLM runs):

```bash
pip install -e ".[dev,codex]"
```

## Running the suite

```bash
pytest                       # full suite, ~5 s on a laptop
ruff check argus_skill/ tests/
```

The repo ships with `pytest -q` as the default and `ruff` configured in
`pyproject.toml`. Both must be green before a PR is reviewed.

## Architectural rules (non-negotiable)

These are the load-bearing invariants. Touching them needs explicit
review-level discussion in the PR description.

1. **One entry point.** `argus-skill` (no subcommand) drops into the
   unified REPL in `argus_skill/apps/_life_repl.py`. There is no
   `chat`, `go`, `mission`, `life`, `daemon`, or `up` subcommand and
   we are not bringing them back.
2. **Backlog state machine is sealed.** Items in `done` / `failed` /
   `skipped` cannot transition back to `pending` / `running`. Anything
   that looks like a re-execution must go through the `claim_next` →
   `update` cycle and respect `IllegalStateTransition`. Tests live in
   `tests/life/test_state_machine_guards.py`.
3. **Singleton REPL.** Each life-dir has at most one live REPL, guarded
   by `<state>/repl.pid`. Cross-process safety is the lock's job;
   in-process safety is the state machine's job. Don't add a third
   layer.
4. **`LessonsAwareReviewer` is the only reviewer wrapper that touches
   the lessons log.** New review-time signals go through it, not via
   parallel channels.

## What lands quickly

- Bug fixes with a regression test.
- Cleanups that delete code.
- Doc fixes that match the actual behavior.

## What gets bounced

- New CLI subcommands. The product surface is intentionally narrow.
- Re-introducing dead `core/{supervisor,bus,daemon_client}.py` style
  scaffolding for a "future" that has no in-tree caller.
- Code without tests. Even small helpers — the suite is fast enough
  that there's no excuse.
- "Helpful" debug `print` statements in production paths. Use `log =
  logging.getLogger(__name__)`.

## Commit messages

We prefer the form

```
<area>: <one-line summary in lowercase>

<optional 2–3 line explanation of why, not what>
```

Examples lifted from `git log --oneline`:

```
life: surface engineer.failure_nudge in user-facing event set
reviewer: stop demanding re-runs when evidence is already shown
life chat UI: split model speech vs operations + paste fix
```

## Reporting issues

Before opening an issue, run with `/verbose` enabled (it's the default
in the REPL) and include the lifecycle event trail. The events are
deterministic and make root-cause near-mechanical.
