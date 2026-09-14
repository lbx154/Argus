# The Vertical Store

Argus ships seven verticals. The other seventeen live in the community repository
[`Argus-AiTeam/argus-verticals`](https://github.com/Argus-AiTeam/argus-verticals),
one directory each. The **Vertical Store** installs those directories into a
user-level location, one vertical at a time, without `pip`: it reads the
repository's release catalog, downloads the vertical's zip, verifies it, unpacks it
under `~/.argus-skill/verticals/argus_verticals/<name>/`, and the running Argus
discovers it there exactly like an entry-point plugin. Installing `chip_design`
brings `digital_circuit` with it (the catalog says it `requires` it); removing
`chip_design` leaves `digital_circuit` in place.

Why a store next to `pip install argus-verticals`: the frozen desktop has no `pip`;
a hosted image wants a prepared, read-only root shared by every tenant; and an
operator who needs one vertical should not have to install `torch` for another.
The store never installs Python dependencies: a vertical's `python_requirements`
are *shown* (with the ones whose module is missing flagged), so the decision stays
with the operator.

## Surfaces

| surface | where |
|---|---|
| CLI | `argus verticals {list,info,install,update,remove,enable,disable,refresh}` (also `python -m argus verticals ...`, never Node) |
| Web API | `GET /api/verticals`, `POST /api/verticals/catalog/refresh`, `POST /api/verticals/{name}/manage/{action}`, `GET /api/verticals/{name}/operation` (capability `verticals.store.v1`) |
| Web UI | the Verticals page of the cockpit, built on the API above |
| hosted images | `python -m argus.release_tools.preinstall_verticals`, `ARGUS_VERTICALS_PREINSTALL`, `ARGUS_VERTICALS_HOST_ROOT` |
| code | `argus/verticals/store.py` (operations, rows), `argus/verticals/_registry.py` (discovery), `argus/webapi/routes/verticals.py`, `argus/apps/cli/_verticals.py` |

## Layout on disk

The store root is `<ARGUS_SKILL_HOME>/verticals` (`core.paths.verticals_root()`), or the
directory named by `ARGUS_VERTICALS_HOST_ROOT` when that is set.

```
verticals/
  argus_verticals/<name>/...          the vertical's directory, exactly as in the repository
  argus_verticals/literary/shared/    a shared helper tree, at its repo-relative place
  registry.json                       installed verticals + shared-tree ownership (host state)
  store.lock                          portalocker lock around every registry write and job start
  catalog.json                        the last catalog fetched, or the last failure (see Catalog)
  operations/<name>.json              the running or last job for that name
  logs/<name>.log                     one line per job step
  .staging/<name>-<pid>-<id>/         per-job scratch; swept when its owner.json names a dead process
```

Next to it, but always under the *user's* home even when the store root is a shared host
root, lives the per-user overlay:

```
~/.argus-skill/verticals/state.json   {"schema": 1, "disabled": ["quant"]}
```

`registry.json` records what is installed and never whether it is enabled:

```json
{"schema": 1,
 "verticals": {"chip_design": {"version": "0.1.0", "sha256": "…", "module": "argus_verticals.chip_design.stages",
                               "source": {"repo": "Argus-AiTeam/argus-verticals", "tag": "v0.1.0", "url": "https://…/chip_design-0.1.0.zip"},
                               "installed_at": 1789400000.0,
                               "requires": ["digital_circuit"], "shared": [], "paths": ["argus_verticals/chip_design"]}},
 "shared": {"argus_verticals/literary/shared": {"owners": ["prose", "modern_poetry"], "sha256s": {"prose": "…", "modern_poetry": "…"}}}}
```

**Enabled is per user.** A vertical is enabled when it is installed and not in the user's
`disabled` list. `enable`/`disable` write only `state.json`, so they work on a read-only
host root and one tenant's choice never changes what another tenant sees. When the overlay
directory cannot be written, the rows simply do not offer `enable`/`disable`. The
`sha256s` per owner are compared when a second owner installs the same shared tree; a
differing copy is installed (the newer archive wins) and logged as a warning.

There is no `argus_verticals/__init__.py` on disk. Discovery registers `argus_verticals`
as a namespace package pointing at the store directory; when a pip-installed
`argus_verticals` exists, the store directory is appended to that package's `__path__`
instead, so the pip copy wins for a name both provide (the store page shows such a
row as kind `package`). Nothing reads dist-info, which is why the frozen desktop
discovers store verticals too.

## Catalog

The catalog is the `catalog.json` attached to the latest release of the community
repository (`https://github.com/Argus-AiTeam/argus-verticals/releases/latest/download/catalog.json`).
Per vertical it names the version, module, `paths`, `requires`, `shared`,
`python_requirements`, tags, and the archive's `url`, `sha256` and `size` (see the
repository's README, "Store metadata"). The store validates `schema == 1` and every
field it uses and caches the result for six hours. A failed fetch is recorded in the
same cache file (`failed_at`, `error`): the stale catalog is served with `catalog.error`
set and the network is **not retried for five minutes** (a blackholed host would otherwise
stall every poll for the whole HTTP timeout); `argus verticals refresh` and
`POST /api/verticals/catalog/refresh` always try. The catalog is refused as a whole when
an entry carries a built-in vertical's name or two entries claim the same directory (a
shared tree may not lie inside a vertical's directory either).

`ARGUS_VERTICAL_CATALOG` overrides the source: an https URL on an allowed host, a
local path, or a `file://` URL. A local catalog turns its directory into an offline
mirror — an archive named in the catalog and present next to it is used before its
URL — so `python scripts/build_catalog.py --release vX --dist DIR` in the community
repository produces a complete offline store source.

## Hosted mode

Set `ARGUS_VERTICALS_PREINSTALL=materials,chip_design` and the web server brings those
verticals to installed, current and enabled at startup on its own thread (the
interface is never delayed). To bake them into an image:

```
ARGUS_SKILL_HOME=/tmp/build \
python -m argus.release_tools.preinstall_verticals materials chip_design --root /opt/argus-verticals
```

Point every tenant at that root with `ARGUS_VERTICALS_HOST_ROOT=/opt/argus-verticals`.
With that variable (or `ARGUS_TRIAL_HARNESS`) set the store is **host-managed**:
`install`, `update` and `uninstall` are refused (CLI exit 1, API 409; the hosted trial
answers 403 before reaching the store), the rows carry `managed_by_host: true`, and
only `enable`/`disable` remain. Those two write the tenant's own
`<ARGUS_SKILL_HOME>/verticals/state.json`, never the host root, so they work on a
read-only root and are private to that tenant by construction; refreshing the catalog is
read-only for the host and stays available. The invitation portal whitelists exactly
`POST /api/verticals/catalog/refresh` and `POST /api/verticals/<name>/manage/(enable|disable)`.

## Security

- **Allow-listed hosts only.** Catalog and archive URLs must be `https://` on
  `github.com`, `objects.githubusercontent.com` or `release-assets.githubusercontent.com`;
  redirects are followed manually and every hop is checked against the same list.
  `file://`/local archives are accepted only when the catalog itself is a local file.
- **Size caps and digests.** Catalog 8 MiB, archive 256 MiB, expanded 512 MiB. The
  downloaded archive must match the catalog's `size` and `sha256` before it is opened.
- **Extraction guards.** Every member is checked before anything is written: no
  absolute paths, no `..`, no symlinks, and every member must lie under one of the
  trees the catalog declares for *that* vertical (`paths` + `shared`) — an archive
  cannot drop a file into another vertical's directory or a package-root `__init__.py`.
  The tree must contain `<paths[0]>/stages.py`.
- **Atomic placement.** Archives are unpacked in `.staging/` and swapped into place per
  tree with a rename; the replaced tree is kept complete as a backup, so a failure at
  any later step restores exactly what was there. An update of a parent directory
  (`digital_circuit`) keeps an installed nested vertical (`digital_circuit/benchmark`)
  by copying it into the new tree. Staging directories carry an `owner.json`; those
  whose process is gone are swept at the next install.
- **One job per name, across processes.** The "already running" check and the
  operation record are written under the store's file lock, so two `argus verticals
  install` processes cannot both run the same job.
- **No pip, no code execution during install.** The module is imported only by
  discovery, after installation, and validated like an entry-point plugin
  (`ARGUS_VERTICAL_API_VERSION`, `VERTICAL_PURPOSE`, `vertical_contract`,
  `VERTICAL_SKILL_PARENTS`); a built-in's name is refused, a broken module is logged and skipped.
- **Registry writes** happen under a `portalocker` lock; a job's `operation.json`
  records its pid and process identity, so a job whose process died is reported failed.

## CLI

```
argus verticals refresh                     # fetch the catalog again
argus verticals list [--json]               # every vertical: builtin / package / installed / available
argus verticals info chip_design [--json]   # one row: requires, shared, python_requirements, missing_python, used_by, actions
argus verticals install materials chip_design   # dependencies first; waits and prints progress
argus verticals update [NAME ...]           # reinstall when the catalog's version/sha changed (all when no NAME)
argus verticals disable quant / enable quant
argus verticals remove chip_design [--force]    # --force: even when a session's PIPELINE_STATE.json names it
```

Exit status: 0 success, 1 the store refused or the job failed (message on stderr),
2 an unresolved path placeholder or a usage error. Names are lowercased on the way in
(`argus verticals install Quant` installs `quant`). `remove` also refuses when a session's
`PIPELINE_STATE.json` cannot be read (its vertical is unknown); `--force` overrides both.
Sessions are looked up under the home and under every root in
`ARGUS_SKILL_WEB_SESSION_ROOTS`; the web server passes its own session roots.

## API

| method + path | body | answer |
|---|---|---|
| `GET /api/verticals` | – | `{"verticals": [row…], "catalog": {"source", "fetched_at", "release_tag", "error"}, "host": {"managed_by_host", "store_root"}}` |
| `POST /api/verticals/catalog/refresh` | – | the same payload after a forced fetch (a broken catalog is reported in `catalog.error`) |
| `POST /api/verticals/{name}/manage/install` | `{"force"?: bool}` | 202 `{"name", "action", "operation"}` |
| `POST /api/verticals/{name}/manage/update` | | 202, same |
| `POST /api/verticals/{name}/manage/uninstall` | `{"force": true}` to override `used_by` | 202, same |
| `POST /api/verticals/{name}/manage/enable` / `disable` | – | 200 `{"name", "action", "operation": null}` |
| `GET /api/verticals/{name}/operation` | – | `{"status": "running"\|"done"\|"failed", "action", "progress": 0–100, "message", "started", "finished", "pid"}` or 404 |

Wire types: `catalog.fetched_at`, `operation.started` and `operation.finished` are
ISO-8601 UTC strings (`"2026-09-14T19:00:00Z"`) or `null` — never epoch numbers;
`operation.progress` is an integer percent, `1`–`99` while a step runs and `100` on
the finished record (`0` before the first step).

Errors: a store refusal is 409 `{"detail": message}`; an unknown name or action is
404; writes are same-origin only (403 otherwise); every route needs the usual bearer
token. A row has `name, purpose, purpose_zh, kind, version, installed_version, enabled,
update_available, requires, shared, python_requirements, missing_python, tags,
size_bytes, used_by, operation, managed_by_host, actions`; `kind` is one of
`builtin`, `package` (pip-installed `argus-verticals`), `installed` (store) or
`available` (catalog only), and `actions` lists exactly the manage actions the row
accepts in its current state.

## Troubleshooting

- *`catalog … could not be downloaded`* — no network or GitHub is unreachable. The
  last good catalog is served with `catalog.error` set; `argus verticals refresh`
  retries. Point `ARGUS_VERTICAL_CATALOG` at an offline mirror if needed.
- *`archive sha256 … does not match`* — the download was tampered with or the release
  was rebuilt; nothing was installed. Refresh the catalog and retry.
- *`X is required by installed vertical(s) Y`* — remove `Y` first; the store never
  cascades a removal.
- *`X is the vertical of session(s) s-…`* — a local project still names it; disable
  instead, or `remove --force` once the project is finished.
- *`verticals are provided by the host`* — `ARGUS_VERTICALS_HOST_ROOT` or
  `ARGUS_TRIAL_HARNESS` is set; installation belongs to the image build.
- *A vertical is installed but not in `available_verticals()`* — check
  `argus verticals info NAME` (`enabled`; a project naming a disabled vertical is told
  to run `argus verticals enable NAME`), then the server log: a store module that
  fails to import or violates the contract is skipped with a warning naming it.
- *`enable`/`disable` missing from a row* — `~/.argus-skill/verticals/` is not writable
  for this user; the overlay `state.json` cannot be recorded.
- *`missing_python`* lists a requirement — install it into the Python that runs Argus
  (`pip install <requirement>`), or accept the documented gap for optional ones.
