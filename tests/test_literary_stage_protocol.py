"""Loop 11: every real literary vertical conforms to the shared Stage Protocol,
and the protocol genuinely extracts each vertical's (different) stage flow.

The protocol is EXTRACTED from the five shipped verticals, not designed empty:
this test proves all five pass conformance, that extract_protocol reconstructs
their distinct flows (with next-chains and produced artifacts), and that a
malformed stage module is rejected.
"""
from __future__ import annotations

import importlib

import pytest

from argus_skill.literary.stage_protocol import (
    StageProtocolError,
    extract_protocol,
    validate_stage_module,
)

_VERTICAL_STAGES = {
    "fiction_writing": "argus_skill.verticals.fiction_writing.stages",
    "classical_poetry": "argus_skill.verticals.classical_poetry.stages",
    "modern_poetry": "argus_skill.verticals.modern_poetry.stages",
    "prose": "argus_skill.verticals.prose.stages",
    "literary_editor": "argus_skill.verticals.literary_editor.stages",
}


@pytest.mark.parametrize("mod_path", _VERTICAL_STAGES.values(),
                         ids=list(_VERTICAL_STAGES))
def test_each_vertical_conforms(mod_path):
    module = importlib.import_module(mod_path)
    validate_stage_module(module)  # raises if non-conforming


@pytest.mark.parametrize("mod_path", _VERTICAL_STAGES.values(),
                         ids=list(_VERTICAL_STAGES))
def test_extract_reconstructs_the_flow(mod_path):
    module = importlib.import_module(mod_path)
    specs = extract_protocol(module)
    order = list(module.STAGE_ORDER)
    assert list(specs) == order
    # next-chain matches STAGE_ORDER; last stage has no next
    for i, stage in enumerate(order):
        expected = order[i + 1] if i + 1 < len(order) else None
        assert specs[stage].next == expected
    # every stage records at least one produced/checked artifact (the gate targets)
    assert all(specs[s].produced_artifacts for s in order)
    # the flow keeps its own intake and a final stage — not one imposed order
    assert order[0] == "intake"


def test_flows_are_genuinely_different_across_verticals():
    fic = list(importlib.import_module(_VERTICAL_STAGES["fiction_writing"]).STAGE_ORDER)
    ed = list(importlib.import_module(_VERTICAL_STAGES["literary_editor"]).STAGE_ORDER)
    poe = list(importlib.import_module(_VERTICAL_STAGES["classical_poetry"]).STAGE_ORDER)
    assert fic != ed and fic != poe and ed != poe  # not a forced single order
    # the protocol still accepts all three despite different flows
    for p in (fic, ed, poe):
        assert "intake" in p


def test_extract_pulls_real_validations_and_inputs():
    poetry = importlib.import_module(_VERTICAL_STAGES["classical_poetry"])
    specs = extract_protocol(poetry)
    # the prosody_check stage's validation is the machine prosody gate
    assert any("prosody" in v for v in specs["prosody_check"].validations)
    # the review stage reads the draft/report as its inputs
    assert specs["review"].required_inputs


# --------------------------------------------------------------------------- #
# a malformed stage module is rejected
# --------------------------------------------------------------------------- #

class _Bad:
    """A stage module missing required attributes / with an inconsistent check."""
    STAGE_ORDER = ["a", "b"]
    completion_gate = "none"
    STAGE_CHECKS = {"a": [("d", "test -s x")], "ghost": [("d", "test -s y")]}
    REVIEWER_CHECKLISTS = {}
    def role_banner(self, role):  # noqa: D401
        return "x"


def test_malformed_module_rejected():
    bad = _Bad()
    with pytest.raises(StageProtocolError):
        validate_stage_module(bad)


def test_missing_attribute_rejected():
    class M:
        STAGE_ORDER = ["intake"]
        # missing completion_gate/STAGE_CHECKS/REVIEWER_CHECKLISTS/role_banner
    with pytest.raises(StageProtocolError, match="missing required attribute"):
        validate_stage_module(M())


def test_stage_without_gate_rejected():
    class M:
        STAGE_ORDER = ["intake", "finish"]
        completion_gate = "none"
        STAGE_CHECKS = {"intake": [("d", "test -s x")]}  # 'finish' ungated
        REVIEWER_CHECKLISTS = {}
        def role_banner(self, role):
            return "framing"
    with pytest.raises(StageProtocolError, match="no STAGE_CHECKS gate"):
        validate_stage_module(M())
