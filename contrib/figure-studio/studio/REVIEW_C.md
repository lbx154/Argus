# Independent Review C — Argus Figure Studio v2

Date: 2026-09-03  
Reviewer role: independent; no production files modified

## Overall verdict

**ISSUE, with all current scientific diagrams visually usable.** After a separate concurrent implementer rebuilt the four diagrams, their geometry, labels, native/editable PowerPoint, roundtrips, visual appearance, and determinism all pass. Three release-assurance issues remain: the gate still falls back to fail-open shared-label semantics if `data-expected-count` is absent; the build produces 1280 px PNGs despite the 2560 px convention and two manually replaced root PNGs no longer match receipts; and scenario 5 lacks a canonical receipt for its distributed SVG/PDF.

Snapshot note: the requested recent edit (e) was unsound when first tested: deleting one grid-cell text passed. During this independent review, another process added exact shared-label counts and rebuilt all four diagrams. I independently repeated the full checks against that completed rebuild. The report preserves the original adversarial evidence, marks the repaired visual defects closed, and reports the remaining current defects separately.

| Scenario | Verdict | Basis |
|---|---|---|
| 1 — MARL architecture | **ISSUE** | Scientific artifact PASS; C-4 root PNG is 2560 px but does not match the 1280 px receipted build artifact. |
| 2 — attention flow | **ISSUE** | Scientific artifact PASS; C-4 final PNG is only 1280×720 rather than the required 2560×1440. |
| 3 — federated protocol | **ISSUE** | Scientific artifact PASS; C-4 final PNG is only 1280×720 rather than the required 2560×1440. |
| 4 — NAS search space | **ISSUE** | Scientific artifact and current text-only adversarial test PASS; C-1 residual fail-open metadata fallback and C-4 PNG provenance remain. |
| 5 — ablation results | **ISSUE** | Requested font/legend/error-bar checks pass; C-5 receipt/final-artifact provenance mismatch is minor. |

## Concrete defects

### C-1 — Major (current); blocker in original edit — shared-label fallback remains fail-open

- Gate location: `studio/figure_quality_gate_v2.py:653-709` (exact-count branch plus the legacy shared-label fallback).
- Renderer location: `studio/figma_figure_renderer.py:246-268,1291-1301`.
- Emitted example: `studio/scenario_4_nas_search_space.svg:145-147`.
- In the fallback, the gate reduces every shared text to membership in a global `set`. Once any surviving `3x3 Conv` text inherits `data-shared-label="true"`, both the under-count and over-count branches are skipped for that string. Only a total count of zero is rejected.
- `_contract_labels` counts each grid candidate once because each appears once in `grid.candidates`; the renderer draws it once per layer (four times). Those are four distinct cell labels, not one pill standing in for four edges. Marking all candidate node groups shared therefore weakens coverage.
- Original confirmed false negative: removed the `Layer 1 / 3x3 Conv` text at `(x=232, y=149.14)` while leaving that cell and the other three `3x3 Conv` labels. Contract gate returned **exit 0, PASS, 0 errors** (`texts: 28`, versus 29 before tampering).
- The rebuilt renderer now emits `data-expected-count="4"`; deleting the same text returns **exit 1**, `label_count_mismatch`, expected 4 / actual 3. Thus edit (e) is sound in current renderer output.
- Residual current false negative: from the rebuilt SVG, delete the same text and remove only the `data-expected-count="4"` attributes while retaining `data-shared-label="true"`. The current gate returns **exit 0, PASS, 0 errors**. Contract mode does not require exact-count metadata on a shared label and silently re-enters the unsafe fallback. A renderer/serializer omission can therefore recreate the regression.

Edits (c) and (d) are sound for the present artifacts because each marked pill is intentionally the sole visual label for a collapsed group and now declares expected visible count 1. Removing that sole pill takes the count to zero and still fails. Original edit (e) was not sound; the concurrent exact-count addition repairs normal output, but the gate should require `data-expected-count` whenever `data-shared-label` is present to eliminate the residual fallback.

### C-2 — Major — CLOSED during review — scenario-4 chips/route collided with layer containers

