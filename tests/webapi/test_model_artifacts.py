"""Robot/3D model results: the model file and the meshes it references."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi import server
from argus.webapi.artifacts import _model_asset_candidates

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "robot" / "adjustable_fivebar.urdf"
SID = "s-model0001"

MESH_URDF = """<?xml version="1.0"?>
<robot name="arm">
  <link name="base"><visual><geometry><mesh filename="../meshes/base.stl"/></geometry></visual></link>
  <link name="tool"><visual><geometry><mesh filename="package://arm_description/meshes/tool.dae"/></geometry></visual></link>
  <joint name="j" type="fixed"><parent link="base"/><child link="tool"/></joint>
</robot>
"""


@pytest.fixture()
def project(tmp_path: Path):
    life = tmp_path / "projects" / SID
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": time.time()}) + "\n", encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    robot = workspace / "robot"
    robot.mkdir(parents=True)
    shutil.copy(FIXTURE, robot / "adjustable_fivebar.urdf")
    (robot / "closure.json").write_text("{}", encoding="utf-8")
    description = workspace / "arm_description"
    (description / "urdf").mkdir(parents=True)
    (description / "meshes").mkdir()
    (description / "urdf" / "arm.urdf").write_text(MESH_URDF, encoding="utf-8")
    (description / "meshes" / "base.stl").write_bytes(b"solid base\nendsolid base\n")
    (description / "meshes" / "tool.dae").write_text("<COLLADA/>", encoding="utf-8")
    (description / "meshes" / "notes.txt").write_text("not a mesh", encoding="utf-8")
    (description / "meshes" / "secrets").mkdir()
    (description / "meshes" / "secrets" / "key.stl").write_bytes(b"solid x\n")
    (tmp_path / "outside.stl").write_bytes(b"solid outside\n")
    write_session_meta(tmp_path, SessionMeta(id=SID, cwd=str(life), workdir=str(workspace)))
    manifest = life / ".argus" / "live-view.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "version": 1, "title": "Robot", "reason": "",
        "paths": ["robot/adjustable_fivebar.urdf", "arm_description/urdf/arm.urdf"],
    }), encoding="utf-8")
    return tmp_path, workspace, TestClient(server.create_app(global_root=tmp_path))


def test_urdf_is_a_model_artifact_and_names_its_closure_file(project) -> None:
    _, _, client = project
    rows = client.get(f"/api/projects/{SID}/artifacts").json()["artifacts"]
    assert [row["path"] for row in rows] == ["robot/adjustable_fivebar.urdf", "arm_description/urdf/arm.urdf"]
    info = client.get(f"/api/projects/{SID}/artifact", params={"path": "robot/adjustable_fivebar.urdf"}).json()
    assert info["kind"] == "model"
    assert info["mime"] == "application/xml"
    assert info["constraint_files"] == ["closure.json"]
    plain = client.get(f"/api/projects/{SID}/artifact", params={"path": "arm_description/urdf/arm.urdf"}).json()
    assert plain["constraint_files"] == []

    raw = client.get(f"/api/projects/{SID}/artifact/raw", params={"path": "robot/adjustable_fivebar.urdf"})
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("application/xml")
    assert raw.headers["content-security-policy"].startswith("sandbox")
    assert raw.headers["x-content-type-options"] == "nosniff"
    assert '<robot name="adjustable_fivebar">' in raw.text


def test_model_assets_resolve_relative_and_package_references(project) -> None:
    _, _, client = project
    url = f"/api/projects/{SID}/artifact/model-asset"
    stl = client.get(url, params={"path": "arm_description/urdf/arm.urdf", "ref": "../meshes/base.stl"})
    assert stl.status_code == 200
    assert stl.content.startswith(b"solid base")
    assert stl.headers["content-type"] == "model/stl"
    assert stl.headers["content-security-policy"].startswith("sandbox")
    dae = client.get(url, params={"path": "arm_description/urdf/arm.urdf",
                                  "ref": "package://arm_description/meshes/tool.dae"})
    assert dae.status_code == 200
    assert dae.headers["content-type"].startswith("model/vnd.collada+xml")


@pytest.mark.parametrize("model, ref", [
    ("arm_description/urdf/arm.urdf", "../meshes/notes.txt"),          # not a model asset
    ("arm_description/urdf/arm.urdf", "../meshes/secrets/key.stl"),    # sensitive directory
    ("arm_description/urdf/arm.urdf", "../../../outside.stl"),         # leaves the workspace
    ("arm_description/urdf/arm.urdf", "/etc/passwd.stl"),              # absolute
    ("arm_description/urdf/arm.urdf", "file:///tmp/outside.stl"),      # other scheme
    ("arm_description/urdf/arm.urdf", "package://arm_description/../../outside.stl"),
    ("arm_description/urdf/arm.urdf", "../meshes/missing.stl"),
    ("arm_description/meshes/base.stl", "tool.dae"),                   # model not registered
    ("robot/closure.json", "adjustable_fivebar.urdf"),                 # not a model
])
def test_model_assets_refuse_anything_outside_the_allowlist(project, model, ref) -> None:
    _, _, client = project
    response = client.get(f"/api/projects/{SID}/artifact/model-asset", params={"path": model, "ref": ref})
    assert response.status_code == 404


def test_model_asset_symlink_out_of_workspace_is_refused(project) -> None:
    root, workspace, client = project
    (workspace / "arm_description" / "meshes" / "link.stl").symlink_to(root / "outside.stl")
    response = client.get(f"/api/projects/{SID}/artifact/model-asset",
                          params={"path": "arm_description/urdf/arm.urdf", "ref": "../meshes/link.stl"})
    assert response.status_code == 404


def test_package_reference_candidates_prefer_the_named_package_directory() -> None:
    from pathlib import PurePosixPath

    candidates = [p.as_posix() for p in _model_asset_candidates(
        PurePosixPath("src/arm_description/urdf"), "package://arm_description/meshes/a.stl")]
    assert candidates[0] == "src/arm_description/meshes/a.stl"
    assert "src/arm_description/urdf/arm_description/meshes/a.stl" in candidates
    assert "meshes/a.stl" in candidates
    assert _model_asset_candidates(PurePosixPath("robot"), "package://") == []
    assert _model_asset_candidates(PurePosixPath("robot"), "../../x.stl") == []


def test_a_completion_summary_naming_models_registers_them(tmp_path: Path) -> None:
    from argus.life.delivery import referenced_delivery_paths

    for name in ("robot/arm.urdf", "meshes/arm.dae", "scene/arm.gltf", "scene/arm.glb", "meshes/a.stl"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x", encoding="utf-8")
    summary = "Built robot/arm.urdf with meshes/arm.dae; also scene/arm.gltf, scene/arm.glb and meshes/a.stl."
    assert referenced_delivery_paths(tmp_path, [summary]) == [
        "robot/arm.urdf", "meshes/arm.dae", "scene/arm.gltf", "scene/arm.glb", "meshes/a.stl",
    ]
