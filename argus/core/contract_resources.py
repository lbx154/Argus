"""Locate language-neutral contracts in a checkout or an installed wheel."""
from pathlib import Path


def contract_schema_path(name: str) -> Path:
    if Path(name).name != name or not name.endswith(".json"):
        raise ValueError("contract schema must be a JSON filename")
    package = Path(__file__).resolve().parents[1]
    installed = package / "_contracts" / name
    if installed.is_file():
        return installed
    return package.parent / "packages" / "contracts" / "schemas" / name
