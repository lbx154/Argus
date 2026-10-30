# Manager failure after message acceptance

Controlled reproduction of PM9 report item 1 (A03 and A05) and item 10 (B03), recorded on 2026-10-30 through the built CLI at baseline edde90c96, with an isolated ARGUS_HOME and a scripted Codex app-server. The scripted turn returned the missing-Codex error shown in the report. These are the resulting project state, all four event lines and the two HTTP stream frames; the project workdir is replaced with /fixture/work. This is a reproduction, not the unavailable original PM9 project record.

The phase frame precedes the business error. The already accepted message remains in the record, its Manager failure supplies the conversation row text, and the same project supplies the map's paused status and the top bar's interrupted label. No model or operator instance was used.
