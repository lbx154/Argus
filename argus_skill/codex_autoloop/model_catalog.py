from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelPreset:
    name: str
    main_model: str
    main_reasoning_effort: str
    reviewer_model: str
    reviewer_reasoning_effort: str
    plan_model: str
    plan_reasoning_effort: str
    note: str


@dataclass(frozen=True)
class ModelEntry:
    model: str
    category: str
    note: str


MODEL_ENTRIES: list[ModelEntry] = [
    # GPT Series
    ModelEntry("gpt-5.4", "general", "Current strongest general model available in local Codex cache."),
    ModelEntry("gpt-5.3-codex", "codex", "Current strongest codex-optimized model in local cache."),
    ModelEntry("gpt-5.2-codex", "codex", "Previous codex-optimized model."),
    ModelEntry("gpt-5.1-codex", "codex", "Balanced codex model."),
    ModelEntry("gpt-5.1-codex-max", "codex", "Long-running high-cost coding model."),
    ModelEntry("gpt-5-codex", "codex", "Older codex-optimized model."),
    ModelEntry("gpt-5.1-codex-mini", "cheap", "Cheaper codex-focused model."),
    ModelEntry("gpt-5-codex-mini", "cheap", "Older cheaper codex-focused model."),
    # Claude Series
    ModelEntry("claude-sonnet-4-6", "general", "Claude Code high-quality balanced model."),
    ModelEntry("claude-opus-4-6", "general", "Claude Code highest-quality model."),
    ModelEntry("sonnet", "general", "Claude Code alias for the latest Sonnet model."),
    ModelEntry("opus", "general", "Claude Code alias for the latest Opus model."),
    # Qwen3 Series
    ModelEntry("qwen3-30b-a3b", "general", "Qwen3 30B A3B model with strong reasoning capabilities."),
    ModelEntry("qwen3-32b", "general", "Qwen3 32B general purpose model."),
    ModelEntry("qwen3-14b", "general", "Qwen3 14B general purpose model."),
    ModelEntry("qwen3-8b", "general", "Qwen3 8B general purpose model."),
    ModelEntry("qwen3-coder-32b", "codex", "Qwen3 32B code-optimized model."),
    ModelEntry("qwen3-coder-14b", "codex", "Qwen3 14B code-optimized model."),
    ModelEntry("qwen3-coder-7b", "cheap", "Qwen3 7B code-optimized model."),
    # Qwen2.5 Series
    ModelEntry("qwen2.5-max", "general", "Qwen2.5 Max - strongest Qwen2.5 model."),
    ModelEntry("qwen2.5-72b", "general", "Qwen2.5 72B general purpose model."),
    ModelEntry("qwen2.5-32b", "general", "Qwen2.5 32B general purpose model."),
    ModelEntry("qwen2.5-14b", "general", "Qwen2.5 14B general purpose model."),
    ModelEntry("qwen2.5-7b", "general", "Qwen2.5 7B general purpose model."),
    ModelEntry("qwen2.5-coder-32b", "codex", "Qwen2.5 32B code-optimized model."),
    ModelEntry("qwen2.5-coder-14b", "codex", "Qwen2.5 14B code-optimized model."),
    ModelEntry("qwen2.5-coder-7b", "cheap", "Qwen2.5 7B code-optimized model."),
    # Qwen2.5-VL Series (Vision)
    ModelEntry("qwen2.5-vl-72b", "general", "Qwen2.5-VL 72B vision model."),
    ModelEntry("qwen2.5-vl-32b", "general", "Qwen2.5-VL 32B vision model."),
    ModelEntry("qwen2.5-vl-7b", "general", "Qwen2.5-VL 7B vision model."),
    # Qwen3.5 Series
    ModelEntry("qwen3.5-72b", "general", "Qwen3.5 72B general purpose model."),
    ModelEntry("qwen3.5-32b", "general", "Qwen3.5 32B general purpose model."),
    ModelEntry("qwen3.5-14b", "general", "Qwen3.5 14B general purpose model."),
    ModelEntry("qwen3.5-8b", "general", "Qwen3.5 8B general purpose model."),
    ModelEntry("qwen3.5-coder-32b", "codex", "Qwen3.5 32B code-optimized model."),
    ModelEntry("qwen3.5-coder-14b", "codex", "Qwen3.5 14B code-optimized model."),
    ModelEntry("qwen3.5-coder-7b", "cheap", "Qwen3.5 7B code-optimized model."),
]


DEFAULT_MODEL_PRESET = "codex-xhigh"


