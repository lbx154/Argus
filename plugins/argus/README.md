# Argus Plugin

Install Node.js 22.12+ first, then install for both hosts on macOS/Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/lbx154/Argus/main/plugins/argus/install.sh | sh -s -- all
```

On Windows, run `install.ps1 all` from PowerShell. It uses the system `py`
installation and does not create a virtual environment. The bundled MCP launcher
uses Node.js to select the platform-appropriate Argus Python.

See [../../docs/plugin.md](../../docs/plugin.md) for Codex-only, Claude-only, and
usage examples. The `medical` vertical (from the `argus-verticals` package) is
for research, not diagnosis or treatment advice.