- Pre-rebuild coordinates were captured at `studio/scenario_4_nas_search_space.svg:225-229`; the current rebuilt file at those lines contains the corrected terminal. Positioning implementation: `studio/figma_figure_renderer.py:306-353`.
- Output chip: `(1152,302,112,64)`. Icon tile spans `x=1164..1202`; the modeled 21 px `Output` label spans `x=1198.50..1259.50`, so the text overlaps the tile/border by about **3.50 px**. The icon glyph itself ends at `x=1196`, leaving only about 2.50 px before the text.
- Container and route locations: `studio/scenario_4_nas_search_space.svg:23-24,38-39,131-136,139-143,225-229`.
- The Input chip spans `x=16..128`, while Layer 1 begins at `x=125`, producing a **3 px node/container overlap**. The Output chip begins at `x=1152`, only **27 px** after Layer 4 ends at `x=1125`.
- The selected route's 3 px vertical segments are at `x=127` and `x=1123`. Including stroke widths, each overlaps its adjacent 1.5 px dashed container border by about **0.25 px**; visually, the orange route overwrites portions of the dashed boundary.
- These are visible in the 2560 px render as a crowded icon/“Output” lockup and ambiguous orange/border tracks. The Input text itself has about 4.87 px between tile and text. No text is clipped.
- Rebuilt result: terminal/container gaps are 28.00 px, terminal margins are 16.00 px, minimum icon/label clearance is 18.00 px, intersections are zero, and the selected vertical corridors sit at gap midpoints. Independent visual reinspection passes.

### C-3 — Minor — CLOSED during review — scenario-1 observation pill straddled the group border

- Pre-rebuild coordinates were captured at `studio/scenario_1_marl_architecture.svg:55-59,125-127`; the current rebuilt pill at line 126 contains the corrected y-position.
- The `Decentralized Actors` group top border is at `y=144.5`. The observation pill spans `y=139.62..172.62`, so it crosses and masks the group outline rather than sitting wholly inside or outside it.
- At 2560 px this interrupts the long dashed group boundary beneath a scientific edge label, making the pill resemble a second group tab. It does not overlap a node and does not impair label legibility.
- Rebuilt result: the observation pill is now at `y=161.62..194.62`, wholly inside the group whose top is `y=144.5`; independent visual reinspection passes.

### C-4 — Major — diagram PNG export is 1280 px and root copies are inconsistent

- Location: `studio/build_figure.py:317` uses CairoSVG `-W 2560`. In CairoSVG, `-W/--width` is the parent-container width, not the output width; the SVG's explicit 1280 px width wins. `--output-width 2560` is required.
- Receipted `studio/out/scenario_{1..4}_*/<id>.png` files are all **1280×720**, contrary to `CONVENTIONS.md`'s 2560 px publication export.
- A concurrent manual post-build render changed only root scenarios 1 and 4 to 2560×1440. Consequently, root scenarios 1/4 no longer match their receipt hashes, while root scenarios 2/3 remain 1280×720. SVG/PPTX/PDF root copies still match their receipted project artifacts.
- Exact current root/out dimensions: scenario 1 `2560/1280`, scenario 2 `1280/1280`, scenario 3 `1280/1280`, scenario 4 `2560/1280` (width in pixels).

```bash
/data/v-boxiuli/argus_test_env/bin/python - <<'PY'
from PIL import Image
from pathlib import Path
for i in range(1, 5):
    matches = list(Path('studio').glob(f'scenario_{i}_*.png'))
    p = matches[0]; q = Path('studio/out') / p.stem / p.name
    print(p.stem, Image.open(p).size, Image.open(q).size)
PY
for id in scenario_1_marl_architecture scenario_4_nas_search_space; do
  cmp -s "studio/$id.png" "studio/out/$id/$id.png"; echo "$id root/out cmp=$?"
done
# Both cmp results are 1.
```

### C-5 — Minor — scenario-5 final SVG/PDF are not receipt-attested

- The stated canonical receipt path `studio/out/scenario_5_ablation_results/quality/build_receipt.json` does not exist.
- The available receipt is `studio/out/_data_route/scenario_5_ablation_results/quality/build_receipt.json:12-22` and points to artifacts under `_data_route`, not to the distributed files in `studio/`.
- Root versus receipted hashes:

