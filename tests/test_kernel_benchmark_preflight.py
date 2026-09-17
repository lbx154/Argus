from __future__ import annotations

from argus.verticals.kernel_engineering.benchmark_preflight import (
    main,
    parse_shape_id,
    preflight_shape,
)


def test_parse_shape_id_understands_binary_k_suffix() -> None:
    assert parse_shape_id("L8_B1_T32K_D2K") == {
        "L": 8,
        "B": 1,
        "T": 32768,
        "D": 2048,
    }


def test_preflight_catches_stale_fla_shape_label() -> None:
    result = preflight_shape(
        "L32_B1_T8K_D2K",
        {"L": 64, "B": 1, "T": 8192, "H": 1, "D": 8192},
        dtype="bf16",
    )

    assert result.ok is False
    assert result.mismatches == [
        "L: label=32, actual=64",
        "D: label=2048, actual=8192",
    ]
    assert result.suggested_shape_id == "L64_B1_T8K_D8K"
    # Matches the observed allocation floor: 8 GiB residuals plus one B*T*D
    # gradient and two D-vectors, before framework temporaries and gradients.
    assert result.residual_bytes == 8 * 1024**3
    assert result.minimum_input_bytes > result.residual_bytes


def test_valid_shape_reports_large_memory_warning() -> None:
    result = preflight_shape(
        "L64_B1_T8K_D8K",
        {"L": 64, "B": 1, "T": 8192, "D": 8192},
        dtype="bf16",
        gpu_memory_bytes=12 * 1024**3,
    )

    assert result.ok is True
    assert any("50%" in warning for warning in result.warnings)


def test_cli_rejects_mismatched_shape(capsys) -> None:
    rc = main([
        "--shape-id", "L32_B1_T8K_D2K",
        "--L", "64",
        "--B", "1",
        "--T", "8192",
        "--D", "8192",
    ])

    assert rc == 2
    assert '"ok": false' in capsys.readouterr().out
