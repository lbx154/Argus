# Optional workbench plugins

Argus provides the plugin center and lifecycle API. CrystalPilot is distributed independently: its scientific core, workbench frontend and installer source are not in this public repository or the host installation. The default installation has no enabled workbench plugins.

## Install and use

Open **Plugins → Argus CrystalPilot → Install**. Argus downloads the wheel specified by its bundled `argus_skill/plugin_catalog.json`, verifies SHA-256, creates an isolated Python environment, installs the plugin and scientific dependencies, and activates it only after its core and host interface validate.

The public distribution page is https://crystalpilot-downloads.argusbot.cn/. Immutable wheel and source archives are under `/releases/<version>/`. Argus installs the wheel; it never clones a private repository. The source ZIP is for inspection and independent builds. Upstream scientific binaries, model credentials and research datasets are not included in these artifacts.

The host checks its default and per-role execution backends. CrystalPilot supports Codex, Copilot and Pi, including mixed supported roles. Unsupported backends block workbench access with “暂不支持，敬请期待”. Provider credentials and model choices remain owned by Argus.

After installation, `/crystalpilot` in a native Argus session enables scientific tools in that interface. Opening CrystalPilot from Plugins uses its own workbench. The surfaces retain separate conversations, ownership and project bindings.

The host checks the current session's enabled entry in the plugin-owned binding
registry before attaching tools; sharing a working directory does not enable a
plugin for another session. Workbench executors retain the installation host
root separately from their own session-storage root.

## Environment and lifecycle

The plugin prepares its private scientific environment and automatically attempts free dependency installation. Missing optional/licensed components remain visible in its health report and can be repaired or configured later. For SHELX, users register with the upstream author and enter their licensed download credentials in the local administration form. Credentials are not passed through models.

Installations live under `ARGUS_SKILL_HOME/extensions/<id>/releases`. Research state and reusable scientific programs live separately. Updates are manual, staged in a new directory and activated atomically; failure retains the previous version. Active session, CLI and scientific work blocks update, disable, uninstall and environment repair. Uninstall removes release environments while preserving research data, conversations and reusable software.

The plugin center uses Argus's authenticated local administration interface. It is not a multi-tenant marketplace. Browsers submit a catalog id and action, not arbitrary installer URLs or commands. Dynamic ASGI routing activates installed plugins without restarting unrelated Argus sessions.

## Hosted deployments

On a hosted service the visitor should never have to click Install: the server prepares the workbench itself. Set `ARGUS_PLUGINS_PREINSTALL` to the comma-separated catalog ids that must be present (for example `ARGUS_PLUGINS_PREINSTALL=crystalpilot`). At startup the web server checks each one; a plugin that is installed, enabled and matches the catalog is left untouched, anything else starts the normal verified install (with its automatic setup) in the background, once, without delaying the interface. The server log records what was found and what was started. While the install runs, the sidebar entry shows a single preparing sentence with the current step; once it finishes, the entry opens the workbench directly. Plugins named this way are reported with `managed_by_host: true`, the interface hides disable and uninstall for them, and the API refuses those two actions.

The invitation runtimes declare CrystalPilot automatically and prepare its
workspace under each account's `ARGUS_SKILL_HOME/crystalpilot-runtime`.
The public portal permits the curated workbench's launch, conversations,
uploads and scientific operations, retaining invitation authentication,
same-origin checks and read-only restrictions. Installation, updates, removal
and arbitrary executable-path configuration remain service-owned; users can
check/repair the environment and submit their own SHELX download credentials.
Folder browsing and opening/importing a project cannot escape that account's
CrystalPilot workspace, including through symlinks. No backend credential or
plugin access cookie is forwarded to the browser.

To prepare a tenant before its first visit, use the same verified install:

```
ARGUS_SKILL_HOME=/tenant/home/.argus-skill \
python -m argus_skill.release_tools.preinstall_plugins crystalpilot --root /tenant/home/.argus-skill
```

The command waits for completion and exits non-zero on failure. Run it inside
the account's container, using the same absolute root as its API. Do not point
multiple accounts at a shared writable host root: the plugin stores bindings
and conversations there as well as reusable software. API routes explicitly use
their own account root, rather than overriding it with
`ARGUS_WORKBENCH_HOST_ROOT`. Startup confirms the prepared copy without
reinstalling a current enabled release. Egress must reach the distribution host
(`crystalpilot-downloads.argusbot.cn` over HTTPS) and the upstream sources used by
automatic setup. A successful install makes the workbench available but does
not imply that optional licensed components are installed; check their health
rows separately.

The curated CrystalPilot 0.4.0 environment constrains NumPy to `<2`: its
distributed cctbx wheel can crash when loaded after NumPy 2. The installer
passes catalog constraints through both pip installation stages, checks
dependency consistency, and cold-imports the scientific registry in its real
load order before activation. Changed constraints require an update even when
the plugin wheel version is unchanged. A module-only or lightweight health
check is not sufficient proof that the scientific worker can start.

## Release maintenance

The catalog is curated and pinned to reviewed HTTPS artifacts and SHA-256 digests. New plugin releases require a catalog update; this implementation does not automatically trust an online latest manifest. The public catalog at the distribution source helps maintainers inspect releases but does not override a user's bundled trust configuration.

Host release builds preserve external entries even when no plugin source exists in the checkout. They neither download nor rebuild CrystalPilot. Host contracts can run in public Argus CI without access to the private repository. A four-platform installer workflow example is provided in `docs/ci-examples/plugin-compatibility.yml`; it is not active until a maintainer with workflow permission copies it into `.github/workflows/`. Existing repository CI remains unchanged. An example is not evidence of a successful platform run.

Optional in-tree plugins can still use `python -m argus_skill.release_tools.build_plugins`. That command merges generated entries with the externally maintained catalog; its developer catalog adds local wheel paths only for packages actually built in that checkout. `ARGUS_PLUGIN_CATALOG` is a host-side override for a curated local file. Release maintainers must publish every referenced artifact before updating the host catalog.

## CrystalPilot licensing

© 2026 TopoSpace. All rights reserved. Unauthorized commercial use or derivative development of Argus CrystalPilot is prohibited. The plugin is proprietary; the public source archive does not grant commercial or derivative-development rights. Third-party components retain their respective licenses. These terms apply to the TopoSpace-owned plugin, not to Argus itself or the MIT-licensed host integration.

CrystalPilot includes Chinese and English workbench modes. The sidebar language button and Settings → Appearance share a plugin-owned preference. Changing language preserves drafts and running tasks; new user-facing replies and titles follow that default. Raw scientific records and user-authored names remain intact.

The submitting OAuth session does not have GitHub's `workflow` scope. The four-platform installer matrix is intentionally supplied as an inactive example, not a newly active workflow. Maintainers can review and enable it separately without blocking the plugin-center implementation.
