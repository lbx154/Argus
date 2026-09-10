# Research map

The Web UI's Map tab shows tasks and their recorded execution steps on one canvas. Click a task or zoom in to reveal its submap; click a step to read its details. The minimap supports navigation, and the toolbar can locate the current task or fit the whole map.

The attention button cycles through tasks needing input, then failed tasks. Opening one shows its position in that list and the recorded question or latest explicit failure reason. Search reports the total matches until one is focused; Enter advances through them and the counter identifies the displayed result.

**Trace dependencies** frames the selected task and its direct recorded prerequisites and dependents, dimming unrelated cards and links without changing their positions. The strip above the canvas names those tasks. Click a card or a neighboring task in the strip to trace it; click the selected card again, toggle the button off, or use **Fit map** / **F** to return to the normal overview. **Open task** in the strip opens the selected task's details. Content relationships and plan replacements are not treated as scheduling dependencies, and tracing does not call a model.

On phones, the header combines the title and data source. Opening a live map without a saved view starts at running work, otherwise a task needing attention, planned work, or the latest task. Saved views still take precedence. The legend, minimap and zoom controls step aside while a task is open; the toolbar retains navigation back to the overview. Step readers continue to fit above the message composer.

Task frames fit their content. Longer tasks continue in linked cards. Solid lines represent recorded dependencies; dashed lines represent content relationships or plan replacements. Generated relationships do not change scheduling dependencies.

Time advances across columns in the outer map. The layout chooses column boundaries using card dimensions and displayed relationships, then lets vertical positions follow connected cards. Ordered compaction preserves task order and a minimum gap while producing staggered branches. Card positions are independent of zoom and changes to generated wording. The initial overview and Fit map account for the toolbar, composer and minimap, and adapt to window resizing until the user navigates manually.

Each outer card contains at most 12 steps. Longer tasks continue through linked cards titled “Continued 1”, “Continued 2”, and so on. These are parts of the same task: they preserve the original step order and event IDs without adding scheduler tasks. Incoming task relationships enter the first part; outgoing relationships leave the last part. Earlier parts show recorded progress, while the final part carries the task's current state and activity indicator. References include the task ID, part number and events from the selected card.

Within a part, equal-sized step cards follow an ordered grid: read down each column, then advance right. Column headings identify step ranges, and cards retain their stage and recorded round. Previous/next controls navigate between parts on the same canvas; touch layouts also provide these controls above the canvas. Appending a part keeps an automatically focused card in view.

The map input sends messages through the existing Manager message endpoint. Right-click a card to insert a reference containing its source, task and event identifiers. Click the Argus icon to select attachments. File formats, limits, previews and uploads use the same implementation as the chat composer. A failed upload preserves the draft and attachments; text entered while a message is being sent is kept for the next message.

In the overview, an empty composer becomes a small message bar. Hover or focus it to expand; on touch devices, tap the text field. Drafts, references, selected files and pending messages keep it expanded. It also stays open while viewing a task submap. Expansion uses a short, restrained transition and respects reduced-motion preferences. The zoom controls sit horizontally below the legend.

## Data and optional summaries

`GET /api/projects/{sid}/map` projects task history and public lifecycle/round events. Existing event IDs are retained; legacy events receive stable presentation IDs. When an older event has no task ID, it is associated only if exactly one task was active. Ambiguous events are not attached to an arbitrary task. Private execution-progress text is not included.

Opening a session with at least 40 tasks or an event log of at least 8 MiB first asks how much history to load. The size check does not read event contents or call a model. Choose **Start at current progress**, **Load from the beginning**, or **Keep map mode off**. The dialog explains loading time and additional model tokens. The choice is remembered per session in the browser and can be changed using **History range** at the top right. Short new sessions load automatically; their subsequent progress continues without another prompt.

Full history loads in pages through `/api/projects/{sid}/map-history`, processing roughly 1 MiB of source records and returning at most 500 matching events per page. A local SQLite index under the Argus home retains event identities and permits summaries to cite old evidence. It is a rebuildable projection, not research state. Summary generation waits until the initial pages have loaded. The current-progress option limits tasks and events to the selected starting point while retaining dependency references to earlier tasks.

Subsequent requests use cursors and return changed tasks and new events. Unchanged records and nodes are reused; a temporary fetch failure keeps the loaded map visible. Reopening the map restores its viewport, and old steps do not replay their arrival animation. A paused or stopped session disables activity indicators; recorded progress can finish loading before the map settles. Resuming continues from new evidence rather than rebuilding completed summaries.

The recent-record `/map` endpoint remains available: it reads at most the last 8 MiB and retains up to 2,000 matching events, with `coverage.truncated` indicating partial coverage. Full-history pages are not limited to that tail. Index writes, partial trailing lines, file replacement and stale cursors are handled separately from scheduler records.

Card summaries use the same runner, account and provider configuration as the research Engineer. By default, the model and reasoning effort also follow that role. The map does not require a separate API URL or key.

Open Map model settings from the map header or the existing Settings window to choose a summary model or reasoning effort. Leave the model blank, or choose **Follow research settings**, to restore inheritance. These are instance-wide settings, consistent with the existing research settings. They use the existing authenticated `/api/projects/{sid}/config/set` endpoint and take effect on subsequent generation requests without restarting Argus.

| Setting | Default | Behavior |
| --- | --- | --- |
| `ARGUS_SKILL_MAP_MODEL` | `auto` | Follow the research Engineer model; a model ID overrides summaries only |
| `ARGUS_SKILL_MAP_REASONING_EFFORT` | `auto` | Follow the research Engineer effort; accepts `low`, `medium`, `high`, `xhigh`, `max` |

Runner and account changes remain in the existing research configuration. A summary model must be available through that runner. If the runner or login is unavailable, the map keeps recorded text and cached summaries.

Generation sends selected public task/event fields through a separate read-only runner turn. It does not resume the research conversation or enqueue tasks. Task ownership, event ownership and output completeness are checked before publication. Valid cached summaries remain usable across model and cache-format changes; new model settings apply when new or changed evidence needs text. Child summaries describe their selected events and do not change when a later task state changes. Monotonic cache revisions prevent late responses from overwriting newer text or relationships. Existing relationships are retained when new work connects to the map.

`GET /api/map-copy/{source}/{name}` reads the cache; `POST` requests text for specified cards. Routes use the existing Web API authentication. Batches contain at most eight cards; requests for the same source share a generation across tabs and API workers. Different sessions can prepare summaries independently. Leaving the map does not discard an in-flight result. Calls use Argus's shared quota and cost-control path, and their usage is recorded in the owning session under `map-summary`. Pricing availability follows the configured runner's normal accounting. Historical generation requires a valid `session_id` for attribution. Summary calls remain in usage records but do not become research steps on the map. Kiosk mode disables generation and hides message/upload/reference controls.

## Historical datasets

`ARGUS_MAP_DATASETS_DIR` can point to a directory of read-only datasets and an `index.json`. These are optional: live sessions work without them. Imported datasets should contain explicit snapshots with credentials and local paths removed. They are never used as runnable Argus state. A reference to a historical card is sent to the currently selected session, identified beside the composer.

Task scheduling and role ownership remain with the existing Harness. The map adds a read projection, presentation cache and UI interactions; dragging or displaying a relationship does not edit the task graph.
