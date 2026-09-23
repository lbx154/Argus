from __future__ import annotations

from argus.life.supervisor._helpers import _sanitize_planner_task_text


def test_sanitize_planner_task_text_removes_generic_legacy_deployment_paths() -> None:
    text = (
        "Run PYTHONPATH=/home/legacy-user/Argus "
        "/home/legacy-user/miniconda3/bin/python -m argus, read "
        "`/home/legacy-user/research.md`, inspect "
        "`/home/legacy-user/Argus`, then open "
        "`/root/old-research/skills/paper-illustration-image2/SKILL.md`."
    )

    sanitized = _sanitize_planner_task_text(text)

    assert "/home/legacy-user" not in sanitized
    assert "/root/old-research" not in sanitized
    assert '"${ARGUS_SKILL_PYTHON:-python}" -m argus' in sanitized
    assert "operator-provided research playbook" in sanitized
    assert "active Argus source/package" in sanitized
    assert "argus_builtin_skills/engineer/paper-illustration-image2.md" in sanitized


def test_sanitize_planner_task_text_rewrites_the_pre_rename_module_and_checkout() -> None:
    text = (
        "Run PYTHONPATH=/home/legacy-user/argus-skill "
        "/usr/bin/python3 -m argus_skill.tools.subagent status --task-id t1, "
        "then read `/home/legacy-user/research.md`."
    )

    sanitized = _sanitize_planner_task_text(text)

    assert '"${ARGUS_SKILL_PYTHON:-python}" -m argus.tools.subagent status --task-id t1' in sanitized
    assert "argus_skill" not in sanitized
    assert "/home/legacy-user" not in sanitized
    assert "operator-provided research playbook" in sanitized


def test_sanitize_planner_task_text_preserves_unrelated_paths() -> None:
    text = (
        "Analyze /home/alice/project/results.csv and "
        "/home/alice/project/research.md in /home/alice/project/Argus "
        "without launching Argus."
    )

    assert _sanitize_planner_task_text(text) == text


def test_sanitize_planner_task_text_does_not_replace_source_prefixes() -> None:
    text = (
        "Run PYTHONPATH=/home/legacy-user/Argus "
        "python -m argus, but preserve "
        "/home/legacy-user/Argus-backup/results.json."
    )

    sanitized = _sanitize_planner_task_text(text)

    assert "/home/legacy-user/Argus-backup/results.json" in sanitized
