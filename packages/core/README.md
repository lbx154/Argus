# Argus

A research and engineering team of AI agents — a Manager you talk to, a Planner, Engineers and a
Reviewer — run from a web cockpit on your machine. Give it work in a sentence; follow it on the map;
open what it hands back.

```sh
npm install -g @lbx154/argus
argus
```

`argus` prints the cockpit's address; open it. The team works through a coding-agent CLI, Codex by
default (`--backend codex|claude|copilot|pi`), which must be installed and signed in. Each project
works in the folder set when you start it. `argus --help` lists the options.

Node 22.12 or later. MIT licensed.
