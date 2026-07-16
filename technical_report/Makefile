# Argus technical report build.
# Standard TeX Live toolchain: pdflatex + bibtex, no latexmk dependency.

MAIN     := main
PDF      := argus-technical-report.pdf
LATEX    := pdflatex
BIBTEX   := bibtex
PYTHON   := python3
LFLAGS   := -interaction=nonstopmode -halt-on-error -file-line-error
TEASER   := figures/argus_teaser.pdf

.PHONY: all clean distclean

all: $(PDF)

$(TEASER): figures/build_argus_teaser.py evidence/website_results.json evidence/paper_inventory.json
	$(PYTHON) figures/build_argus_teaser.py

$(PDF): $(MAIN).tex references.bib $(TEASER) $(wildcard sections/*.tex) \
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
