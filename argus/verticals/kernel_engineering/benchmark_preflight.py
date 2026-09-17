"""Cheap benchmark-shape and memory preflight for kernel experiments.

A label such as ``L32_B1_T8K_D2K`` is evidence only when it describes the
actual tensors.  This tool catches stale labels before a large allocation or a
multi-minute compile, and reports a conservative minimum input footprint so a
Planner can choose a smaller diagnostic instead of blindly increasing timeout.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

_SHAPE_TOKEN_RE = re.compile(r"(?:^|_)(L|B|T|H|D)(\d+(?:\.\d+)?[KMG]?)(?=_|$)", re.IGNORECASE)
_SCALE = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}
_DTYPE_BYTES = {
    "float8": 1,
    "fp8": 1,
    "int8": 1,
    "float16": 2,
    "fp16": 2,
    "half": 2,
    "bfloat16": 2,
    "bf16": 2,
    "float32": 4,
    "fp32": 4,
}


@dataclass(frozen=True)
class BenchmarkPreflight:
    ok: bool
    shape_id: str
    declared: dict[str, int]
    actual: dict[str, int]
    mismatches: list[str]
    suggested_shape_id: str
    dtype: str
    minimum_input_bytes: int
    residual_bytes: int
    element_work: int
    warnings: list[str]


def _scaled_int(value: str) -> int:
    text = str(value or "").strip().upper()
    suffix = text[-1] if text and text[-1] in _SCALE and not text[-1].isdigit() else ""
    number = text[:-1] if suffix else text
    return int(float(number) * _SCALE[suffix])


def parse_shape_id(shape_id: str) -> dict[str, int]:
    return {
        name.upper(): _scaled_int(value)
        for name, value in _SHAPE_TOKEN_RE.findall(str(shape_id or ""))
    }


def _compact(value: int) -> str:
    for suffix, scale in (("G", 1024**3), ("M", 1024**2), ("K", 1024)):
        if value >= scale and value % scale == 0:
            return f"{value // scale}{suffix}"
    return str(value)


def canonical_shape_id(shape: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("L", "B", "T", "H", "D"):
        if key in shape:
            value = int(shape[key])
            parts.append(f"{key}{_compact(value) if key in {'T', 'D'} else value}")
    return "_".join(parts)


def preflight_shape(
    shape_id: str,
    shape: Mapping[str, Any],
    *,
    dtype: str = "bf16",
    gpu_memory_bytes: int | None = None,
) -> BenchmarkPreflight:
    declared = parse_shape_id(shape_id)
    actual = {
        key: int(shape[key])
        for key in ("L", "B", "T", "H", "D")
        if key in shape and shape[key] is not None
    }
    mismatches = [
        f"{key}: label={declared[key]}, actual={actual[key]}"
        for key in ("L", "B", "T", "H", "D")
        if key in declared and key in actual and declared[key] != actual[key]
    ]
    normalized_dtype = str(dtype or "bf16").strip().lower()
    itemsize = _DTYPE_BYTES.get(normalized_dtype)
    if itemsize is None:
        raise ValueError(
            f"unsupported dtype {dtype!r}; expected one of {', '.join(sorted(_DTYPE_BYTES))}"
        )
    required = {key: actual.get(key, 1) for key in ("L", "B", "T", "D")}
    residual_elements = required["L"] * required["B"] * required["T"] * required["D"]
    row_elements = required["B"] * required["T"] * required["D"]
    residual_bytes = residual_elements * itemsize
    # Residual sources + output gradient + query/rms weights. This deliberately
    # excludes framework temporaries and gradients, so it is a floor, not a
    # promise that the run fits.
    minimum_input_bytes = residual_bytes + row_elements * itemsize + 2 * required["D"] * itemsize
    warnings: list[str] = []
    if not declared:
        warnings.append("shape id contains no parseable L/B/T/H/D tokens")
    if gpu_memory_bytes and minimum_input_bytes > int(gpu_memory_bytes * 0.5):
        warnings.append(
            "minimum inputs exceed 50% of device memory; isolate one row and preflight allocation before timing"
        )
    if mismatches:
        warnings.append(
            "shape label does not describe the tensors; rename or correct the shape before collecting evidence"
        )
    return BenchmarkPreflight(
        ok=not mismatches and bool(declared),
        shape_id=str(shape_id),
        declared=declared,
        actual=actual,
        mismatches=mismatches,
        suggested_shape_id=canonical_shape_id({
            key: value
            for key, value in actual.items()
            if key != "H" or key in declared
        }),
        dtype=normalized_dtype,
        minimum_input_bytes=minimum_input_bytes,
        residual_bytes=residual_bytes,
        element_work=residual_elements,
        warnings=warnings,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shape-id", required=True)
    for key in ("L", "B", "T", "H", "D"):
        parser.add_argument(f"--{key}", type=int, required=key in {"L", "B", "T", "D"})
    parser.add_argument("--dtype", default="bf16")
    parser.add_argument("--gpu-memory-gib", type=float, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    shape = {key: getattr(args, key) for key in ("L", "B", "T", "H", "D") if getattr(args, key) is not None}
    gpu_bytes = int(args.gpu_memory_gib * 1024**3) if args.gpu_memory_gib else None
    result = preflight_shape(
        args.shape_id,
        shape,
        dtype=args.dtype,
        gpu_memory_bytes=gpu_bytes,
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