| Artifact | `studio/` SHA-256 | receipted `_data_route` SHA-256 | Match |
|---|---|---|---|
| SVG | `aebd4888ade6765ffc2a4d18e5e44a13fde1468ecd0e96e8f6a97badd75878a2` | `9478439f3d6e530aba5efa79ad7163ba8e0b3d1b9098743658167d85bbe338d7` | No |
| PNG | `d485cf11c4dfdbbae5a053380d47d952b7d407c6643fe9bed1ef2038244d18d6` | same | Yes |
| PDF | `227be3d5da4c0afbcb8d30c8125a0bcf99dad1ac5991b69e6d9535c74f98394a` | `ade706661f4cff148ac4054e170ccb878f36cad342c23c93892a5add47e369c7` | No |

The SVG diff includes generation time and randomized Matplotlib definition IDs; the PNGs are byte-identical, so this is provenance/determinism rather than a visual discrepancy.

## 1. Label-count semantics and adversarial tests

`_contract_labels` (`figure_quality_gate_v2.py:278-325` after the concurrent edit) counts normalized literal label mentions. Relevant expected counts are: scenario 1 `observation=4`, `trajectory=4`; scenario 3 `Broadcast Model=3`, `Local Training=4` (one message plus three activations), `Upload Gradients=3`; scenario 4 each candidate name `=1`.

The fallback `shared_labels` (`:653-709`) is a global set of strings. The logic always rejects zero occurrences, but absent exact-count metadata it accepts every positive count for a shared string. The new exact-count branch takes precedence when metadata is present. `ALLOWED_NON_CONTRACT_ROLES` at `:36` includes both `legend` and `axis`. Therefore:

- Changing legend roles to `legend` is correct; these explanatory strings are deliberately outside contract-label counts.
- Changing `Time` to `axis` is correct.
- `_draw_pill_label(extra=...)` correctly attaches the marker to the `<text>`, which is what the gate inspects.
- Graph edit (c) is correct for a genuinely collapsed one-pill label. The adversarial deletion of the only `observation` pill failed; current output also declares exact visible count 1.
- Sequence edit (d) is correct for the current grouped pills. A separate repeated-label sequence test retained multiplicity protection when no pill was marked shared; current grouped pills declare exact visible count 1. The `activation_mentions` calculation remains global rather than step-scoped, so contracts reusing text at different steps deserve care.
- Original grid edit (e) was incorrect because the repeated texts are individually rendered cell labels; its first adversarial deletion passed. Current output declares exact visible count 4 and the same text-only deletion now fails. The gate still permits missing exact-count metadata, which is C-1's residual issue.

Current and snapshot adversarial outcomes:

| Case | Untampered | Tamper | Gate result |
|---|---|---|---|
| `/tmp/review_c/post/adversarial/scenario_1_marl_architecture.svg` | rebuilt artifact | Delete sole shared `observation` pill | **exit 1**, `missing_label`, expected 4 / actual 0 |
| `/tmp/review_c/post/adversarial/scenario_3_federated_protocol.svg` | rebuilt artifact | Delete sole grouped `Broadcast Model` pill | **exit 1**, `missing_label`, expected 3 / actual 0 |
| `/tmp/review_c/adversarial/sequence_repeated.json` | snapshot | Two separate `Event` pills; delete one | **exit 1**, `missing_label`, expected 2 / actual 1 |
| `/tmp/review_c/adversarial/grid_missing_cell.json` | original snapshot | Delete Layer-1 `3x3 Conv` text only | **exit 0, PASS** (original regression) |
| `/tmp/review_c/concurrent/scenario4_missing.svg` | rebuilt/current | Delete Layer-1 `3x3 Conv` text only | **exit 1**, `label_count_mismatch`, expected 4 / actual 3 |
| `/tmp/review_c/post/adversarial/scenario_4_missing_no_counts.svg` | rebuilt/current | Same deletion plus strip exact-count attributes | **exit 0, PASS** (residual C-1) |

