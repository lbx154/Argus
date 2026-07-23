"""Research-profile context for long-running life-mode projects."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from ..tools.capability_vault import (
    default_vault_path,
    load_model_api_grant,
    load_model_api_route,
    status_payload,
)

_PROFILE_ENV = "ARGUS_SKILL_RESEARCH_PROFILE"
_PROFILE_PATH_ENV = "ARGUS_SKILL_RESEARCH_PROFILE_PATH"
_EMNLP2026_PROFILE = "emnlp2026-tierharness"
_AAAI2026_PROFILE = "aaai2026-tierharness"
_NANOCHAT_PROFILE = "nanochat-autoresearch"
_DEFAULT_TEXT_MODELS = "gpt-5.5,gpt-5.5"
_DEFAULT_IMAGE_MODEL = "gpt-image-2"
_SHARED_MODEL_CACHE_ROOT_ENV = "ARGUS_SKILL_SHARED_MODEL_CACHE_ROOT"


@dataclass(frozen=True)
class ResearchProfile:
    name: str
    text: str
    sha256: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _env_text(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key) or "").strip()


def _read_profile_file(path: str) -> str:
    if not path:
        return ""
    try:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _shared_model_cache_root(env: Mapping[str, str]) -> Path:
    raw = _env_text(env, _SHARED_MODEL_CACHE_ROOT_ENV)
    return Path(raw).expanduser() if raw else Path.home() / ".cache"


def shared_model_cache_defaults(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the host-shared model/data cache env used by research missions."""

    source = env if env is not None else os.environ
    root = _shared_model_cache_root(source)
    hf_home = root / "huggingface"
    return {
        "XDG_CACHE_HOME": str(root),
        "HF_HOME": str(hf_home),
        "HUGGINGFACE_HUB_CACHE": str(hf_home / "hub"),
        "HF_DATASETS_CACHE": str(hf_home / "datasets"),
        "TRANSFORMERS_CACHE": str(hf_home / "hub"),
        "TORCH_HOME": str(root / "torch"),
    }


def ensure_shared_model_cache_environment(
    env: MutableMapping[str, str] | None = None,
) -> None:
    """Default all model/data downloads to the host-shared cache tree.

    Argus research missions often spawn Codex, Python, HuggingFace, and Torch
    subprocesses from different project directories. Setting these defaults in
    the daemon environment keeps model weights and datasets under one host cache
    instead of repeatedly downloading them into each workspace. Explicit
    operator-provided cache variables still win.
    """

    target = env if env is not None else os.environ
    for key, value in shared_model_cache_defaults(target).items():
        if not _env_text(target, key):
            target[key] = value


def ensure_research_api_environment(env: MutableMapping[str, str] | None = None) -> None:
    """Populate process env with pre-granted model API credentials if available.

    This never prints secrets. It copies the pre-approved key from the local
    capability vault (or the operator's Codex auth file) into the daemon process
    environment so child tool processes can call model APIs without repeatedly
    asking the human.
    """
    target = env if env is not None else os.environ
    ensure_shared_model_cache_environment(target)
    engineer = load_model_api_route("engineer", target)
    reviewer = load_model_api_route("reviewer", target)
    image = load_model_api_route("image", target)
    image_review = load_model_api_route("image_review", target)
    # OPENAI_* remains a compatibility export for generic child tools. Use
    # the engineer/text route as the default; route-aware tools read the vault.
    if engineer is not None and not _env_text(target, "OPENAI_API_KEY") and engineer.api_key:
        target["OPENAI_API_KEY"] = engineer.api_key
    if engineer is not None and not _env_text(target, "OPENAI_BASE_URL") and engineer.base_url:
        target["OPENAI_BASE_URL"] = engineer.base_url
    text_models = tuple(
        route.model
        for route in (engineer, reviewer)
        if route is not None and route.model
    )
    if not _env_text(target, "ARGUS_SKILL_TEXT_MODELS") and text_models:
        target["ARGUS_SKILL_TEXT_MODELS"] = ",".join(dict.fromkeys(text_models))
    if image is not None and not _env_text(target, "ARGUS_SKILL_IMAGE_MODEL") and image.model:
        target["ARGUS_SKILL_IMAGE_MODEL"] = image.model
    if (
        image_review is not None
        and not _env_text(target, "ARGUS_SKILL_IMAGE_REVIEW_MODEL")
        and image_review.model
    ):
        target["ARGUS_SKILL_IMAGE_REVIEW_MODEL"] = image_review.model



