# Vendored: codex_autoloop

This directory is a vendored copy of the **ArgusBot** package
(`codex_autoloop` Python module, ArgusBot version **0.1.0**).

- Upstream source: <https://github.com/waltstephen/ArgusBot>
- License: MIT (see `./LICENSE`)

## Why vendored

`argus_skill.adapters.agent_cli_backend` and `argus_skill.apps._life_repl` need
the codex/claude/copilot CLI dialect handler from ArgusBot. Argus additionally
maintains its OpenCode dialect in this copy. We vendor the
module so end users do not need a separate `pip install 'argus-skill[codex]'`
step — `pip install argus-skill` is sufficient to drive the codex CLI.

## Hash of source `.py` files at vendor time

```
7b0556cab73f878b2256608e2b844d9e8bf7c701eb1c52f4d356f5accf8561d8
```

(Hash is `sha256` over a stream of `<relative_path>` + file bytes for every
`*.py` under this directory, sorted by path. Recompute and compare when
refreshing.)

## How to refresh

```bash
pip install --upgrade ArgusBot@git+https://github.com/waltstephen/ArgusBot.git
SRC=$(python -c "import codex_autoloop, pathlib; print(pathlib.Path(codex_autoloop.__file__).parent)")
DST=argus_skill/agent_cli
rm -rf "$DST"
mkdir -p "$DST"
cp -r "$SRC"/. "$DST"/
find "$DST" -name __pycache__ -type d -exec rm -rf {} +
# then recompute the sha above and update this file
```

## Local edits

Keep local dialect extensions explicit and covered by tests; reapply them when
refreshing the upstream-owned base.