The first three missing-label cases satisfy the requested check that repeated labels still fail when genuinely absent. The last three isolate the original failure, its repair, and the remaining metadata-fallback weakness.

Exact construction and gate commands:

```bash
cd /data/v-boxiuli/argus_figure_test
PY=/data/v-boxiuli/argus_test_env/bin/python
mkdir -p /tmp/review_c/adversarial

# (c) A collapsed graph label: deleting its sole pill must fail.
cp studio/contracts/scenario_1_marl_architecture.json /tmp/review_c/adversarial/graph_shared.json
$PY studio/figma_figure_renderer.py render /tmp/review_c/adversarial/graph_shared.json --output /tmp/review_c/adversarial/graph_original.svg
cp /tmp/review_c/adversarial/graph_original.svg /tmp/review_c/adversarial/graph_missing_shared.svg
sed -i '/data-edge-id="observation-1".*>observation<\/text>/d' /tmp/review_c/adversarial/graph_missing_shared.svg
$PY studio/figure_quality_gate_v2.py check /tmp/review_c/adversarial/graph_missing_shared.svg --contract /tmp/review_c/adversarial/graph_shared.json --output /tmp/review_c/adversarial/graph_missing_shared.json
# exit 1; missing_label, expected_count=4, actual_count=0

# Separate repeated sequence labels: deleting one must fail.
cp studio/contracts/scenario_3_federated_protocol.json /tmp/review_c/adversarial/sequence_repeated.json
$PY studio/figma_figure_renderer.py render /tmp/review_c/adversarial/sequence_repeated.json --output /tmp/review_c/adversarial/sequence_original.svg
perl -0pi -e 's/"label": "Aggregate"/"label": "Event"/; s/"label": "Update Global Model"/"label": "Event"/' /tmp/review_c/adversarial/sequence_repeated.json
perl -0pi -e 's/>Aggregate<\/text>/>Event<\/text>/; s/>Update Global Model<\/text>/>Event<\/text>/' /tmp/review_c/adversarial/sequence_original.svg
cp /tmp/review_c/adversarial/sequence_original.svg /tmp/review_c/adversarial/sequence_missing_one.svg
sed -i '/data-edge-id="update-global-model">Event<\/text>/d' /tmp/review_c/adversarial/sequence_missing_one.svg
$PY studio/figure_quality_gate_v2.py check /tmp/review_c/adversarial/sequence_missing_one.svg --contract /tmp/review_c/adversarial/sequence_repeated.json --output /tmp/review_c/adversarial/sequence_missing_one.json
# exit 1; missing_label, expected_count=2, actual_count=1

# (e) Historical command executed before the concurrent exact-count repair.
# Re-running it with the current renderer now fails correctly (shown below).
cp studio/contracts/scenario_4_nas_search_space.json /tmp/review_c/adversarial/grid_missing_cell.json
$PY studio/figma_figure_renderer.py render /tmp/review_c/adversarial/grid_missing_cell.json --output /tmp/review_c/adversarial/grid_original.svg
cp /tmp/review_c/adversarial/grid_original.svg /tmp/review_c/adversarial/grid_missing_one.svg
sed -i '/data-label-for="layer-1--3x3-conv">3x3 Conv<\/text>/d' /tmp/review_c/adversarial/grid_missing_one.svg
$PY studio/figure_quality_gate_v2.py check /tmp/review_c/adversarial/grid_missing_one.svg --contract /tmp/review_c/adversarial/grid_missing_cell.json --output /tmp/review_c/adversarial/grid_missing_one.json
# exit 0; PASS, errors=0 (incorrect)

# Retest the concurrent in-progress exact-count repair on a fresh render.
mkdir -p /tmp/review_c/concurrent
$PY studio/figma_figure_renderer.py render studio/contracts/scenario_4_nas_search_space.json --output /tmp/review_c/concurrent/scenario4.svg
cp /tmp/review_c/concurrent/scenario4.svg /tmp/review_c/concurrent/scenario4_missing.svg
sed -i '/data-label-for="layer-1--3x3-conv">3x3 Conv<\/text>/d' /tmp/review_c/concurrent/scenario4_missing.svg
$PY studio/figure_quality_gate_v2.py check /tmp/review_c/concurrent/scenario4_missing.svg --contract studio/contracts/scenario_4_nas_search_space.json --output /tmp/review_c/concurrent/gate.json
# exit 1; label_count_mismatch, expected_count=4, actual_count=3 (correct)

# Rebuilt grouped-pill deletions still fail.
mkdir -p /tmp/review_c/post/adversarial
cp studio/scenario_1_marl_architecture.svg /tmp/review_c/post/adversarial/scenario_1_marl_architecture.svg
cp studio/scenario_3_federated_protocol.svg /tmp/review_c/post/adversarial/scenario_3_federated_protocol.svg
sed -i '/data-edge-id="observation-1".*>observation<\/text>/d' /tmp/review_c/post/adversarial/scenario_1_marl_architecture.svg
sed -i '/data-edge-id="broadcast-client-1".*>Broadcast Model<\/text>/d' /tmp/review_c/post/adversarial/scenario_3_federated_protocol.svg
$PY studio/figure_quality_gate_v2.py check /tmp/review_c/post/adversarial/scenario_1_marl_architecture.svg --contract studio/contracts/scenario_1_marl_architecture.json --output /tmp/review_c/post/adversarial/scenario_1_marl_architecture.json
# exit 1; missing_label, expected_count=4, actual_count=0
$PY studio/figure_quality_gate_v2.py check /tmp/review_c/post/adversarial/scenario_3_federated_protocol.svg --contract studio/contracts/scenario_3_federated_protocol.json --output /tmp/review_c/post/adversarial/scenario_3_federated_protocol.json
# exit 1; missing_label, expected_count=3, actual_count=0

# Residual fallback: stripping only exact-count metadata recreates the pass.
cp studio/scenario_4_nas_search_space.svg /tmp/review_c/post/adversarial/scenario_4_missing_no_counts.svg
sed -i '/data-label-for="layer-1--3x3-conv">3x3 Conv<\/text>/d' /tmp/review_c/post/adversarial/scenario_4_missing_no_counts.svg
sed -i 's/ data-expected-count="4"//g' /tmp/review_c/post/adversarial/scenario_4_missing_no_counts.svg
$PY studio/figure_quality_gate_v2.py check /tmp/review_c/post/adversarial/scenario_4_missing_no_counts.svg --contract studio/contracts/scenario_4_nas_search_space.json --output /tmp/review_c/post/adversarial/scenario_4_missing_no_counts.json
# exit 0; PASS, errors=0 (residual issue)
```