def _capability_context(env: Mapping[str, str]) -> str:
    grant = load_model_api_grant(env)
    key_source = grant.key_source if grant is not None else "missing"
    base_url_source = grant.base_url_source if grant is not None else "missing"
    text_models = ",".join(grant.text_models) if grant is not None else _DEFAULT_TEXT_MODELS
    image_model = grant.image_model if grant is not None else _DEFAULT_IMAGE_MODEL
    review_model = grant.image_review_model if grant is not None else "gpt-5.5"
    api_available = bool(grant and grant.usable)
    vault_path = grant.vault_path if grant is not None and grant.vault_path else default_vault_path(env)
    status = status_payload(env)
    raw_routes = status.get("routes")
    routes: dict[str, Any] = raw_routes if isinstance(raw_routes, dict) else {}
    raw_image_route_status = routes.get("image")
    image_route_status: dict[str, Any] = (
        raw_image_route_status if isinstance(raw_image_route_status, dict) else {}
    )
    image_tool_available = bool(image_route_status.get("available"))
    cache_defaults = shared_model_cache_defaults(env)
    cache_lines = []
    for key in (
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "HF_DATASETS_CACHE",
        "TRANSFORMERS_CACHE",
        "TORCH_HOME",
        "XDG_CACHE_HOME",
    ):
        cache_lines.append(f"- {key}: {_env_text(env, key) or cache_defaults[key]}")
    route_lines = []
    for route_name in ("engineer", "reviewer", "author", "image", "image_review"):
        route = routes.get(route_name)
        if not isinstance(route, dict):
            continue
        route_lines.append(
            "- route.{name}: available={available}, model={model}, "
            "provider={provider}, wire_api={wire_api}, base_url_source={base_url_source}, "
            "key_source={key_source}".format(
                name=route_name,
                available="yes" if route.get("available") else "no",
                model=route.get("model") or "",
                provider=route.get("provider") or "",
                wire_api=route.get("wire_api") or "",
                base_url_source=route.get("base_url_source") or "missing",
                key_source=route.get("key_source") or "missing",
            )
        )
    tool_block = (
        "- image_tool_generate: `python -m argus_skill.tools.image_api generate "
        "--prompt-file figures/<name>.prompt.txt --out figures/<name>.png --force`\n"
        "- image_tool_inspect: `python -m argus_skill.tools.image_api inspect "
        "--image figures/<name>.png`\n"
        "- image_tool_review (paper figures): `python -m "
        "argus_skill.verticals.research.figure_tool review "
        "--image figures/<name>.png --out figures/<name>.review.json`\n"
    )
    return (
        "## Granted capability layer\n"
        f"- model_api_available: {'yes' if api_available else 'no'}\n"
        f"- capability_vault_path: {vault_path}\n"
        f"- model_api_key_source: {key_source}\n"
        f"- model_api_base_url_source: {base_url_source}\n"
        f"- text_models_allowed: {text_models}\n"
        f"- image_model_allowed: {image_model}\n"
        f"- image_review_model_allowed: {review_model}\n"
        f"{chr(10).join(route_lines)}\n"
        f"- image_tool_available: {'yes' if image_tool_available else 'no'}\n"
        f"{tool_block if image_tool_available else ''}"
        "- Default authorization source: the fixed capability vault above. "
        "Treat Codex auth/config files only as one-time import sources for "
        "`python -m argus_skill.tools.capability_vault init-model-api`.\n"
        "- API routes are independent: engineer/reviewer/author/image/"
        "image_review may use different URLs, keys, providers, and models.\n"
        "- Permission model: the human has pre-approved these capabilities. Do not "
        "ask again for API access when model_api_available=yes; call the approved "
        "tool instead.\n"
        "- Isolation rule: prompts may mention capability availability and file "
        "paths, but must never read, print, summarize, or commit raw API keys. "
        "Only tool subprocesses may load the vault.\n"
        "- Use the image model for paper figures only when a figure specification "
        "is saved under figures/ or paper/; always keep the generated image, "
        "sidecar metadata, inspect output, and review output as artifacts.\n"
        "\n## Shared model/data cache layer\n"
        f"- cache_root: {_shared_model_cache_root(env)}\n"
        f"{chr(10).join(cache_lines)}\n"
        "- Rule: do not download model weights, HuggingFace hub files, datasets, "
        "or Torch checkpoints into project directories. Reuse the shared host "
        "cache paths above for every project and experiment subprocess.\n"
        "- Manuscript boundary: capability-vault paths, cache paths, local device "
        "IDs, daemon configuration, and Argus/Codex route names are agent-only "
        "runtime facts. Keep them out of rendered paper prose, captions, tables, "
        "and appendix text; use paper-facing evaluated system facts instead.\n"
    )


