# Argus technical report build.
# Standard TeX Live toolchain: pdflatex + bibtex, no latexmk dependency.

MAIN     := main
PDF      := argus-technical-report.pdf
LATEX    := pdflatex
BIBTEX   := bibtex
LFLAGS   := -interaction=nonstopmode -halt-on-error -file-line-error

.PHONY: all clean distclean

all: $(PDF)

$(PDF): $(MAIN).tex references.bib $(wildcard sections/*.tex) $(wildcard figures/*.tex)
	$(LATEX) $(LFLAGS) $(MAIN).tex
	$(BIBTEX) $(MAIN)
	$(LATEX) $(LFLAGS) $(MAIN).tex
	$(LATEX) $(LFLAGS) $(MAIN).tex
	cp -f $(MAIN).pdf $(PDF)

clean:
	rm -f $(MAIN).aux $(MAIN).bbl $(MAIN).blg $(MAIN).log \
	      $(MAIN).out $(MAIN).toc $(MAIN).pdf $(PDF) sections/*.aux

distclean: clean
	@true
