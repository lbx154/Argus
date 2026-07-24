# Argus

Binary-only Argus beta for Linux x64 and native Windows x64 terminals.
The Copilot path requires Node.js 22 or newer and an active Copilot subscription.

```bash
npm install -g @github/copilot @argusevolve/argus@beta
copilot login
argus --setup --non-interactive --backend copilot --accept-house-rules
argus
```

`argus --setup` validates and persists an explicit backend/auth mode. It creates
the required trusted baseline house-rules directive when accepted, preserves
operator-authored directives, and does not change global Git identity or
backend-owned authentication files by default.

`argus` opens the terminal cockpit. `argus --daemon-fg` runs a supervised
foreground worker; `argus --daemon` runs persistently in the background;
`argus --doctor` validates readiness.

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