def _default_emnlp2026_profile() -> str:
    return """## Research profile: EMNLP 2026 TierHarness project

Long-horizon goal:
- Produce an EMNLP 2026 paper and supporting artifacts for TierHarness, the
  agent system currently implemented in this repository as argus-skill.
- Treat the desired paper claims as hypotheses until backed by reproducible
  evidence. Do not write or summarize any benchmark number as fact unless a raw
  artifact path proves it.

Paper hypotheses to test:
1. There is a hierarchy SLM -> LLM -> HUMAN where each tier is more capable but
   more expensive in money, latency, and human attention.
2. TierHarness uses this hierarchy through budgeted escalation: cheap model
   work first, LLM/reviewer repair only after objective verifier failure, and
   human attention only after autonomous repair is exhausted.
3. Current agent benchmarks under-report human interaction. The project must
   define and measure zero-touch success, human turns after assignment, active
   attention minutes, manual commands, intervention severity, and rescue rate.
4. Multi-agent structure is necessary where single-agent loops self-satisfy,
   ignore verifier evidence, or fail to repair hard tasks.
5. The planner can propose trivial objectives; prevent that by requiring every
   planned task to create or improve a concrete artifact under benchmarks/,
   experiments/, paper/, figures/, docs/, or tests/, with measurable acceptance.

Evidence and anti-fabrication rules:
- Every experimental claim must cite a local artifact path containing raw
  reward, model id, token counters, prompt/config hash, command, commit or
  working-tree manifest, started/ended timestamps, and logs.
- New research-paper missions must target frontier-domain gaps grounded in
  current literature and official benchmark evidence. Do not accept a
  synthetic proxy benchmark, local generated task set, hand-written oracle, or
  tiny custom scorer as the main proposed paper system when real benchmarks and
  GPU-scale training/adaptation are available.
- Paper-facing benchmark results must come from existing real benchmarks or
  official task/data releases with documented ground truth/evaluation. Synthetic
  or local tasks are smoke-only unless the operator explicitly changes the
  deliverable away from a submission-quality empirical paper.
- If evidence is missing, create a task to collect it; never fill gaps with
  estimates or optimistic prose.
- Final EMNLP completion is a separate `final_submission` scope. The project is
  not done until the L2 reviewer certifies the full pipeline checklist (research
  → submission) as `done` — every checklist item satisfied with concrete
  evidence — and that certified verdict is present in journal evidence.
- Passing a single stage's checklist, a pilot run, or an existing
  PDF is not enough for project_done. If the full checklist is not certified,
  queue bounded blocker tasks for the reported experiment, baseline, ablation,
  paper-contract, assurance, manifest, or submission-state gaps.
- For positive EMNLP paper objectives, final readiness requires a structured
  `paper/PAPER_QUALITY_CALIBRATION.json.paper_contribution` claim in the
  research.md form: "We propose X. We show X improves Y by Z because W." The
  proposed artifact/protocol must beat the strongest nontrivial baseline on the
  declared primary metric and on any held-out/public-validation split, with a
  local statistical-support artifact. Do not let Reflexion or another baseline
  winning over a trivial direct/no-skill baseline stand in for the proposed
  contribution winning.
- If experiments reject the method-positive thesis, do not relabel the package
  as a negative-result paper and declare success. Queue bounded repair or pivot
  tasks for the method, benchmark, metric, or objective unless the operator has
  explicitly requested a negative-result paper.
- Use `scope: bounded` for intermediate missions even when they mention EMNLP;
  reserve `scope: final_submission` for the single project-final readiness proof.

Autonomy and background-experiment rules:
- Long experiments must be launched as background jobs with a unique run_id.
  Write experiments/<run_id>/manifest.json, pid, stdout.log, stderr.log, and a
  status file before returning from the mission.
- Any experiment with more than 5 model/API calls or expected runtime above
  roughly 60 seconds must implement the Live Experiment Protocol:
  progress.jsonl, status.json, per-trial stdout progress, flush/fsync after each
  trial, STOP-file cancellation, and early-stop invariant checks.
- Do not block a mission waiting for a long experiment if independent paper,
  analysis, plotting, or user-study work is available. Record how to resume.
- Later missions should inspect experiments/*/pid and status files, collect
  completed results, then summarize them into reproducible tables.
- While an experiment is running, keep accepting operator guidance and continue
  independent work. If the user says the design is wrong, cancel via STOP/PID
  rather than letting the run spend the full budget.
- If API credentials are already present in the process environment, use them;
  do not ask the human to paste model keys. If credentials are missing, record a
  blocked status with the exact env var needed.

Self-architecture rules:
- The agent may modify its own harness, daemon, reviewer, critic, planner,
  benchmark, or tool architecture when the current architecture measurably
  prevents progress on experiments, evidence collection, paper writing, figure
  generation, or user-study design.
- Self-architecture changes must be driven by observed bottlenecks (e.g. task
  execution keeps stalling, reviewer/critic accepts the wrong evidence, long
  experiments block unrelated work, missing tools prevent paper artifacts).
  Cosmetic refactors, renames, or generic cleanup are invalid.
- Every self-architecture mission must include acceptance criteria and run
  targeted tests or a smoke scenario proving the blocked class of tasks now
  works. Daemon/runtime code changes only take effect once the operator
  restarts the daemon at a clean mission boundary — the running process keeps
  the previously-imported architecture until then. Land the change with passing
  tests and record that a restart is required; do not assume an automatic
  handoff will swap in the new code mid-flight.

Planning discipline:
- Prefer high-impact tasks in this order: fix reward/cost measurement bugs,
  launch reproducible benchmark runs, analyze failures, design user-study
  metrics/protocol, generate figures/tables, draft paper sections.
- A valid planner objective must include acceptance criteria and a command or
  artifact path that proves completion.
- Avoid vanity work: renames, comment polish, and generic "improve docs" tasks
  are invalid unless they directly support the paper or experiment protocol.
"""


