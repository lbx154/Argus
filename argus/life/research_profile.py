"""Research-profile context for long-running life-mode projects."""

from __future__ import annotations

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
_NANOCHAT_PROFILE = "nanochat-autoresearch"
_DEFAULT_TEXT_MODELS = "gpt-5.5,gpt-5.5"
_DEFAULT_IMAGE_MODEL = "gpt-image-2"
_SHARED_MODEL_CACHE_ROOT_ENV = "ARGUS_SKILL_SHARED_MODEL_CACHE_ROOT"


@dataclass(frozen=True)
class ResearchProfile:
    name: str
    text: str


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
        route.model for route in (engineer, reviewer) if route is not None and route.model
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
    vault_path = (
        grant.vault_path if grant is not None and grant.vault_path else default_vault_path(env)
    )
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
        "IDs, daemon configuration, and internal route names are agent-only "
        "runtime facts. Keep them out of rendered paper prose, captions, tables, "
        "and appendix text; use paper-facing evaluated system facts instead.\n"
    )


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
- Use the seed count and aggregation rule declared by the frozen scorer. A
  cheaper exploratory screen is provisional and cannot replace the official
  protocol; a recipe that only wins on one lucky seed has not won.

Fixed scaffold and harness (do not modify):
- The operator-provided benchmark workspace contains a shared `lib.py` with the
  tokenizer, dataloader, `evaluate_bpb`, held-out shard, and time budget. Read
  the workspace path and launch command from the current mission manifest or
  environment; never assume a host, mount point, SSH alias, GPU SKU, or Python
  interpreter from this profile.
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
- Read the time limit, device constraint, and setup/evaluation accounting from
  the current frozen harness. This profile does not define hardware inventory
  or a portable time budget. Every recipe must stop cleanly and evaluate inside
  the observed protocol; a model too large to converge or unused budget both
  require measured diagnosis.
- Read seed injection and result formatting from the active runner rather than
  assuming an environment variable or output line. The official scorer owns the
  aggregation across seeds.

Hardware and execution rules:
- Probe the actual device and software stack before choosing kernels. A profile
  name or historical result is not evidence that a particular GPU is available.
- Proceed only when the active benchmark runner verifies the hardware and time
  constraints declared by its frozen protocol. A different device or budget is
  a different benchmark, not a substitute result.
- Use only the operator-provided remote command, interpreter, data mount, and
  compatibility shim. If the detected GPU cannot run a candidate attention
  implementation, use a supported path such as torch SDPA or record an
  infrastructure blocker; do not invent benchmark results.

Baseline to beat:
- The baseline is the reference artifact named by the current benchmark,
  re-measured on the active frozen harness rather than trusting published
  figures. The number that matters is its re-measured val bpb under the same
  active protocol as the candidate.
- Success means the candidate achieves a lower official val bpb than that
  re-measured active baseline under the identical
  protocol (same seeds, budget, device constraints, and held-out validation).
  Beating a published number from different hardware while losing to the re-measured
  local baseline does not count.

Anti-cheat and reward rules:
- The ONLY reward that counts is the val bpb produced by the VERIFIER re-running
  the agent's solution.py under the identical frozen protocol: seeds, budget,
  hardware constraints, and held-out validation shard. The agent's self-reported "val_bpb:" line is
  never the reward; it is only a hint to be confirmed.
- Any gap between a self-reported number and the verifier's number is resolved in
  favor of the verifier. Do not tune against the held-out validation set, do not
  special-case the held-out shard, and do not leak validation bytes into training.
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
- Individual runs are bounded, but the search over recipes and seeds is
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
    _NANOCHAT_PROFILE: _default_nanochat_profile,
}


def load_research_profile(
    env: Mapping[str, str] | None = None,
) -> ResearchProfile | None:
    source = env if env is not None else os.environ
    path_text = _read_profile_file(_env_text(source, _PROFILE_PATH_ENV))
    if path_text:
        name = _env_text(source, _PROFILE_ENV) or "custom-file"
        return ResearchProfile(name=name, text=path_text)

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
        return ResearchProfile(name=name, text=text)

    text = builder()
    return ResearchProfile(name=name, text=text)


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
    )
