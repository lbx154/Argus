# Research map

The Web UI's Map tab shows tasks and their recorded execution steps on one canvas. Click a task or zoom in to reveal its submap; click a step to read its details. The minimap supports navigation, and the toolbar can locate the current task or fit the whole map.

Task frames fit their content. Longer tasks continue in linked cards. Solid lines represent recorded dependencies; dashed lines represent content relationships or plan replacements. Generated relationships do not change scheduling dependencies.

Time advances across columns in the outer map. The layout chooses column boundaries using card dimensions and displayed relationships, then lets vertical positions follow connected cards. Ordered compaction preserves task order and a minimum gap while producing staggered branches. Card positions are independent of zoom and changes to generated wording. The initial overview and Fit map account for the toolbar, composer and minimap, and adapt to window resizing until the user navigates manually.

Each outer card contains at most 12 steps. Longer tasks continue through linked cards titled “Continued 1”, “Continued 2”, and so on. These are parts of the same task: they preserve the original step order and event IDs without adding scheduler tasks. Incoming task relationships enter the first part; outgoing relationships leave the last part. Earlier parts show recorded progress, while the final part carries the task's current state and activity indicator. References include the task ID, part number and events from the selected card.

Within a part, equal-sized step cards follow an ordered grid: read down each column, then advance right. Column headings identify step ranges, and cards retain their stage and recorded round. Previous/next controls navigate between parts on the same canvas; touch layouts also provide these controls above the canvas. Appending a part keeps an automatically focused card in view.

The map input sends messages through the existing Manager message endpoint. Right-click a card to insert a reference containing its source, task and event identifiers. Click the Argus icon to select attachments. File formats, limits, previews and uploads use the same implementation as the chat composer. A failed upload preserves the draft and attachments; text entered while a message is being sent is kept for the next message.

In the overview, an empty composer becomes a small message bar. Hover or focus it to expand; on touch devices, tap the text field. Drafts, references, selected files and pending messages keep it expanded. It also stays open while viewing a task submap. Expansion uses a short, restrained transition and respects reduced-motion preferences. The zoom controls sit horizontally below the legend.

## Data and optional summaries

`GET /api/projects/{sid}/map` projects task history and public lifecycle/round events. Existing event IDs are retained; legacy events receive stable presentation IDs. When an older event has no task ID, it is associated only if exactly one task was active. Ambiguous events are not attached to an arbitrary task. Private execution-progress text is not included.

The endpoint currently reads the last 8 MiB of the event log and returns at most 2,000 matching events. `coverage.truncated` identifies incomplete coverage. This is a recent-history view, not a paginated archive of an entire long-running project.

Card summaries use the same runner, account and provider configuration as the research Engineer. By default, the model and reasoning effort also follow that role. The map does not require a separate API URL or key.

Open Map model settings from the map header or the existing Settings window to choose a summary model or reasoning effort. Leave the model blank, or choose **Follow research settings**, to restore inheritance. These are instance-wide settings, consistent with the existing research settings. They use the existing authenticated `/api/projects/{sid}/config/set` endpoint and take effect on subsequent generation requests without restarting Argus.

| Setting | Default | Behavior |
| --- | --- | --- |
| `ARGUS_SKILL_MAP_MODEL` | `auto` | Follow the research Engineer model; a model ID overrides summaries only |
| `ARGUS_SKILL_MAP_REASONING_EFFORT` | `auto` | Follow the research Engineer effort; accepts `low`, `medium`, `high`, `xhigh`, `max` |

Runner and account changes remain in the existing research configuration. A summary model must be available through that runner. If the runner or login is unavailable, the map keeps recorded text and cached summaries.

Generation sends selected public task/event fields through a separate read-only runner turn. It does not resume the research conversation or enqueue tasks. Task ownership, event ownership and output completeness are checked before publication. Model changes invalidate cached summaries, and results from an older setting cannot replace the new selection. Child summaries describe their selected events rather than rewriting earlier attempts using a later result.

`GET /api/map-copy/{source}/{name}` reads the cache; `POST` requests text for specified cards. Routes use the existing Web API authentication. Batches contain at most eight cards and repeated updates are coalesced. Calls use Argus's shared quota and cost-control path, and their usage is recorded in the owning session under `map-summary`. Pricing availability follows the configured runner's normal accounting. Historical generation requires a valid `session_id` for attribution. Summary calls remain in usage records but do not become research steps on the map. Kiosk mode disables generation and hides message/upload/reference controls.

## Historical datasets

`ARGUS_MAP_DATASETS_DIR` can point to a directory of read-only datasets and an `index.json`. These are optional: live sessions work without them. Imported datasets should contain explicit snapshots with credentials and local paths removed. They are never used as runnable Argus state. A reference to a historical card is sent to the currently selected session, identified beside the composer.

Task scheduling and role ownership remain with the existing Harness. The map adds a read projection, presentation cache and UI interactions; dragging or displaying a relationship does not edit the task graph.
