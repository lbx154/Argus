"""Package an allowlisted HTML result and its explicitly referenced static assets.

The API returns inert JSON; executable content stays in an opaque-origin iframe.
No asset URL exposes the project directory or carries the operator's credentials.
"""
from __future__ import annotations

import base64
import mimetypes
import re
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .artifacts import safe_artifact_path

MAX_FILES = 80
MAX_BYTES = 12 * 1024 * 1024
STATIC_SUFFIXES = {'.css', '.js', '.mjs', '.png', '.jpg', '.jpeg', '.gif', '.webp',
                   '.svg', '.ico', '.avif', '.woff', '.woff2', '.ttf', '.otf', '.mp3',
                   '.wav', '.ogg', '.mp4', '.webm'}
CSS_URL = re.compile(r'url\(\s*([\'"]?)([^)\'"\s]+)\1\s*\)', re.I)
CSS_IMPORT = re.compile(r'(@import\s+)([\'"])([^\'"]+)\2', re.I)
# Static module specifiers, including re-exports and literal dynamic imports.
JS_IMPORT = re.compile(r'((?:\b(?:import|export)\s+[^;\n]*?\bfrom\s*|\bimport\s*\(?\s*))([\'"])(\.{1,2}/[^\'"]+)\2')


class HtmlPackage(HTMLParser):
    def __init__(self, entry: Path):
        super().__init__(convert_charrefs=False)
        self.root = entry.parent.resolve()
        self.entry = entry
        self.files: dict[str, bytes] = {}
        self.urls: dict[str, str] = {}
        self.visiting: set[str] = set()
        self.warnings: list[str] = []
        self.output: list[str] = []
        self.total = 0
        self.rendered_bytes = 0
        self.in_style = False
        self.in_module = False

    def emit(self, text: str) -> None:
        self.rendered_bytes += len(text.encode('utf-8'))
        if self.rendered_bytes > MAX_BYTES * 3:
            raise ValueError('Expanded HTML result exceeds the preview size limit')
        self.output.append(text)

    def read(self, path: Path) -> bytes:
        name = path.relative_to(self.root).as_posix()
        if name in self.files:
            return self.files[name]
        if len(self.files) >= MAX_FILES or path.stat().st_size + self.total > MAX_BYTES:
            raise ValueError('HTML result exceeds the preview size limit')
        with path.open('rb') as handle:
            data = handle.read(MAX_BYTES - self.total + 1)
        if self.total + len(data) > MAX_BYTES:
            raise ValueError('HTML result exceeds the preview size limit')
        self.total += len(data)
        self.files[name] = data
        return data

    def asset(self, url: str, parent: Path) -> str:
        parts = urlsplit(url.strip())
        if parts.scheme or parts.netloc or not parts.path:
            return url
        try:
            # Root-relative paths mean the delivered website root, never Argus.
            raw = unquote(parts.path).replace('\\', '/')
            candidate = (self.root / raw.lstrip('/') if raw.startswith('/') else parent / raw).resolve()
            relative = candidate.relative_to(self.root).as_posix()
            if any(part.startswith('.') for part in Path(relative).parts):
                raise ValueError('private path')
            safe = safe_artifact_path(self.root, relative, allowed_suffixes=frozenset(STATIC_SUFFIXES))
            if not safe or candidate.suffix.lower() not in STATIC_SUFFIXES or not candidate.is_file():
                raise ValueError('unsupported asset')
            if relative in self.urls:
                return self.urls[relative] + (('#' + parts.fragment) if parts.fragment else '')
            if relative in self.visiting:
                raise ValueError('cyclic asset reference')
            self.visiting.add(relative)
            try:
                data = self.read(candidate)
                suffix = candidate.suffix.lower()
                if suffix == '.css':
                    data = self.css(data.decode('utf-8', errors='replace'), candidate.parent).encode()
                elif suffix in {'.js', '.mjs'}:
                    data = self.module(data.decode('utf-8', errors='replace'), candidate.parent).encode()
                if len(data) > MAX_BYTES * 2:
                    raise ValueError('Expanded asset is too large')
                mime = {'.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css'}.get(suffix) or mimetypes.guess_type(candidate.name)[0] or 'application/octet-stream'
                result = f'data:{mime};base64,' + base64.b64encode(data).decode()
                self.urls[relative] = result
                return result + (('#' + parts.fragment) if parts.fragment else '')
            finally:
                self.visiting.discard(relative)
        except (OSError, RuntimeError, ValueError):
            warning = f'Could not include local resource: {parts.path[:200]}'
            if warning not in self.warnings:
                self.warnings.append(warning)
            # Do not let relative requests fall through to the authenticated app.
            return 'data:application/octet-stream;base64,'

    def css(self, text: str, parent: Path) -> str:
        text = CSS_URL.sub(lambda m: 'url("' + self.asset(m[2], parent) + '")', text)
        return CSS_IMPORT.sub(lambda m: m[1] + '"' + self.asset(m[3], parent) + '"', text)

    def stylesheet(self, url: str, parent: Path) -> str | None:
        """Return a local stylesheet as text so it can be inlined as a <style>
        block. A page's own stylesheet arrives this way rather than through a
        ``data:`` URL, which a strict host style policy would refuse; only a
        genuine, confined local ``.css`` file qualifies, everything else falls
        back to the ordinary asset handling."""
        parts = urlsplit(url.strip())
        if parts.scheme or parts.netloc or not parts.path:
            return None
        try:
            raw = unquote(parts.path).replace('\\', '/')
            candidate = (self.root / raw.lstrip('/') if raw.startswith('/') else parent / raw).resolve()
            relative = candidate.relative_to(self.root).as_posix()
            if any(part.startswith('.') for part in Path(relative).parts):
                return None
            safe = safe_artifact_path(self.root, relative, allowed_suffixes=frozenset(STATIC_SUFFIXES))
            if not safe or candidate.suffix.lower() != '.css' or not candidate.is_file():
                return None
            if relative in self.visiting:
                return None
            self.visiting.add(relative)
            try:
                return self.css(self.read(candidate).decode('utf-8', errors='replace'), candidate.parent)
            finally:
                self.visiting.discard(relative)
        except (OSError, RuntimeError, ValueError):
            warning = f'Could not include local resource: {parts.path[:200]}'
            if warning not in self.warnings:
                self.warnings.append(warning)
            return None

    def module(self, text: str, parent: Path) -> str:
        return JS_IMPORT.sub(lambda m: m[1] + m[2] + self.asset(m[3], parent) + m[2], text)

    def handle_starttag(self, tag, attrs):
        self.render_starttag(tag, attrs)

    def render_starttag(self, tag, attrs, *, self_closing=False):
        values = dict(attrs)
        if tag == 'base':
            return
        if tag == 'meta' and values.get('http-equiv', '').lower() == 'refresh':
            return
        if tag == 'link' and 'stylesheet' in (values.get('rel') or '').lower().split() and values.get('href'):
            inlined = self.stylesheet(values['href'], self.root)
            if inlined is not None:
                media = values.get('media')
                self.emit('<style' + (' media="' + escape(media, quote=True) + '"' if media else '') + '>' + inlined + '</style>')
                return
        self.in_style = (tag == 'style' and not self_closing) or self.in_style
        self.in_module = tag == 'script' and values.get('type') == 'module' and not self_closing or self.in_module
        rendered = []
        for key, value in attrs:
            if value is not None:
                if key == 'style':
                    value = self.css(value, self.root)
                elif key in {'src', 'poster'} or tag == 'link' and key == 'href':
                    value = self.asset(value, self.root)
                elif key == 'srcset' and not value.startswith('data:'):
                    value = ', '.join(self.asset(bits[0], self.root) + (' ' + bits[1] if len(bits) > 1 else '') for chunk in value.split(',') if (bits := chunk.strip().split(maxsplit=1)))
                # Transformed bytes no longer match the author's integrity hash.
                elif key == 'integrity':
                    continue
            rendered.append(' ' + key + ('' if value is None else '="' + escape(value, quote=True) + '"'))
        self.emit('<' + tag + ''.join(rendered) + (' />' if self_closing else '>'))

    def handle_startendtag(self, tag, attrs):
        # SVG paths, rects and filter primitives are not HTML void elements.
        # Losing their closing slash nests the rest of the city inside a shape.
        self.render_starttag(tag, attrs, self_closing=True)

    def handle_endtag(self, tag):
        if tag == 'base':
            return
        self.emit('</' + tag + '>')
        if tag == 'style':
            self.in_style = False
        if tag == 'script':
            self.in_module = False

    def handle_data(self, data):
        self.emit(self.css(data, self.root) if self.in_style else self.module(data, self.root) if self.in_module else data)

    def handle_entityref(self, name):
        self.emit('&' + name + ';')

    def handle_charref(self, name):
        self.emit('&#' + name + ';')

    def handle_decl(self, decl):
        self.emit('<!' + decl + '>')

    def handle_comment(self, data):
        self.emit('<!--' + data + '-->')

    def build(self) -> dict:
        self.feed(self.read(self.entry).decode('utf-8', errors='replace'))
        self.close()
        return {'html': ''.join(self.output), 'warnings': self.warnings, 'file_count': len(self.files)}
