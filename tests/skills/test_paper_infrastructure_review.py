from argus_skill.skills.paper_infrastructure_review import _review_prompt


def test_paper_infrastructure_prompt_delegates_env_device_leaks_to_reviewer() -> None:
    prompt = _review_prompt(
        source_text_by_path={
            "paper/main.tex": (
                "\\section{Experimental Setup}\n"
                "The evaluated SkillGuard implementation runs in a deterministic Python benchmark harness.\n"
            )
        },
        threshold=4.0,
    )

    for required in (
        "CUDA_VISIBLE_DEVICES",
        "HF_HOME",
        "TRANSFORMERS_CACHE",
        "/root/.cache",
        "single local GPU",
        "local evaluation",
        "local training configuration",
        "local runtime/environment",
        "local software-environment",
        "raw local runner commands or run identifiers",
        "run_mind2web_gpu.py",
        "mind2web-gpu-*",
        "--output-root experiments",
        "neutral replay command alias",
        "Argus/Codex daemon",
        "engineer/reviewer/critic/scientist route",
        "strict JSON only",
        "leak_free",
        "A PASS still requires at least three evidence_spans",
        "different inspected scopes",
        "research-method prose rather than local environment",
    ):
        assert required in prompt
