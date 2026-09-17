#!/usr/bin/env python3
"""One-command Argus Figure Studio v2 build orchestrator."""

from __future__ import annotations
import os

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

sys.dont_write_bytecode = True

from pptmaster_bridge import check_svg, ensure_project, export_pptx, roundtrip


PY = Path(os.environ.get("FIGURE_STUDIO_PYTHON", sys.executable))
PM = Path(os.environ.get("PPT_MASTER_HOME", Path.home() / ".argus-skill/tools/ppt-master/skills/ppt-master"))
STUDIO = Path(__file__).resolve().parent
SCHEMA = STUDIO / "figure_contract.schema.json"
RENDERER = STUDIO / "figma_figure_renderer.py"
GATE = STUDIO / "figure_quality_gate_v2.py"
DATA_RENDERER = STUDIO / "scenario_5_ablation_studio.py"


class BuildFailure(RuntimeError):
    def __init__(self, step: str, message: str, repro: str):
        super().__init__(message)
        self.step = step
        self.repro = repro


def _command_string(command: Iterable[object]) -> str:
    return " ".join(str(item) for item in command)


def _run_logged(command: Iterable[object], log: Path) -> subprocess.CompletedProcess[str]:
    argv = [str(item) for item in command]
    completed = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "$ " + _command_string(argv) + "\n"
        + f"exit_code: {completed.returncode}\n\n[stdout]\n{completed.stdout}\n[stderr]\n{completed.stderr}",
        encoding="utf-8",
    )
    if completed.returncode:
        raise RuntimeError(f"command exited {completed.returncode}; see {log}")
    return completed


def _structural_validate(contract: dict[str, Any]) -> None:
    figure_id = contract.get("figure_id", contract.get("id"))
    if not isinstance(figure_id, str) or not figure_id:
        raise ValueError("contract requires a non-empty figure_id (or id)")
    width = contract.get("final_width_mm")
    if not isinstance(width, (int, float)) or width <= 0:
        raise ValueError("contract requires a positive final_width_mm")
    if contract.get("kind") == "data-chart":
        return
    for key in ("nodes", "edges", "groups"):
        if key not in contract or not isinstance(contract[key], list):
            raise ValueError(f"diagram contract requires an array field: {key}")