_AAAI2026_FORMAT_ADDENDUM = """

AAAI 2026 format rules (override the venue defaults above):
- Use the official AAAI Press style (aaai2026.sty + aaai2026.bst), two-column,
  with `\\documentclass[letterpaper]{article}` + `\\usepackage[submission]{aaai2026}`
  (camera-ready uses `\\usepackage{aaai2026}`), the mandatory `\\pdfinfo` block,
  and Times font. Do NOT use acl.sty / acl-style-files.
- Body is 7 pages of technical content; References and the Reproducibility
  Checklist go on additional, uncounted pages (Conclusion by page 7, References
  on page 8 or later, no cap after the body).
- AAAI has no mandatory Limitations or Ethics sections; a Reproducibility
  Checklist IS required, placed after the References.
- Never emit `\\bibliographystyle` — aaai2026.sty sets it and a manual command
  errors. Do not load hyperref or navigator, and never use `\\nocopyright`.
- The anonymous author block renders as "Anonymous submission" via the
  `[submission]` option; AAAI has no official abstract word limit.
"""


def _default_aaai2026_profile() -> str:
    base = (
        _default_emnlp2026_profile()
        .replace("EMNLP 2026 TierHarness project", "AAAI 2026 TierHarness project")
        .replace("Produce an EMNLP 2026 paper", "Produce an AAAI 2026 paper")
        .replace("even when they mention EMNLP", "even when they mention AAAI")
    )
    return base + _AAAI2026_FORMAT_ADDENDUM


