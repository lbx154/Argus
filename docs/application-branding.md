# Application branding

Compatible applications can reuse the shipped Web and TUI without maintaining a
fork of the runtime. Set `ARGUS_APPLICATION_BRAND` to an absolute path to a
packaged UTF-8 JSON manifest before invoking the normal Argus entrypoint:

```json
{
  "schema": 1,
  "id": "example",
  "name": "Example",
  "tagline": "Engineering workbench",
  "terminal_wordmark": "example",
  "assets": {
    "mark": "mark.svg",
    "mark_dark": "mark-dark.svg",
    "logo": "logo.svg",
    "logo_dark": "logo-dark.svg",
    "icon": "icon.png",
    "icon_dark": "icon-dark.png"
  }
}
```

Assets must be local PNG/SVG files below the manifest directory, without
symlinks or parent traversal. The manifest is limited to 16 KiB and each asset
to 256 KiB. Invalid explicit configuration fails startup rather than presenting
another application's identity. Ship only public, licensed assets in this
directory, never project state.

The real WebAPI captures the manifest and asset bytes at startup. Its read-only
`/api/application-brand` response contains public labels and same-origin asset
URLs, never filesystem paths. The Web shell loads that identity before rendering
its wordmarks, follows both system and manually selected light/dark themes, and
updates the browser title, icons and installable manifest. The administrator data
viewer retains its existing independent identity. Reload/restart the application
to change branding; these endpoints are not a runtime configuration editor.

The Python launcher supplies the validated public identity to the bundled TUI.
Custom terminal branding uses accessible text in the splash, header and prompt;
it does not pretend to render an SVG logo in a text terminal. Direct development
launches of the TUI without the Python launcher retain Argus defaults.

Unset configuration preserves the original Argus artwork and labels. This
extension does **not** rename Python packages, change protocol IDs, rewrite user
messages or historical evidence, select a project/state root, grant authority,
change models, or alter daemon ownership. Backend diagnostics and native CLI
help can still refer to the actual Argus engine. Desktop installer identity,
native icons and updater channels are separate packaging concerns.
