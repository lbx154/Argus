"""The research vertical routes figure work, and only figure work, to the figure model."""
from __future__ import annotations

from argus.verticals._base import load_vertical
from argus.verticals.research import task_routes

METHOD_FIGURE_TASK = (
    "Draw the method figure for the paper. Acceptance: paper/figures/decoupled_framework.pptx is the "
    "native source; figure_spec_scripts/pptx_export.py writes the .pdf and .png; figure_lint reports no defect."
)
DATA_FIGURE_TASK = (
    "Produce the results figures from results/*.json through the paper_charts helper; "
    "paper/figures/src/<stem>/facts.json sits beside each export."
)
WORDED_FIGURE_TASK = "Redraw the architecture figure so it shows the mechanism, not three boxes of bullets."
WRITING_TASK = (
    "Write the Results and Discussion sections of paper/main.tex against results/summary.json, include the "
    "existing graphics under paper/figures/ and compile paper/main.pdf without warnings."
)
EXPERIMENT_TASK = "Run the 32k retrieval evaluation with three seeds and write results/ruler.json."


def test_figure_tasks_take_the_figure_route() -> None:
    assert task_routes.model_route_for_task(METHOD_FIGURE_TASK) == "figure"
    assert task_routes.model_route_for_task(DATA_FIGURE_TASK) == "figure"
    assert task_routes.model_route_for_task(WORDED_FIGURE_TASK) == "figure"


def test_writing_and_experiment_tasks_keep_the_engineer_model() -> None:
    assert task_routes.model_route_for_task(WRITING_TASK) == ""
    assert task_routes.model_route_for_task(EXPERIMENT_TASK) == ""
    assert task_routes.model_route_for_task("") == ""


def test_the_host_finds_the_hook_on_the_loaded_vertical() -> None:
    module = load_vertical("research")
    assert module.model_route_for_task(METHOD_FIGURE_TASK) == "figure"
