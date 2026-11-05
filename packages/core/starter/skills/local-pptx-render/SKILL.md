---
name: local-pptx-render
description: Render an actual PPTX with LibreOffice on Ubuntu without root privileges, then inspect PNG pages.
---
Run `bash render.sh input.pptx output-directory`. Requires apt-get, dpkg-deb, Python 3 and pdftoppm. The script downloads the distribution's LibreOffice packages into a private temp root, fixes relocated absolute symlinks and bootstrap paths, copies the hidden registry payload normally installed by package hooks, and renders PPTX to PDF to PNG. Inspect every PNG with an image viewer; a contact sheet alone misses bad line breaks. Retain the PPTX, previews, source builder and source-fidelity notes. Check speaker notes separately in OOXML.

Case caught: tsPPT base64 slides (2026-11-04). sudo was forbidden, apt lists empty, then unpacked LibreOffice failed with missing unorc and unspecified RuntimeException. Private apt lists, URE_BOOTSTRAP and `.registry` installation solved actual Office rendering. Visual inspection found orphaned Chinese title characters in slide 10; shortening labels fixed them.

For embedded-font deliveries, run `python3 inspect-embedded-fonts.py file.pptx` in this skill directory. It checks actual font relationships and EOT headers/payload sizes, rather than a font name alone. It does not certify target PowerPoint behavior. Install matching legally redistributable TTFs before LibreOffice preview and use `pdffonts output.pdf` to detect substitution; an installed-font render does not prove the viewer loaded embedded fonts. Keep installation TTFs/license alongside editable sources when target software may ignore embedding.

Case caught (2026-11-04, base64 revision): Python ElementTree serialized Content Types and package Relationships with `ns0:` prefixes; LibreOffice 24.2 then reported “source file could not be loaded” despite well-formed XML. Register each OPC namespace as the default immediately before serializing its part. EOT full regular/bold payloads can be retained while notes slide-number placeholders are removed. Check text/arrow intersections and Chinese orphaned characters again after changing fonts.