MODEL_PRESETS: list[ModelPreset] = [
    ModelPreset(
        name="quality",
        main_model="gpt-5.4",
        main_reasoning_effort="high",
        reviewer_model="gpt-5.4",
        reviewer_reasoning_effort="high",
        plan_model="gpt-5.4",
        plan_reasoning_effort="high",
        note="Highest-quality default with high reasoning for both agents.",
    ),
    ModelPreset(
        name="copilot",
        main_model="gpt-5.4",
        main_reasoning_effort="high",
        reviewer_model="gpt-5.4",
        reviewer_reasoning_effort="high",
        plan_model="gpt-5.4",
        plan_reasoning_effort="high",
        note="Copilot proxy-friendly preset using GPT-5.4 across main, reviewer, and planner.",
    ),
    ModelPreset(
        name="claude-sonnet",
        main_model="claude-sonnet-4-6",
        main_reasoning_effort="high",
        reviewer_model="claude-sonnet-4-6",
        reviewer_reasoning_effort="high",
        plan_model="claude-sonnet-4-6",
        plan_reasoning_effort="high",
        note="Claude Code preset using Sonnet across main, reviewer, and planner.",
    ),
    ModelPreset(
        name="claude-opus",
        main_model="claude-opus-4-6",
        main_reasoning_effort="high",
        reviewer_model="claude-sonnet-4-6",
        reviewer_reasoning_effort="high",
        plan_model="claude-sonnet-4-6",
        plan_reasoning_effort="medium",
        note="Claude Code preset using Opus main and Sonnet secondary agents.",
    ),
    ModelPreset(
        name="codex52-xhigh",
        main_model="gpt-5.2-codex",
        main_reasoning_effort="xhigh",
        reviewer_model="gpt-5.2-codex",
        reviewer_reasoning_effort="xhigh",
        plan_model="gpt-5.2-codex",
        plan_reasoning_effort="xhigh",
        note="Codex 5.2 with maximum reasoning for both agents.",
    ),
    ModelPreset(
        name="quality-xhigh",
        main_model="gpt-5.4",
        main_reasoning_effort="xhigh",
        reviewer_model="gpt-5.4",
        reviewer_reasoning_effort="xhigh",
        plan_model="gpt-5.4",
        plan_reasoning_effort="xhigh",
        note="Highest-quality preset with maximum reasoning on both agents.",
    ),
    ModelPreset(
        name="balanced",
        main_model="gpt-5.3-codex",
        main_reasoning_effort="high",
        reviewer_model="gpt-5.1-codex",
        reviewer_reasoning_effort="medium",
        plan_model="gpt-5.1-codex",
        plan_reasoning_effort="medium",
        note="Strong coding quality with cheaper reviewer.",
    ),
    ModelPreset(
        name="codex-xhigh",
        main_model="gpt-5.3-codex",
        main_reasoning_effort="xhigh",
        reviewer_model="gpt-5.3-codex",
        reviewer_reasoning_effort="xhigh",
        plan_model="gpt-5.3-codex",
        plan_reasoning_effort="xhigh",
        note="Pure codex-focused preset with maximum reasoning on both agents.",
    ),
    ModelPreset(
        name="cheap",
        main_model="gpt-5.1-codex-mini",
        main_reasoning_effort="medium",
        reviewer_model="gpt-5-codex-mini",
        reviewer_reasoning_effort="low",
        plan_model="gpt-5-codex-mini",
        plan_reasoning_effort="low",
        note="Lower-cost pairing for long background loops.",
    ),
    ModelPreset(
        name="max",
        main_model="gpt-5.1-codex-max",
        main_reasoning_effort="xhigh",
        reviewer_model="gpt-5.3-codex",
        reviewer_reasoning_effort="high",
        plan_model="gpt-5.3-codex",
        plan_reasoning_effort="high",
        note="Most expensive long-horizon pairing.",
    ),
    # Qwen3 Series Presets
    ModelPreset(
        name="qwen3-quality",
        main_model="qwen3-30b-a3b",
        main_reasoning_effort="high",
        reviewer_model="qwen3-30b-a3b",
        reviewer_reasoning_effort="high",
        plan_model="qwen3-30b-a3b",
        plan_reasoning_effort="high",
        note="Qwen3 high-quality preset using 30B A3B model.",
    ),
    ModelPreset(
        name="qwen3-balanced",
        main_model="qwen3-32b",
        main_reasoning_effort="high",
        reviewer_model="qwen3-14b",
        reviewer_reasoning_effort="medium",
        plan_model="qwen3-14b",
        plan_reasoning_effort="medium",
        note="Qwen3 balanced preset with 32B main and 14B secondary.",
    ),
    ModelPreset(
        name="qwen3-coder",
        main_model="qwen3-coder-32b",
        main_reasoning_effort="high",
        reviewer_model="qwen3-coder-14b",
        reviewer_reasoning_effort="medium",
        plan_model="qwen3-coder-14b",
        plan_reasoning_effort="medium",
        note="Qwen3 code-optimized preset using coder variants.",
    ),
    ModelPreset(
        name="qwen3-cheap",
        main_model="qwen3-coder-7b",
        main_reasoning_effort="medium",
        reviewer_model="qwen3-8b",
        reviewer_reasoning_effort="low",
        plan_model="qwen3-8b",
        plan_reasoning_effort="low",
        note="Lower-cost Qwen3 pairing for budget-conscious usage.",
    ),
    # Qwen2.5 Series Presets
    ModelPreset(
        name="qwen25-quality",
        main_model="qwen2.5-max",
        main_reasoning_effort="high",
        reviewer_model="qwen2.5-max",
        reviewer_reasoning_effort="high",
        plan_model="qwen2.5-max",
        plan_reasoning_effort="high",
        note="Qwen2.5 highest-quality preset using Qwen2.5 Max.",
    ),
    ModelPreset(
        name="qwen25-balanced",
        main_model="qwen2.5-72b",
        main_reasoning_effort="high",
        reviewer_model="qwen2.5-32b",
        reviewer_reasoning_effort="medium",
        plan_model="qwen2.5-32b",
        plan_reasoning_effort="medium",
        note="Qwen2.5 balanced preset with 72B main and 32B secondary.",
    ),
    ModelPreset(
        name="qwen25-coder",
        main_model="qwen2.5-coder-32b",
        main_reasoning_effort="high",
        reviewer_model="qwen2.5-coder-14b",
        reviewer_reasoning_effort="medium",
        plan_model="qwen2.5-coder-14b",
        plan_reasoning_effort="medium",
        note="Qwen2.5 code-optimized preset using coder variants.",
    ),
    ModelPreset(
        name="qwen25-cheap",
        main_model="qwen2.5-coder-7b",
        main_reasoning_effort="medium",
        reviewer_model="qwen2.5-7b",
        reviewer_reasoning_effort="low",
        plan_model="qwen2.5-7b",
        plan_reasoning_effort="low",
        note="Lower-cost Qwen2.5 pairing for budget-conscious usage.",
    ),
    # Qwen3.5 Series Presets
    ModelPreset(
        name="qwen35-quality",
        main_model="qwen3.5-72b",
        main_reasoning_effort="high",
        reviewer_model="qwen3.5-32b",
        reviewer_reasoning_effort="high",
        plan_model="qwen3.5-32b",
        plan_reasoning_effort="high",
        note="Qwen3.5 high-quality preset using 72B main model.",
    ),
    ModelPreset(
        name="qwen35-balanced",
        main_model="qwen3.5-32b",
        main_reasoning_effort="high",
        reviewer_model="qwen3.5-14b",
        reviewer_reasoning_effort="medium",
        plan_model="qwen3.5-14b",
        plan_reasoning_effort="medium",
        note="Qwen3.5 balanced preset with 32B main and 14B secondary.",
    ),
    ModelPreset(
        name="qwen35-coder",
        main_model="qwen3.5-coder-32b",
        main_reasoning_effort="high",
        reviewer_model="qwen3.5-coder-14b",
        reviewer_reasoning_effort="medium",
        plan_model="qwen3.5-coder-14b",
        plan_reasoning_effort="medium",
        note="Qwen3.5 code-optimized preset using coder variants.",
    ),
    ModelPreset(
        name="qwen35-cheap",
        main_model="qwen3.5-coder-7b",
        main_reasoning_effort="medium",
        reviewer_model="qwen3.5-8b",
        reviewer_reasoning_effort="low",
        plan_model="qwen3.5-8b",
        plan_reasoning_effort="low",
        note="Lower-cost Qwen3.5 pairing for budget-conscious usage.",
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="argusbot-models",
        description="List supported model presets and common model names for ArgusBot.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args()

    if args.json:
        payload = {
            "models": [asdict(item) for item in MODEL_ENTRIES],
            "presets": [asdict(item) for item in MODEL_PRESETS],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print("Model presets:")
    for preset in MODEL_PRESETS:
        print(
            f"- {preset.name}: main={preset.main_model}/{preset.main_reasoning_effort}, "
            f"reviewer={preset.reviewer_model}/{preset.reviewer_reasoning_effort}, "
            f"plan={preset.plan_model}/{preset.plan_reasoning_effort} "
            f"({preset.note})"
        )
    print("")
    print("Common model names:")
    for item in MODEL_ENTRIES:
        print(f"- {item.model}: [{item.category}] {item.note}")


def get_preset(name: str) -> ModelPreset | None:
    normalized = name.strip().lower()
    for preset in MODEL_PRESETS:
        if preset.name == normalized:
            return preset
    return None


if __name__ == "__main__":
    main()
