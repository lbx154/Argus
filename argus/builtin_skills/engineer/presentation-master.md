---
name: "Editable Presentations with PPT Master"
description: "创建或编辑可编辑的演示文稿和模板。 Use the installed PPT Master toolkit for editable PPTX delivery, honoring the requested format and existing authorization; research figure policy comes from the active research vertical."
---

# Editable Presentations with PPT Master

Use when the user needs editable PowerPoint slides or templates. Ordinary measured
charts and Markdown diagrams can use simpler tools when that satisfies the request.
For paper-facing figures, first read the active research vertical's figure routing
Skill; this global adapter does not decide a Method D/Method B research policy.

## Locate the supported toolkit

```bash
PPT_MASTER_ROOT="${ARGUS_SKILL_HOME:-$HOME/.argus-skill}/tools/ppt-master"
SKILL_DIR="$PPT_MASTER_ROOT/skills/ppt-master"
"${ARGUS_SKILL_BIN:-argus}" --ppt-master-status
```

Use the revision managed by Argus. A failed status check may indicate a missing
installation, a dependency problem or a modified checkout: report the actual cause.
Use `argus --install-ppt-master` only within existing operator authorization;
do not silently clone, update or replace the shared toolkit to complete a slide.

Read `$SKILL_DIR/SKILL.md`, its `workflows/routing.md`, then only the selected route's
required references. Reuse the installed Generate PPTX, Create Template, Fill Native
PPTX or Enhance Native PPTX workflow instead of inventing a second toolkit.

Run toolkit scripts through the supplied framework interpreter:

```bash
"${ARGUS_SKILL_PYTHON:-python3}" "$SKILL_DIR/scripts/<script>.py" ...
```

Do not call bare `python` or `python3`: toolkit dependency checks are tied to the
configured interpreter. Project dependencies still belong in the project environment.

## Deliver and verify

- Keep projects and outputs in the authorized workdir, outside the installed toolkit.
  Do not run upstream `update_repo.py`; Argus manages installation updates.
- Follow the requested audience, format and available input evidence. Upstream role
  switches are workflow modes inside the current task, not permission to spawn agents.
- Carry forward decisions the operator already delegated. Request only an actually
  missing decision/resource, with a concrete draft where possible.
- Preserve native text, shapes and connectors required for editability; do not flatten
  a complete slide into an image and call it editable.
- Export and inspect the actual rendered slides. Check clipping, fonts, reading order,
  contrast and content against the source. Keep the editable final artifact and the
  necessary source; do not claim success from the script exit code alone.
- Use a configured image backend only when the chosen route and user request need it.
  Follow the existing permitted fallback when it does not; never fabricate image output.

For research figures, the active vertical's evidence and figure rules remain binding.
Report material unsupported features or rendering differences. Stop when the user's
artifact and decisive visual/editability checks are satisfied.
