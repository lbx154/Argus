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
REVIEWFIG := figures/reviewer_mechanism.pdf
HORIZONFIG := figures/horizon_mountain.pdf
ERDOSFIG := figures/erdos_vertical_trace.pdf

.PHONY: all clean distclean

all: $(PDF)

$(TEASER): figures/build_argus_teaser.py evidence/website_results.json evidence/paper_inventory.json \
	evidence/swebench_pro/unified_experiment_summary.json
	$(PYTHON) figures/build_argus_teaser.py

$(SWEFIG): figures/build_swebench_evolution_pptx.py \
	evidence/swebench_pro/unified_experiment_summary.json \
	evidence/swebench_pro/argus_wave_efficiency.csv
	$(PYTHON) figures/build_swebench_evolution_pptx.py

$(REVIEWFIG): figures/build_reviewer_mechanism_pptx.py \
	evidence/swebench_pro/reviewer_mechanism_stats.json \
	evidence/swebench_pro/reviewer_interventions.csv
	$(PYTHON) figures/build_reviewer_mechanism_pptx.py

$(HORIZONFIG): figures/build_horizon_mountain_html.py figures/horizon_mountain.html \
	figures/horizon_mountain_illustrated_base.png figures/argus_explorer_binoculars.png \
	figures/horizon_base_composition_guide_v2.svg
	$(PYTHON) figures/build_horizon_mountain_html.py

$(ERDOSFIG): figures/erdos_agent_trace.html figures/render_html_figure.py \
	evidence/erdos_trace/trace.csv
	$(PYTHON) figures/render_html_figure.py figures/erdos_agent_trace.html $(ERDOSFIG)

$(PDF): $(MAIN).tex references.bib $(TEASER) $(SWEFIG) $(REVIEWFIG) $(HORIZONFIG) $(ERDOSFIG) $(wildcard sections/*.tex) \
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
