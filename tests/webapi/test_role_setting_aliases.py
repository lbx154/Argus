from __future__ import annotations

from argus_skill.webapi.mission_items import _CONFIG_ALIASES


def test_role_settings_do_not_alias_the_global_model() -> None:
    """Every per-role cockpit setting must reach its own env var.

    ``manager_model`` pointed at ``ARGUS_SKILL_MODEL`` from the initial public
    release. The cockpit field said Manager and the write landed on the global
    model, so setting a Manager model there silently retargeted every role and
    left the Manager itself unchanged. One operator ran a weak model as Manager
    for a week on the strength of a setting that had never applied, and the
    classification contract failures that followed read as provider trouble.

    Asserted as a property of the whole table rather than one key, because the
    defect is a copy-paste that any future role added by the same edit inherits.
    """
    assert _CONFIG_ALIASES["manager_model"] == "ARGUS_SKILL_MANAGER_MODEL"

    role_models = {
        key: env
        for key, env in _CONFIG_ALIASES.items()
        if key.endswith("_model") and key != "model"
    }
    assert role_models, "expected per-role model settings in the alias table"
    assert _CONFIG_ALIASES["model"] not in role_models.values()
    assert len(set(role_models.values())) == len(role_models)