def _default_nanochat_profile() -> str:
    return """## Research profile: nanochat autoresearch (val-bpb minimization)

Long-horizon goal:
- Win a head-to-head automated-research contest: argus-skill versus Recursive's
  automated-research system. The single judged question is whose generated
  `solution.py` trains a small language model that reaches a LOWER mean
  validation bits-per-byte (val bpb) under one fixed, shared protocol.
- val bpb is the average number of bits needed to encode each byte of held-out
  text. It is a tokenizer-independent quality metric, so lower is strictly
  better. Treat every reported bpb as a hypothesis until the verifier reproduces
  it; never write a self-reported number into a result table as fact.

Research target and metric:
- Minimize validation bits-per-byte of a small LLM pretrained from scratch under
  a FIXED wall-clock budget. The whole game is squeezing the best held-out
  language-modeling quality out of a tightly bounded training run, not building
  the largest possible model.
- The primary metric is the MEAN val bpb across N random seeds. Recursive used
  N=10; while iterating, use N=3-5 for fast signal and re-run a larger N for any
  number that goes into a final comparison. A recipe that only wins on one lucky
  seed has not won.

Fixed scaffold and harness (do not modify):
- The contest runs on a GPU node under
  /scratch/recursive/nanochat_autoresearch. A shared harness `lib.py` provides
  the tokenizer, the dataloader, and `evaluate_bpb`, which scores held-out
  text on shard_06542 with TIME_BUDGET=300s. Every candidate under
  solutions/<name>.py imports this shared library.
- The agent's deliverable is exactly ONE self-contained training script,
  solution.py, that imports the shared lib.py. The agent may change only the
  training recipe inside solution.py: model architecture, optimizer, schedule,
  data ordering, batching, precision, regularization, and any other choice that
  lives inside the training run.
- The agent may NOT touch lib.py, the evaluation code, the validation set, or the
  time budget. Editing the harness, the held-out shard, the scorer, or the budget
  is cheating and invalidates the run. If the harness appears to block a
  legitimate recipe, record the limitation; do not work around it by altering
  shared code.

Budget and runtime contract:
- 300 seconds of wall-clock per run on ONE A100. Every recipe must fit useful
  tokenizer setup, model construction, and as much effective training as
  possible inside that window, then stop cleanly and evaluate. Spending the
  budget on a model too large to converge, or leaving the budget unused, are both
  failures.
- The SEED environment variable selects the seed for a run. The script must print
  its result on a line containing "val_bpb:" so the harness and verifier can
  parse it. Evaluation is the MEAN of these val bpb values across the N seeds.

Hardware and execution rules:
- GPU access is via `ssh ds "<cmd>"`; the node is an 8xA100-40GB host named
  dashing-stork. The Python interpreter on the node is
  /opt/conda/envs/ptca/bin/python and the data is already wired at /data. Do not
  re-download weights or datasets; reuse the on-node data path.
- A100 is Ampere, not Hopper, so flash-attn-3 cannot run there. Either write
  solution.py against torch SDPA attention directly, or launch it through
  /scratch/run_with_shim.py <solution.py>, which transparently swaps an
  FA3 call for torch SDPA. Assume FA3 is unavailable and design attention
  accordingly.

Baseline to beat:
- The baseline is Recursive's released solutions, re-measured ON OUR harness and
  hardware rather than trusting their published figures. Their best released
  recipe is optimized_from_karpathy.py, published at 0.9109 val bpb on a B200;
  we re-measure it on our A100 harness and the number that matters is that
  re-measured mean val bpb.
- Success means the argus-skill solution.py achieves a lower mean val bpb than
  the re-measured optimized_from_karpathy.py baseline under the identical
  protocol (same N seeds, same 300s budget, same held-out validation). Beating
  the published B200 number while losing to the re-measured A100 baseline does
  not count.

Anti-cheat and reward rules:
- The ONLY reward that counts is the val bpb produced by the VERIFIER re-running
  the agent's solution.py under the identical protocol: N seeds, 300s budget,
  the held-out validation shard. The agent's self-reported "val_bpb:" line is
  never the reward; it is only a hint to be confirmed.
- Any gap between a self-reported number and the verifier's number is resolved in
  favor of the verifier. Do not tune against the held-out validation set, do not
  special-case shard_06542, and do not leak validation bytes into training.
  Recipes that inspect, memorize, or otherwise contaminate the val set are
  disqualified even if they print a low number.
- Treat the held-out split as untouchable: select hyperparameters using only
  training-time signal or seeds/shards you are allowed to train on, then let the
  verifier reveal the real held-out result.

Evidence and anti-fabrication rules:
- Every bpb claim must cite a local artifact: the exact solution.py used, the
  seeds, the per-seed val bpb values, the mean, the command, timestamps, and the
  verifier log. A mean with no per-seed artifacts behind it is not evidence.
- When comparing against the baseline, both the argus-skill solution.py and the
  re-measured optimized_from_karpathy.py must be scored by the same verifier run
  configuration; never compare your re-measured number against their published
  number.
- If a recipe fails to fit the budget, diverges, or underperforms the baseline,
  record the negative result honestly and queue a bounded next experiment. Do not
  relabel a loss as a win or fill gaps with optimistic prose.

Autonomy and background-experiment rules:
- Training runs are short (300s each) but the search over recipes and seeds is
  long. Launch sweeps as background jobs with a unique run_id and write
  experiments/<run_id>/manifest.json, pid, stdout.log, stderr.log, and a status
  file before returning from the mission.
- Any sweep with many runs must implement the Live Experiment Protocol:
  progress.jsonl, status.json, per-run progress, flush/fsync after each run,
  STOP-file cancellation, and early-stop checks so an obviously-bad recipe does
  not consume the whole sweep.
- Keep accepting operator guidance while a sweep runs; if the operator says a
  recipe direction is wrong, cancel via STOP/PID rather than spending the full
  budget. If GPU credentials or the node are unavailable, record a blocked status
  with the exact access needed instead of guessing a number.

Planning discipline:
- Prefer high-impact work in this order: reproduce and re-measure the baseline on
  our harness, fix any measurement/seed/parsing bug, propose one concrete recipe
  change with a hypothesis, run it across N seeds, then analyze why it won or
  lost before proposing the next change.
- A valid planner objective must name the specific recipe change, the hypothesis
  for why it lowers val bpb, the seeds, and the artifact path that will prove the
  result. Vanity edits, renames, and changes that touch lib.py or the eval are
  invalid.
"""


