#!/usr/bin/env bash
# Install Method-Figure as a Claude Code skill.
#
#   ./install.sh              -> ~/.claude/skills/method-figure   (all projects)
#   ./install.sh --project    -> ./.claude/skills/method-figure   (this repo only)
#
# The installed skill id stays lowercase (method-figure) because Claude Code
# skill ids are lowercase-kebab; only the project's display name is Method-Figure.
#
# The skill is copied, not symlinked, so pulling this repo later will not
# silently change a skill you are relying on.  Re-run to update.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="method-figure"

if [[ "${1:-}" == "--project" ]]; then
  DEST="$(pwd)/.claude/skills/$NAME"
else
  DEST="$HOME/.claude/skills/$NAME"
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found. Method-Figure needs Python 3.8 or newer." >&2
  exit 1
fi

PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
echo "python3 $PYV"

mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
mkdir -p "$DEST"

# Copy everything the skill needs at run time; leave repo furniture behind.
for item in SKILL.md AGENTS.md manifest.yaml README.md README.zh-CN.md LICENSE \
            CHANGELOG.md static references scripts templates examples assets \
            agents evals tests; do
  [[ -e "$SRC/$item" ]] && cp -R "$SRC/$item" "$DEST/"
done
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "installed to $DEST"

echo
echo "verifying..."
python3 "$DEST/scripts/render.py" "$DEST/templates/pipeline-4stage.yaml" \
        -o "$DEST/.selftest" -f svg >/dev/null
rm -f "$DEST/.selftest.svg"
echo "  render OK"
python3 "$DEST/scripts/validate.py" "$DEST/templates/pipeline-4stage.yaml" >/dev/null
echo "  audit OK"
if python3 "$DEST/tests/test_engine.py" >/dev/null 2>&1; then
  echo "  self-test OK"
else
  echo "  self-test FAILED -- run python3 $DEST/tests/test_engine.py to see why"
fi

echo
echo "Optional converters (for .pdf / .png / .tiff / .emf):"
for tool in inkscape soffice rsvg-convert magick pdftoppm; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "  found    $tool"
  else
    echo "  missing  $tool"
  fi
done
echo
echo "  .svg, .vsdx and .pptx need none of these and always work."
echo "  For high-resolution PNG install poppler-utils (pdftoppm) or ImageMagick,"
echo "  and one of Inkscape / LibreOffice / librsvg for SVG->PDF."
echo
echo "Done. In Claude Code, ask for a framework figure and the skill will load,"
echo "or invoke it by name with /method-figure."
