# Argus

Binary-only Argus beta for Linux x64 and native Windows x64 terminals.
The Copilot path requires Node.js 22 or newer and an active Copilot subscription.

```bash
npm install -g @github/copilot @argusevolve/argus@beta
copilot login
argus --setup
argus
```

`argus --setup` creates the required trusted baseline house-rules directive
when none exists, preserves any operator-authored directives, and defaults to
Copilot when it is the only supported agent CLI on `PATH`.

`argus` opens the terminal cockpit. `argus --web` starts and opens the Web UI.
The compatibility command `argus-skill` remains available for administrative
operations.

The npm tarball contains a small JavaScript launcher and a platform executable;
it does not contain the Argus Python source tree, tests, private documentation,
or Git history. The executable is not a security boundary and may still be
reverse engineered.

This beta does not bundle optional local quant stacks such as Qlib, LightGBM,
PyTorch, or private market-data integrations. Those workloads require a
separately managed project environment.

Windows support targets Windows Terminal, PowerShell, and classic console
sessions. This beta does not install a Windows Service or promise that work
survives sign-out, reboot, or closing every owning terminal process.