## 2. Diagram geometry and visual inspection

Legacy-mode results and independent metadata/coordinate checks:

| Scenario | Legacy gate | Text overlaps | Edge-label/node overlaps | Edge endpoints | Min font | Visual inspection at 2560×1440 |
|---|---:|---:|---:|---|---:|---|
| 1 | PASS | 0 | 0 | 10/10 start and end on source/target box boundary; none at center | 18 px | Balanced top-down layout with no clipping or stray elements. Four trajectory arrowheads are dense but distinguishable; the rebuilt observation pill clears the group outline. |
| 2 | PASS | 0 | 0 | 8/8 boundary-to-boundary; none at center | 18 px | Q/K/V branches are readable, three attention inputs are staggered, cards align, and labels do not collide with nodes or lines. |
| 3 | PASS | 0 | 0 | All message starts/ends use participant lifelines; header-card centers are not used. Self-loop return tips are offset 12 px from the lifeline by design. | 18 px | Headers/lifelines align; broadcast/upload rows, activation bars, self loops, step badges, and legend are clear; no clipping. |
| 4 | PASS | 0 | 0 | 5/5 selected-path edges are boundary-to-boundary; none at center | 18 px | Candidate cards align; terminal chips have clear spacing; the orange route is unambiguous over pale feasible edges; legend and footnote are visible. |

Commands executed:

