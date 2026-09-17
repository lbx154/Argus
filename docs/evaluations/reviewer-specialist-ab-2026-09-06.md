# Specialist review token-efficiency A/B

Date: 2026-09-06. Baseline: upstream `0840b58e03d1`.
Coordination: upstream issue #96.

## Change and authority boundaries

Two concrete issues remained after the existing token-accounting and
unchanged-PDF reuse fixes:

1. The prompt catalog prepended the integrated research Reviewer banner to
   `cold_read` and `science_loss_check`. That banner asks the recipient to build
   a contribution, repair the current stage, and overwrite `paper/REVIEW.md`.
   These instructions conflict with the specialists' existing read-only,
   PDF-only or immutable-snapshot assignments. The specialists now receive
   their complete operation-specific policy without this generic banner.
   The integrated Reviewer's banner, venue standard, and writeback authority
   are unchanged.
2. Specialist results concatenated every assistant message, including progress
   reports and tentative findings, into the integrated Reviewer's evidence.
   The host now requests a complete final assessment and forwards that final
   message without truncating it. It does not promote earlier progress into
   evidence when the final message is empty. Original provider logs and usage
   accounting are unchanged.

These changes do not remove a specialist pass, cache scientific judgments,
relax the independent integrated review, change models or reasoning settings
in production, or widen budgets or permissions. Existing PDF/model/policy
invalidation continues to apply. No new cache, fingerprint, or retry mechanism
was added.

## Live prompt A/B

The opt-in script `reviewer_specialist_ab.py` sends four public miniature
fixtures to a Responses-compatible endpoint, once per arm, with no tools.
Both arms receive the same operation policy and fixture instruction.
The baseline recreates the catalog's old generic-banner-plus-operation
composition; the candidate uses the changed catalog directly.

The run requested `gpt-5.6-sol`, used low reasoning effort, and received
`gpt-5.6-sol` in each response. The host's existing relay internally maps that
requested model to `gpt-5.6-sol-fast`; this is a paired comparison on that
route, not a claim of validation on every deployment of either model name.
There were eight requests total, no retries, and no production configuration
changes. Arm order alternates AB/BA. Every response reported zero cached input
and zero reasoning tokens.

| Fixture | Expected | Baseline input tokens | Candidate input tokens | Baseline / candidate result |
|---|---|---:|---:|---|
| Clear latency comparison with explicit scope | pass | 642 | 518 | pass / pass |
| 2x speedup claim contradicted by equal latencies | fail | 615 | 491 | fail / fail |
| Adverse result relocated with an explicit appendix reference | pass | 404 | 280 | pass / pass |
| Adverse result omitted from the after snapshot | fail | 393 | 269 | fail / fail |
| **Total** | **4/4 correct per arm** | **2,054** | **1,558** | **No fixture verdict regression** |

Input tokens decreased **24.15%** on these fixtures. Output tokens increased
from **255 to 288**, so total reported tokens decreased from **2,309 to 1,846**
or **20.05%** in this run. The raw public-fixture assessments and per-request
usage are in `reviewer-specialist-ab-2026-09-06.json`.

These are narrow semantic acceptance fixtures, not full papers: the shared
fixture instruction explicitly limits the judgment to contradictions and
edit-induced loss. This tests prompt composition, not the additional final-
assessment instruction in the complete host pass or a real PDF rendering/tool
workflow. Four paired fixtures cannot establish general scientific-quality
equivalence, total production savings, or a statistically stable output-token
reduction. The production reasoning setting was not changed to the probe's
low setting.

### Reproduction

From this checkout, using the existing Argus Python environment:

```bash
# Supply your authorized endpoint and key without committing the credential.
PYTHONPATH=. python docs/evaluations/reviewer_specialist_ab.py \
  --endpoint "$ARGUS_AB_RESPONSES_ENDPOINT" \
  --model gpt-5.6-sol \
  --output /tmp/reviewer-specialist-ab.json
```

The script requires `ARGUS_AB_API_KEY` in its environment. It reads no local
manuscripts, account configuration, or runtime logs. Provider failures are
surfaced, completed results are saved after each request, and a wrong expected
verdict makes the run fail.

## Historical handoff replay: a separate measurement

A read-only local replay covered 74 completed, error-free isolated PDF-review
sessions observed before 07:47 PDT on 2026-09-06. Their 215 nonempty assistant
messages totalled 47,232 characters under the old newline-concatenation rule.
Keeping each complete final message totalled 27,066 characters:
**20,166 fewer characters, or 42.70%**.

This is an aggregate character measurement of the specialist assessment field,
not a tokenizer measurement or a second live experiment. It does not include
the surrounding integrated-review prompt, measure upstream specialist-call
savings, or prove every historical final answer was substantively complete.
Raw trajectories, unpublished manuscripts, session identifiers, and account
identities stay local and are not included in this report.

The regression fixture explicitly includes a tentative missing-control concern
followed by a final assessment that corrects it and retains a real page-2
overlap finding and repair. Only the complete final evidence is forwarded and
cached; the actual provider usage remains accounted for. Empty final messages
remain failures rather than falling back to progress.

## Focused regression evidence

80 existing/extended tests passed across:

- `tests/test_paper_pass_reuse.py`
- `tests/skills/test_paper_narrative_packaging.py`
- `tests/test_stop_kind_lifecycle.py`
- `tests/skills/test_stage_checklists.py`
- `tests/roles/test_prompt_catalog.py`

This includes the real pass orchestration with test backends, immutable/PDF-only
workspace boundaries, unchanged-PDF cache hits, changed PDF/policy/model
invalidation, provider failures and stop signals, operation-specific prompt
composition, and preservation of integrated review authority. Ruff passed for
the changed Python files and opt-in A/B script.

## Deployment and cooperation

Development and comparisons used an isolated branch/worktree. Active research
jobs and the shared runtime checkout were not edited or restarted. The patch
is for upstream review before a coordinated deployment at a safe job boundary.
An in-memory cache or already-running Python process is not claimed to have
adopted the new code merely because the branch was pushed.
