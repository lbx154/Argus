# Argus technical report build.
# Standard TeX Live toolchain: pdflatex + bibtex, no latexmk dependency.

MAIN     := main
PDF      := argus-technical-report.pdf
LATEX    := pdflatex
BIBTEX   := bibtex
PYTHON   := python3
LFLAGS   := -interaction=nonstopmode -halt-on-error -file-line-error
TEASER   := figures/argus_teaser.pdf
SWEFIG   := figures/swebench_evolution.pdf
HORIZONFIG := figures/horizon_mountain.pdf
ERDOSFIG := figures/erdos_vertical_trace.pdf
PAPERCASEFIG := figures/paper_case_study.pdf
PAPERTRACEFIG := figures/paper_case_trajectory.pdf
ANIMEASSETS := $(wildcard figures/assets/anime/*.png)
PAPERTHUMBS := $(wildcard figures/assets/paper_thumbnails/*.png)

.PHONY: all clean distclean

all: $(PDF)

$(TEASER): figures/build_argus_teaser.py figures/argus_teaser.pptx \
	figures/argus_teaser.source.svg
	$(PYTHON) figures/build_argus_teaser.py

$(SWEFIG): figures/build_swebench_evolution_pptx.py \
	evidence/swebench_pro/unified_experiment_summary.json \
	evidence/swebench_pro/argus_wave_efficiency.csv \
	evidence/swebench_pro/reviewer_mechanism_stats.json $(ANIMEASSETS)
	$(PYTHON) figures/build_swebench_evolution_pptx.py

$(HORIZONFIG): figures/build_horizon_mountain_html.py figures/horizon_mountain.html \
	figures/horizon_mountain_illustrated_base.png figures/argus_explorer_binoculars.png \
	figures/horizon_base_composition_guide_v2.svg $(ANIMEASSETS)
	$(PYTHON) figures/build_horizon_mountain_html.py

$(ERDOSFIG): figures/erdos_agent_trace.html figures/render_html_figure.py \
	evidence/erdos_trace/trace.csv $(ANIMEASSETS)
	$(PYTHON) figures/render_html_figure.py figures/erdos_agent_trace.html $(ERDOSFIG)
	pdftocairo -svg $(ERDOSFIG) figures/erdos_vertical_trace.svg
	pdftoppm -singlefile -png -r 180 $(ERDOSFIG) figures/erdos_vertical_trace

$(PAPERCASEFIG) $(PAPERTRACEFIG) &: figures/build_paper_case_study.py \
	figures/render_html_figure.py \
	evidence/paper_case_study/paper_trajectory_summary.json \
	evidence/paper_case_study/stage_transitions.csv \
	evidence/paper_case_study/paper_scientific_findings.json \
	evidence/paper_case_study/mm_hallucination_trace.json $(ANIMEASSETS) $(PAPERTHUMBS)
	$(PYTHON) figures/build_paper_case_study.py
	$(PYTHON) figures/render_html_figure.py figures/paper_case_study.html $(PAPERCASEFIG)
	$(PYTHON) figures/render_html_figure.py figures/paper_case_trajectory.html $(PAPERTRACEFIG)
	pdftocairo -svg $(PAPERCASEFIG) figures/paper_case_study.svg
	pdftocairo -svg $(PAPERTRACEFIG) figures/paper_case_trajectory.svg
	pdftoppm -singlefile -png -r 180 $(PAPERCASEFIG) figures/paper_case_study
	pdftoppm -singlefile -png -r 180 $(PAPERTRACEFIG) figures/paper_case_trajectory

$(PDF): $(MAIN).tex references.bib $(TEASER) $(SWEFIG) $(HORIZONFIG) $(ERDOSFIG) $(PAPERCASEFIG) $(PAPERTRACEFIG) $(wildcard sections/*.tex) \
	$(wildcard figures/*.tex) $(wildcard figures/*.png) $(wildcard figures/*.pdf) \
	$(wildcard assets/*.png)
	$(LATEX) $(LFLAGS) $(MAIN).tex
	$(BIBTEX) $(MAIN)
	$(LATEX) $(LFLAGS) $(MAIN).tex
	$(LATEX) $(LFLAGS) $(MAIN).tex
	$(LATEX) $(LFLAGS) $(MAIN).tex
	cp -f $(MAIN).pdf $(PDF)

clean:
	rm -f $(MAIN).aux $(MAIN).bbl $(MAIN).blg $(MAIN).log \
	      $(MAIN).out $(MAIN).toc $(MAIN).pdf $(PDF) sections/*.aux

distclean: clean
	@true
