# Argus

Binary-only Argus beta for Linux x64 and native Windows x64 terminals.

```bash
npm install -g @argusevolve/argus@beta
argus --setup
argus
```

`argus --setup` creates the required trusted baseline house-rules directive
when none exists and preserves any operator-authored directives.

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