def _load_validate_contract(path: Path) -> tuple[dict[str, Any], str]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read contract {path}: {exc}") from exc
    if not isinstance(contract, dict):
        raise ValueError("contract root must be a JSON object")
    _structural_validate(contract)

    # The current shared schema covers native diagram contracts.  A data-chart
    # contract uses the explicit router kind and is structurally checked when
    # that schema revision does not yet define the kind property.
    schema_mode = "structural"
    if SCHEMA.is_file():
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        schema_supports_data = "kind" in schema.get("properties", {})
        if contract.get("kind") != "data-chart" or schema_supports_data:
            try:
                import jsonschema
            except ImportError as exc:
                raise RuntimeError(f"jsonschema is required to validate {SCHEMA}") from exc
            jsonschema.validate(contract, schema)
            schema_mode = str(SCHEMA)
        else:
            schema_mode = "structural (diagram schema does not define data-chart kind)"
    return contract, schema_mode


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pptmaster_revision() -> str:
    completed = subprocess.run(
        ["git", "-C", str(PM), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else f"unavailable: {completed.stderr.strip()}"


def _write_receipt(
    quality: Path,
    *,
    figure_id: str,
    route: str,
    contract_source: Path,
    artifacts: dict[str, Path],
    steps: list[dict[str, Any]],
    schema_mode: str | None,
    gate_summary: dict[str, Any] | None,
) -> None:
    receipt = {
        "receipt_version": "2.0",
        "figure_id": figure_id,
        "route": route,
        "contract_source": str(contract_source.resolve()),
        "validation": schema_mode,
        "artifacts": {
            name: {"path": str(path), "sha256": _sha256(path)}
            for name, path in artifacts.items()
        },
        "steps": steps,
        "gate_summary": gate_summary,
        "tool_revisions": {
            "ppt_master_git": _pptmaster_revision(),
            "python": sys.version.split()[0],
        },
    }
    quality.mkdir(parents=True, exist_ok=True)
    (quality / "build_receipt.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _copy_finals(artifacts: dict[str, Path], copy_to: Path | None) -> None:
    if copy_to is None:
        return
    copy_to.mkdir(parents=True, exist_ok=True)
    for kind in ("svg", "png", "pptx", "pdf"):
        source = artifacts.get(kind)
        if source is not None and source.is_file():
            shutil.copy2(source, copy_to / source.name)


def build_one(
    contract_path: Path,
    *,
    out_root: Path,
    prebuilt_svg: Path | None = None,
    copy_to: Path | None = None,
) -> dict[str, Any]:
    contract_path = contract_path.resolve()
    preliminary_id = contract_path.stem
    project = out_root.resolve() / preliminary_id
    quality = project / "quality"
    steps: list[dict[str, Any]] = []
    artifacts: dict[str, Path] = {"contract": project / "contract.json"}
    contract: dict[str, Any] = {}
    schema_mode: str | None = None
    gate_summary: dict[str, Any] | None = None
    route = "unknown"

    def step(name: str, repro: str, action: Callable[[], Any]) -> Any:
        nonlocal gate_summary
        started = time.perf_counter()
        record: dict[str, Any] = {"name": name, "status": "running", "repro": repro}
        steps.append(record)
        try:
            value = action()
        except Exception as exc:
            record.update(status="failed", duration_seconds=round(time.perf_counter() - started, 6), error=str(exc))
            for gate_path in (quality / "gate.json", quality / "gate_pre.json"):
                if not gate_path.is_file():
                    continue
                try:
                    gate_summary = json.loads(gate_path.read_text(encoding="utf-8")).get("summary")
                except (OSError, UnicodeError, json.JSONDecodeError):
                    pass
                break
            _write_receipt(
                quality,
                figure_id=str(contract.get("figure_id", preliminary_id)),
                route=route,
                contract_source=contract_path,
                artifacts=artifacts,
                steps=steps,
                schema_mode=schema_mode,
                gate_summary=gate_summary,
            )
            raise BuildFailure(name, str(exc), repro) from exc
        record.update(status="passed", duration_seconds=round(time.perf_counter() - started, 6))
        return value

    contract, schema_mode = step(
        "load_validate_contract",
        f"{PY} {Path(__file__).resolve()} build --contract {contract_path} --out-root {out_root}",
        lambda: _load_validate_contract(contract_path),
    )
    figure_id = str(contract.get("figure_id", contract.get("id", preliminary_id)))
    if figure_id != preliminary_id:
        project = out_root.resolve() / figure_id
        quality = project / "quality"
        artifacts = {"contract": project / "contract.json"}
    project.mkdir(parents=True, exist_ok=True)
    quality.mkdir(parents=True, exist_ok=True)
    shutil.copy2(contract_path, artifacts["contract"])
    route = "data-chart" if contract.get("kind") == "data-chart" else "ppt-master"

    final_svg = project / f"{figure_id}.svg"
    final_png = project / f"{figure_id}.png"
    final_pdf = project / f"{figure_id}.pdf"
    final_pptx = project / f"{figure_id}.pptx"
    artifacts.update(svg=final_svg, png=final_png, pdf=final_pdf)

    if route == "data-chart":
        command = [PY, DATA_RENDERER, "--output-dir", project, "--basename", figure_id]
        step(
            "render_data_chart",
            _command_string(command),
            lambda: _run_logged(command, quality / "render_data_chart.log"),
        )
        (project / "svg_output").mkdir(parents=True, exist_ok=True)
        shutil.copy2(final_svg, project / "svg_output/P01.svg")
        _copy_finals(artifacts, copy_to)
        steps.append({"name": "ppt_master_route", "status": "skipped", "reason": "contract kind is data-chart"})
    else:
        render_command = [PY, RENDERER, "render", contract_path, "--output", final_svg]
        if prebuilt_svg is not None:
            source = prebuilt_svg.resolve()
            step(
                "render_svg",
                f"cp {source} {final_svg}",
                lambda: shutil.copy2(source, final_svg),
            )
        else:
            if not RENDERER.is_file():
                raise BuildFailure(
                    "render_svg",
                    f"renderer is missing: {RENDERER}",
                    _command_string(render_command),
                )
            step(
                "render_svg",
                _command_string(render_command),
                lambda: _run_logged(render_command, quality / "render.log"),
            )

        step(
            "ensure_pptmaster_project",
            f"{PY} {STUDIO / 'pptmaster_bridge.py'} ensure-project {project} {figure_id} {contract['final_width_mm']}",
            lambda: ensure_project(project, figure_id, float(contract["final_width_mm"])),
        )
        shutil.copy2(final_svg, project / "svg_output/P01.svg")

        def run_checker() -> dict[str, Any]:
            report = check_svg(project / "svg_output/P01.svg")
            if report.get("errors"):
                raise RuntimeError(f"PPT Master reported {len(report['errors'])} error(s)")
            return report

        checker_report = step(
            "pptmaster_checker",
            f"{PY} {STUDIO / 'pptmaster_bridge.py'} check-svg {project / 'svg_output/P01.svg'}",
            run_checker,
        )

        pre_gate_command = [
            PY, GATE, "check", project / "svg_output/P01.svg", "--contract", artifacts["contract"],
            "--output", quality / "gate_pre.json",
        ]
        step(
            "gate_pre_export",
            _command_string(pre_gate_command),
            lambda: _run_logged(pre_gate_command, quality / "gate_pre.log"),
        )

        artifacts["pptx"] = final_pptx
        step(
            "export_pptx",
            f"{PY} {STUDIO / 'pptmaster_bridge.py'} export-pptx {project} {final_pptx}",
            lambda: export_pptx(project, final_pptx),
        )
        step(
            "pptx_roundtrip",
            f"{PY} {STUDIO / 'pptmaster_bridge.py'} roundtrip {final_pptx} {project / 'roundtrip'}",
            lambda: roundtrip(final_pptx, project / "roundtrip"),
        )
        final_gate_command = [
            PY, GATE, "check", project / "svg_output/P01.svg", "--contract", artifacts["contract"],
            "--pptx", final_pptx, "--output", quality / "gate.json",
        ]
        step(
            "gate_pptx_editability",
            _command_string(final_gate_command),
            lambda: _run_logged(final_gate_command, quality / "gate.log"),
        )
        gate_report = json.loads((quality / "gate.json").read_text(encoding="utf-8"))
        gate_summary = gate_report.get("summary")

        png_command = [PY, "-m", "cairosvg", final_svg, "-o", final_png, "-f", "png", "--output-width", "2560"]
        pdf_command = [PY, "-m", "cairosvg", final_svg, "-o", final_pdf, "-f", "pdf"]

        def exports() -> None:
            _run_logged(png_command, quality / "export_png.log")
            from PIL import Image

            with Image.open(final_png) as image:
                png_dimensions = {"width": image.width, "height": image.height}
            steps[-1]["png_dimensions"] = png_dimensions
            if png_dimensions != {"width": 2560, "height": 1440}:
                raise RuntimeError(f"PNG export has unexpected dimensions: {png_dimensions}")
            _run_logged(pdf_command, quality / "export_pdf.log")

        step("publication_exports", _command_string(png_command) + " && " + _command_string(pdf_command), exports)
        step(
            "copy_finals",
            f"copy final SVG/PNG/PPTX/PDF to {copy_to}" if copy_to else "copy disabled",
            lambda: _copy_finals(artifacts, copy_to),
        )

    _write_receipt(
        quality,
        figure_id=figure_id,
        route=route,
        contract_source=contract_path,
        artifacts=artifacts,
        steps=steps,
        schema_mode=schema_mode,
        gate_summary=gate_summary,
    )
    return {"figure_id": figure_id, "route": route, "project": str(project), "receipt": str(quality / "build_receipt.json")}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--contract", type=Path, required=True)
    build.add_argument("--svg", type=Path)
    build.add_argument("--out-root", type=Path, default=STUDIO / "out")
    build_all = commands.add_parser("build-all")
    build_all.add_argument("--contracts-dir", type=Path, default=STUDIO / "contracts")
    build_all.add_argument("--out-root", type=Path, default=STUDIO / "out")
    build_all.add_argument("--copy-to", type=Path, default=STUDIO)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    failures = 0
    results: list[dict[str, Any]] = []
    contracts = [args.contract] if args.command == "build" else sorted(args.contracts_dir.glob("*.json"))
    if not contracts:
        print("FAILED step discover_contracts: no contract JSON files found")
        return 1
    for contract in contracts:
        try:
            result = build_one(
                contract,
                out_root=args.out_root,
                prebuilt_svg=args.svg if args.command == "build" else None,
                copy_to=args.copy_to if args.command == "build-all" else None,
            )
        except BuildFailure as exc:
            failures += 1
            print(f"FAILED step {exc.step}: {exc}; repro: {exc.repro}")
        except Exception as exc:
            failures += 1
            print(f"FAILED step unexpected: {exc}; repro: {PY} {Path(__file__).resolve()} {args.command}")
        else:
            results.append(result)
            print(f"BUILT {result['figure_id']} route={result['route']} receipt={result['receipt']}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
