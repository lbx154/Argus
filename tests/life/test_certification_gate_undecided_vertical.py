"""An undecided project must not inherit research's certified gate.

``_effective_final_certification_gate`` AND-s the operator's configuration with
the *active vertical's* completion gate, so a ``math`` or ``speedrun`` mission —
which has no submission package to certify — can accept ``project_done``
straight from the run loop instead of waiting forever for a certification that
will never be issued.

It resolved that vertical with ``resolve_vertical``, whose contract is not what
the call site assumed. The code caught ``VerticalResolutionError`` and
documented the catch as "we treat 'no vertical yet' as 'gate not satisfied'…
it is NOT a silent default to research". ``resolve_vertical`` does not raise
for an undecided project. It logs a warning and returns ``research``, whose
completion gate *is* ``certified`` — so the branch was dead and the fallback it
was written to avoid was the one actually taken.

Nothing burned, because the wrong answer was masked twice: every production
caller passes ``self._artifact_root()`` (the session state root, which does
carry the Manager's decision), and ``config.final_certification_gate`` is
itself computed by ``_final_certification_for_project_root`` from a *persisted*
certified vertical, so it is already ``False`` for math and materials. Two
independent guards, and the predicate between them said the opposite of what
its own docstring claimed. These tests pin the claim directly, on the
predicate, so it stops depending on both.
"""

from __future__ import annotations

from types import SimpleNamespace

from argus.life.supervisor._planning_context import PlanningContextMixin


class _Harness(PlanningContextMixin):
    def __init__(self, root, *, gate: bool = True) -> None:
        self._root = root
        self.config = SimpleNamespace(final_certification_gate=gate)

    def _artifact_root(self):
        return self._root


def test_a_metric_vertical_stays_ungated(tmp_path, optimize_vertical) -> None:
    """A metric vertical is the shape that, gated, wedges forever."""
    from argus.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, optimize_vertical)

    assert _Harness(tmp_path)._effective_final_certification_gate(tmp_path) is False