```bash
cd /data/v-boxiuli/argus_figure_test
PY=/data/v-boxiuli/argus_test_env/bin/python
mkdir -p /tmp/review_c/post/png /tmp/review_c/post/legacy
for svg in studio/scenario_[1-4]_*.svg; do
  id=$(basename "$svg" .svg)
  $PY studio/figure_quality_gate_v2.py check "$svg" --output "/tmp/review_c/post/legacy/${id}.json"
  $PY -m cairosvg "$svg" -o "/tmp/review_c/post/png/${id}.png" --output-width 2560
done
```

All four review PNGs are 2560×1440 and were opened and inspected after the rebuild, not merely generated. The legacy JSON counts were respectively `(nodes,texts,edges) = (8,15,10), (7,14,8), (4,18,8), (22,29,5)`, each with zero errors. Scenario 2 has two advisory `label_on_group_border` warnings (`Input Embedding`, `Context Vector`); visual inspection finds both contacts intentional/readable.

The independent endpoint check parsed each `data-figure-role="edge"` path and compared its first/last point with `data-pptx-bounds`. Representative exact coordinates: scenario 1 `batch (640,385) → (640,435.5)`; scenario 2 `query (280.43,357) → (308.43,145)`; scenario 4 `selected-5 (1079,526) → (1125,334)`. Every graph endpoint is on a box boundary and none is a box center. Scenario-3 message points align to lifeline x-coordinates `150, 476.67, 803.33, 1130`, which is the correct sequence-diagram geometry.

## 3. PPTX editability and roundtrip

Python-pptx recursively inspected group contents, not only top-level shapes.

| Scenario | Slides | Top-level shapes | Recursive shapes | Groups | Pictures | Nonempty editable text frames | Contract labels missing |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 8 | 131 | 21 | **0** | 15 | 0 |
| 2 | 1 | 4 | 81 | 18 | **0** | 14 | 0 |
| 3 | 1 | 10 | 77 | 12 | **0** | 18 | 0 |
| 4 | 1 | 9 | 191 | 32 | **0** | 29 | 0 |

Every distinct node, edge, participant, message, group, layer, candidate, activation, and sublabel returned by `_contract_labels` exists as exact editable text. Intentional collapsed labels are one editable frame per shared pill: scenario 1 has one each for `observation` and `trajectory`; scenario 3 has one each for `Broadcast Model`, `Local Training`, and `Upload Gradients`. Scenario 4 has four editable frames for each candidate name. Shape counts are sane for native grouped DrawingML with copied icon/freeform paths.

Independent post-rebuild contract-mode gates on `/tmp` SVG copies plus the root PPTX files all returned exit 0 and zero errors. Counts/minimums remained 15/14/18/29 texts, 18 px, and 7.095 pt; warning counts were 7/9/15/4.

Inventory reproduction:

```bash
cd /data/v-boxiuli/argus_figure_test
PY=/data/v-boxiuli/argus_test_env/bin/python
PYTHONPATH=studio $PY - <<'PY'
from collections import Counter
from pathlib import Path
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
def walk(shapes):
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from walk(shape.shapes)
for p in sorted(Path('studio').glob('scenario_[1-4]_*.pptx')):
    prs = Presentation(p); top = list(prs.slides[0].shapes); shapes = list(walk(top))
    pictures = sum(s.shape_type == MSO_SHAPE_TYPE.PICTURE for s in shapes)
    texts = [s.text.strip() for s in shapes if getattr(s, 'has_text_frame', False) and s.text.strip()]
    print(p.stem, len(prs.slides), len(top), len(shapes),
          sum(s.shape_type == MSO_SHAPE_TYPE.GROUP for s in shapes), pictures, len(texts))
PY
```

The completed rebuild reran the roundtrips. Each `quality/roundtrip.log` records `exit_code: 0`; every `roundtrip/conversion-report.json` reports `slides: 1, warnings: 0`; inventories report the same recursive shape counts, `picture_count: 0`, and 15/14/18/29 nonempty texts. Build receipts show all stages passed and final gate warning counts 10/11/18/7.

```bash
for d in studio/out/scenario_[1-4]_*; do
  rg -n 'exit_code: 0|Slides converted: 1' "$d/quality/roundtrip.log"
  jq -c '.summary' "$d/roundtrip/conversion-report.json"
  jq -c '{shape_count,text_frames,picture_count,texts:(.texts|length)}' "$d/quality/roundtrip_inventory.json"
done
```

