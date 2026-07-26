/**
 * Whether the operator asked to see the model's reasoning scratchpad.
 *
 * Opt-in, matching `ARGUS_SKILL_SHOW_REASONING`'s documented default of `"0"`
 * and every Python surface (`cli/render.py`, `apps/cli/_follow.py`, and the web
 * event stream).
 *
 * This lived inline in `App.tsx` as a deny-list — anything that was not
 * `0/false/no/off` counted as "show". An *unset* variable is the empty string,
 * which is not in that list, so the normal case fell through to `true` and the
 * TUI displayed the inner monologue by default while every other surface hid
 * it. Extracted here so the rule has one home and a test can hold it.
 */
export function resolveShowReasoning(
  env: Record<string, string | undefined> = process.env,
): boolean {
  const configured = String(env.ARGUS_SKILL_SHOW_REASONING ?? '').trim().toLowerCase();
  return ['1', 'true', 'yes', 'on'].includes(configured);
}
