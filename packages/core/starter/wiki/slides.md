# Slide decks (.pptx)

- **Fonts**: a deck built on Linux with a Linux font (WenQuanYi Zen Hei, say) is re-flowed by PowerPoint on
  Windows or macOS, which lacks it. Embed the font (Noto Sans SC embedded whole worked, and is OFL-licensed), or
  use one both platforms have. Rendering with LibreOffice on the build machine does not show this: it has the font.
- **Rendering to check pages** without root: see the `local-pptx-render` skill (LibreOffice unpacked into a user
  folder, PPTX → PDF → PNG with pdftoppm). Look at every page for overflow, overlap and small type.
- **Charts**: native PPTX charts (with their embedded workbook) let the owner change numbers later; shapes drawn
  as bars do not. Check that every workbook cell holds its value (an empty cell showed 0 in the chart's cache but
  blank when edited).
- **For a talk**: one plain sentence per slide title; caveats once, on the slide about limits, not on every page;
  the spoken text in the speaker notes as something to read aloud. A deck that reads like an audit report
  (hedges, jargon, defences against objections no one raised) is correct and tiring.
