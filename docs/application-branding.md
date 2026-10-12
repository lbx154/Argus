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

The Tauri host validates both its bundled files and the authenticated backend
against the engine release manifest compiled into the host. Its application
version is independent and remains the version used by the desktop updater.
A compatible product can therefore have a different version from its pinned
Argus engine without accepting a different engine version or source digest.
Executable, token, launch nonce and process-ownership checks still apply.
This does not configure a downstream installer: it must separately supply its
own application identity, state directories, icons, backend resources and
update channel. The standard Argus release builder continues to enforce the
Argus distribution's version matrix.

### Desktop storage profiles

A compatible Tauri build must set `ARGUS_DESKTOP_PROFILE` **at compile time**,
for example `ARGUS_DESKTOP_PROFILE=vulcan`. The profile is a 1–32 character
lowercase ASCII identifier starting with a letter and containing only letters
and digits. Invalid profiles fail compilation; a runtime environment variable
cannot redirect the compiled desktop's settings. A non-Argus Tauri application
identifier requires a non-Argus profile, and a custom profile cannot use the
Argus identifier. This check runs before opening settings or starting a backend.

The default profile keeps `argus-desktop`, its existing legacy-settings
migration, and `~/.argus-skill` unchanged. Custom profiles use
`<profile>-desktop` in the platform application-data directory and never import
Argus settings, tokens, backend ownership or projects. Their default engine root
is `~/.<profile>`; an explicit `<PROFILE>_HOME` is supported, while the established
`ARGUS_SKILL_HOME` override takes precedence for compatibility. Preview storage
remains separate and ignores both engine-home overrides.

These settings are shared by the real desktop backend, trial setup, diagnostics,
updater cache and Windows webview callers. Profile selection does not configure
product artwork, the frozen backend, a signing key or an update channel. A
downstream package still needs its own Tauri identity and a validated package;
the Argus-specific preview build and installer scripts are not automatically
downstream installers.