# Registry mapping a profile name to its built-in prose builder. Unknown names
# fall back to the generic "use ARGUS_SKILL_RESEARCH_PROFILE_PATH" message.
_PROFILE_REGISTRY = {
    _EMNLP2026_PROFILE: _default_emnlp2026_profile,
    _AAAI2026_PROFILE: _default_aaai2026_profile,
    _NANOCHAT_PROFILE: _default_nanochat_profile,
}


def load_research_profile(
    env: Mapping[str, str] | None = None,
) -> ResearchProfile | None:
    source = env if env is not None else os.environ
    path_text = _read_profile_file(_env_text(source, _PROFILE_PATH_ENV))
    if path_text:
        name = _env_text(source, _PROFILE_ENV) or "custom-file"
        return ResearchProfile(name=name, text=path_text, sha256=_sha256(path_text))

    name = _env_text(source, _PROFILE_ENV)
    if not name:
        return None
    builder = _PROFILE_REGISTRY.get(name)
    if builder is None:
        text = (
            "## Research profile\n"
            f"- Active profile: {name}\n"
            "- No built-in profile text is available for this name. Use "
            f"{_PROFILE_PATH_ENV} to provide project-specific instructions."
        )
        return ResearchProfile(name=name, text=text, sha256=_sha256(text))

    text = builder()
    return ResearchProfile(name=name, text=text, sha256=_sha256(text))


def render_research_profile_context(
    env: Mapping[str, str] | None = None,
) -> str:
    if env is None:
        ensure_research_api_environment()
    profile = load_research_profile(env)
    if profile is None:
        return ""
    source = env if env is not None else os.environ
    return (
        f"{profile.text.strip()}\n\n"
        f"{_capability_context(source)}\n"
        "Profile metadata:\n"
        f"- profile_name: {profile.name}\n"
        f"- profile_sha256: {profile.sha256}\n"
    )
