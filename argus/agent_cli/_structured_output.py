"""Per-call native JSON Schema transport; no process-wide provider settings."""
from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

PI_OUTPUT_SCHEMA_ENV = "ARGUS_PI_OUTPUT_SCHEMA"
# Generated from packages/runtime/src/piOutputSchemaExtension.ts and shipped
# with Python wheels; provider startup never requires a TypeScript build.
PI_OUTPUT_SCHEMA_EXTENSION = Path(__file__).with_name("pi_output_schema_extension.mjs")


def output_schema_json(backend: str, options) -> str | None:
    schema = getattr(options, "output_schema", None)
    if schema is None:
        return None
    if backend not in {"pi", "codex"}:
        raise ValueError(f"native output_schema is not supported by {backend}")
    if not options.disable_tools:
        raise ValueError("output_schema requires disable_tools=True")
    if not isinstance(schema, dict):
        raise ValueError("output_schema must be a JSON Schema object")
    # Preserve the caller's schema, including $defs/$ref. Tool-schema
    # strictification has different semantics and must not rewrite this input.
    return json.dumps(schema, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


@contextmanager
def structured_output_call(backend: str, options):
    """Codex needs a file; Pi receives the same JSON only in its child env."""
    encoded = output_schema_json(backend, options)
    if encoded is None or backend == "pi":
        yield options
        return
    with tempfile.TemporaryDirectory(prefix="argus-output-schema-", dir=options.working_dir) as directory:
        path = Path(directory) / "schema.json"
        path.write_text(encoded, encoding="utf-8")
        path.chmod(0o600)
        yield replace(options, _output_schema_path=str(path))