## 4. Determinism

Each contract was rendered twice to separate `/tmp` directories; every `cmp` returned 0.

| Scenario | SHA-256 (both copies) | Bytes |
|---|---|---:|
| 1 | `035f06db64bc166b909534fb304a2df25737b6407b8f17cc79801bb8d3a3a0bd` | 15,855 |
| 2 | `eb701b9f9d230c692edfb00963ee4da42b777b6c5d3b78e0a96243d22fe693c6` | 14,230 |
| 3 | `f557e62c6f781ed2c2b6c1c8be042aeec7f73b1f931bad98a08a92e2d5be4bcd` | 13,405 |
| 4 | `7ebeaff1cbc073c1ce93575f17afa1090eb1067a167bad48027e9e5af8992e1f` | 31,917 |

```bash
PY=/data/v-boxiuli/argus_test_env/bin/python
mkdir -p /tmp/review_c/post/determinism/{a,b}
for c in studio/contracts/scenario_[1-4]_*.json; do
  id=$(basename "$c" .json)
  $PY studio/figma_figure_renderer.py render "$c" --output "/tmp/review_c/post/determinism/a/$id.svg"
  $PY studio/figma_figure_renderer.py render "$c" --output "/tmp/review_c/post/determinism/b/$id.svg"
  cmp -s "/tmp/review_c/post/determinism/a/$id.svg" "/tmp/review_c/post/determinism/b/$id.svg"
  echo "$id cmp=$? $(sha256sum "/tmp/review_c/post/determinism/a/$id.svg")"
done
```

## 5. Scenario-5 SVG/PDF checks

The chart was opened and visually inspected. It is clean: title, axes, three grouped benchmark clusters, hatches, values, and error bars are legible; the four-column legend is centered and fully visible.

- Physical page: `416.4 × 312.132 pt` in both SVG and PDF.
- Declared font sizes: title 10 pt, axis 9 pt, ticks/legend 8 pt, bar values **7 pt**. Minimum meets the 7 pt requirement exactly.
- `pdffonts` reports embedded `STIXGeneral-Regular` and `STIXGeneral-Bold` (both embedded and Unicode).
- PDF legend text bounds are `x=98.081..366.419`, `y=25.516..37.596`, comfortably within the 416.4×312.132 pt page; it is not clipped.
- Comparing extracted PDF text bounds with the top error-bar caps gives positive clearance for all 12 values. Minimum clearance is **0.237 pt** for `75.8`; the SVG conservative geometry check gives 1.742 pt. Thus no bar value label overlaps an error bar, although the tightest PDF gap is small.
- A 300 dpi PDF raster has white margins before ink on all four sides, confirming no visible clipping.

Reproduction commands:

```bash
jq '.font_sizes_pt, .minimum_font_pt' studio/out/scenario_5_ablation_results_style.json
pdfinfo studio/scenario_5_ablation_results.pdf | rg 'Page size|Pages'
pdffonts studio/scenario_5_ablation_results.pdf
pdftotext -bbox studio/scenario_5_ablation_results.pdf /tmp/review_c/scenario5_bbox.html
rg -n 'Full|Attention|Residual|Normalization|82.5|75.8' /tmp/review_c/scenario5_bbox.html
pdftoppm -png -r 300 -singlefile studio/scenario_5_ablation_results.pdf /tmp/review_c/scenario5_pdf
sha256sum studio/scenario_5_ablation_results.{svg,png,pdf} \
  studio/out/_data_route/scenario_5_ablation_results/scenario_5_ablation_results.{svg,png,pdf}
```

## Release recommendation

The rebuilt scientific diagrams are ready, and the exact-count repair catches ordinary missing grid text. Before release, require `data-expected-count` on every shared label (or reject its absence), replace CairoSVG `-W 2560` with `--output-width 2560`, rebuild/copy all four PNGs through the receipted pipeline, and regenerate/copy scenario 5 through one canonical receipted build.
