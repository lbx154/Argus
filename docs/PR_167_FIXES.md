# PR #167 repair summary

Branch: `spec-gen-tianyu`. This summarizes the review fixes and follow-up checks
for the Verus specification-generation Skills and Reviewer validation work.

## Repairs

| Problem | Change |
| --- | --- |
| Completed Reviewer usage disappeared when cleanup failed. | `Reviewer.evaluate()` preserves returned token counts, premium requests and existing session metadata on blocked outcomes. An earlier approval still cannot survive cleanup or receipt failure. |
| Report finalization had additional exception paths. | Temporary report cleanup, report writing and stage-state persistence now share the same error boundary. A completed provider call remains accounted for when finalization blocks the review. |
| Five Skill headers violated the shipped-Skill format. | Quote `name` and `description` as JSON-compatible strings. Parsed metadata and Skill instruction bodies remain unchanged. |
| An empty image default violated the knob registry's display contract. | Display `(unset)` in the registry/help while keeping the runtime default empty and validation disabled. Environment and persisted-setting precedence are unchanged. |
| Synthetic proxy URLs failed public-tree hygiene checks. | Replace three fixture hostnames with the existing approved `example.invalid` domain. Keep credentials in the fixtures and retain all isolation/redaction assertions. |
| Chinese display guides were missing for new catalog entries. | Add ten entries for the five Skills and five reference pages, and refresh the existing source metadata for affected role guides. |
| Reviewer snapshot/render calls had type errors. | Skip manuscript snapshot lookup when no root is supplied, and pass render arguments directly. The seven previously reported targeted mypy errors are resolved. |
| Filesystem initialization could outlive the turn without being tracked. | Register the call-bound worker before directory creation, log-file preparation and the first receipt write. Unfinished initialization blocks approval at teardown; late failure cannot leave an earlier approval valid. |
| Pending results depended on files that might not exist yet. | Return a host-owned `running` snapshot until the worker finishes. Logs are empty only before preparation; later missing-file errors remain explicit. A provisional terminal receipt cannot prematurely report completion. |
| Cancellation competed with saturated ordinary requests. | The bridge accepts a domain-specific cancellation operation, retaining `cancel` as its default. Reviewer registers `cancel_review_command` so it uses the reserved control capacity. Authentication, ownership and handler limits remain intact. |

The existing local Docker creation/teardown budgets of 30/40 seconds are
retained. These repairs do not further increase runtime deadlines, enable
validation by default, add model/verifier calls, or introduce unsandboxed
execution.

## Regression coverage

New deterministic cases cover delayed scratch-directory and receipt creation,
turn closure before initialization finishes, late initialization success/failure,
pending finalization, missing output files, and cancellation with six concurrent
run requests. Existing nonzero-usage, native approval, report-persistence and
configuration-precedence checks remain in place.

Latest local results:

- New concurrency cases plus the original bridge/process-lifecycle contracts:
  **22 passed**.
- Combined Reviewer/Skills/registry/catalog/native-tool selection:
  first run **482 passed, 3 failed, 1 skipped**. All three Docker-related failures
  subsequently passed in focused rechecks, giving **485 distinct passing cases**
  in that selection. The skipped case requires an explicitly configured live
  Pi agent loop.
- Ruff and targeted mypy: **passed**, including the bridge, Reviewer tools,
  validation service, Reviewer core and knob registry.
- Previously completed unchanged frontend checks: **224** presentation tests
  and **17** related UI/configuration tests passed; TypeScript checking passed.

The first real-Docker runs recorded creation, execution/attach and removal
timeouts. Failed receipts were inspected, named test containers were checked,
and an independent Docker CLI create/remove probe completed before the focused
rechecks. Production timeouts and test assertions were not relaxed. The earlier
failed runs are not being represented as passes or as a completed performance
root-cause analysis.
One late-created, unstarted test container was removed by its recorded name;
the shared Docker daemon was not restarted and unrelated containers were not
touched.

## Boundaries

- Reviewer validation still needs an explicitly configured, installed local
  Docker image and approved inputs.
- Native MCP/Pi bridge tests do not establish live model quality.
- This is targeted validation, not the complete repository/platform matrix.
  Windows-specific behavior, a live Pi agent loop, real Verus/specdet workloads
  and specification-generation performance A/B were not exercised here.
