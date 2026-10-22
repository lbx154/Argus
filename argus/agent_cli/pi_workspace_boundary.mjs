// Argus workspace boundary for Pi (SE-1): every tool call is checked by the
// same hook Claude, Codex and Copilot run (argus/core/workspace_boundary.py).
// An unavailable or failing check refuses the call; it never allows it.
import {spawnSync} from "node:child_process";

export default function(pi) {
  let hook = null;
  try {
    hook = JSON.parse(process.env.ARGUS_PLUGIN_WORKSPACE_BOUNDARY_HOOK || "null");
  } catch {
    hook = null;
  }
  pi.on("tool_call", async (event, ctx) => {
    if (!Array.isArray(hook) || hook.length === 0) {
      return {block: true, reason: "Argus workspace boundary is unavailable for this call."};
    }
    const payload = {tool_name: event.toolName, tool_input: event.input, cwd: ctx?.cwd || process.cwd()};
    const result = spawnSync(hook[0], hook.slice(1), {
      input: JSON.stringify(payload), encoding: "utf8", timeout: 30000, windowsHide: true,
    });
    if (result.status === 0) return undefined;
    const reason = String(result.stderr || "").trim() || "Argus workspace boundary refused this call.";
    return {block: true, reason};
  });
}
