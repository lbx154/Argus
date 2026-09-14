#!/bin/sh
set -eu

target="${1:-all}"
repo="lbx154/Argus"
source_url="${ARGUS_INSTALL_SOURCE:-git+https://github.com/lbx154/Argus.git@main}"
argus_home="${ARGUS_HOME:-$HOME/.local/share/argus}"
venv="$argus_home/venv"
python="$venv/bin/python"

case "$target" in
  codex|claude|all) ;;
  *) echo "Usage: install.sh [codex|claude|all]" >&2; exit 2 ;;
esac

command -v node >/dev/null 2>&1 || {
  echo "Node.js 22.12+ is required by the cross-platform MCP launcher." >&2
  exit 1
}
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 12) ? 0 : 1)' || {
  echo "Node.js 22.12+ is required by the cross-platform MCP launcher." >&2
  exit 1
}

mkdir -p "$argus_home"
if command -v uv >/dev/null 2>&1; then
  uv venv --python 3.11 "$venv"
  uv pip install --python "$python" --upgrade "$source_url"
else
  command -v python3 >/dev/null 2>&1 || {
    echo "Python 3.11+ or uv is required." >&2
    exit 1
  }
  python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || {
    echo "Python 3.11+ is required." >&2
    exit 1
  }
  [ -x "$python" ] || python3 -m venv "$venv"
  "$python" -m pip install --upgrade "$source_url"
fi

"$python" -c 'from argus.plugin.mcp_server import mcp; assert mcp.name == "argus"'

installed=0
if [ "$target" = "codex" ] || { [ "$target" = "all" ] && command -v codex >/dev/null 2>&1; }; then
  command -v codex >/dev/null 2>&1 || {
    echo "Codex is not installed." >&2
    exit 1
  }
  codex plugin marketplace add "$repo" --ref main
  codex plugin add argus@argus
  installed=1
fi

if [ "$target" = "claude" ] || { [ "$target" = "all" ] && command -v claude >/dev/null 2>&1; }; then
  command -v claude >/dev/null 2>&1 || {
    echo "Claude Code is not installed." >&2
    exit 1
  }
  claude plugin marketplace add "$repo"
  claude plugin install argus@argus
  installed=1
fi

if [ "$installed" -eq 0 ]; then
  echo "Install Codex or Claude Code, then rerun this command." >&2
  exit 1
fi

echo "Argus plugin installed. Start a new Codex session or run /reload-plugins in Claude Code."
