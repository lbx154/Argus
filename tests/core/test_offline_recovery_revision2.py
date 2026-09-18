"""Synthetic regression controls for offline CAS and sealed receipt ambiguity."""

import copy
import json
import sqlite3

import pytest
from test_offline_recovery import assert_projection, encoded, snapshot
from test_offline_recovery import case as generated_case

from argus.core.incident_recovery import OfflineRecovery, digest
from argus.core.usage import UsageRecord, usage_recorded_event

case = generated_case


def interrupt(work, manifest, point):
    def crash(stage):
        if stage == point:
            raise OSError("synthetic interruption")

    with pytest.raises(OSError, match="synthetic interruption"):
        work.apply_to_copy(
            manifest["id"], expected_manifest_sha256=manifest["manifest_sha256"], fault=crash
        )


@pytest.mark.parametrize("phase", ["initial", "before_projection", "partial", "after_projection"])
@pytest.mark.parametrize("damage", ["missing", "changed"])
@pytest.mark.parametrize(
    "original", ["config.json", "cost-control.jsonl", "cost-control.json", "usage", "events"]
)
def test_every_original_cas_before_any_apply_mutation(case, phase, damage, original):
    work, spec, *_ = case
    manifest = work.prepare(spec)
    if phase != "initial":
        point = "after:" + manifest["order"][0] if phase == "partial" else phase
        interrupt(work, manifest, point)
    name = (
        f"projects/{spec['project_id']}/{original}.jsonl"
        if original in {"usage", "events"}
        else original
    )
    target = work.root / name
    if damage == "missing":
        target.unlink()
    else:
        target.write_bytes(b"synthetic unexpected replacement\n")
    before = snapshot(work.root)
    with pytest.raises(ValueError, match="source changed"):
        work.apply_to_copy(manifest["id"], expected_manifest_sha256=manifest["manifest_sha256"])
    assert snapshot(work.root) == before


@pytest.mark.parametrize("new_file_state", ["absent", "projected", "changed"])
def test_only_explicit_new_finalizer_may_be_absent(case, new_file_state):
    work, spec, *_ = case
    manifest = work.prepare(spec)
    name = manifest["order"][2]
    interrupt(work, manifest, "after:" + name)
    target = work.root / name
    if new_file_state == "absent":
        target.unlink()
    elif new_file_state == "changed":
        target.write_bytes(b"synthetic wrong finalizer\n")
    before = snapshot(work.root)
    if new_file_state == "changed":
        with pytest.raises(ValueError, match="source changed"):
            work.apply_to_copy(manifest["id"], expected_manifest_sha256=manifest["manifest_sha256"])
        assert snapshot(work.root) == before
    else:
        work.apply_to_copy(manifest["id"], expected_manifest_sha256=manifest["manifest_sha256"])
        assert_projection(*case)


@pytest.mark.parametrize(
    "variant",
    [
        "missing",
        "identical_duplicate",
        "conflicting_first",
        "conflicting_last",
        "session_conflict",
        "text_id",
        "float_id",
        "valid_no_constraint",
        "valid_unclaimed_duplicate",
    ],
)
def test_fresh_sealed_sqlite_requires_exact_unambiguous_claimed_id(case, tmp_path, variant):
    work, spec, state, originals, charge = case
    db = tmp_path / "receipt-variants.sqlite"
    db.write_bytes((work.root / "provider.db").read_bytes())
    with sqlite3.connect(db) as con:
        con.execute("ALTER TABLE assistant_usage_events RENAME TO saved")
        # Deliberately no affinity/uniqueness: exercise actual row identity, not
        # merely a trusted schema declaration or first-row ordering.
        columns = [row[1] for row in con.execute("PRAGMA table_info(saved)")]
        con.execute("CREATE TABLE assistant_usage_events (" + ",".join(columns) + ")")
        row = list(con.execute("SELECT * FROM saved").fetchone())
        if variant == "text_id":
            row[0] = str(row[0])
        elif variant == "float_id":
            row[0] = float(row[0])
        if variant != "missing":
            con.execute("INSERT INTO assistant_usage_events VALUES (?,?,?,?,?,?,?,?,?,?)", row)
        if variant in {
            "identical_duplicate",
            "conflicting_first",
            "conflicting_last",
            "session_conflict",
        }:
            con.execute("INSERT INTO assistant_usage_events VALUES (?,?,?,?,?,?,?,?,?,?)", row)
            if variant.startswith("conflicting"):
                which = 1 if variant == "conflicting_first" else 2
                con.execute(
                    "UPDATE assistant_usage_events SET total_nano_aiu=total_nano_aiu+100 WHERE rowid=?",
                    (which,),
                )
            if variant == "session_conflict":
                con.execute(
                    "UPDATE assistant_usage_events SET session_id='other-synthetic-session' WHERE rowid=2"
                )
        if variant == "valid_unclaimed_duplicate":
            row[0] += 1000
            con.executemany(
                "INSERT INTO assistant_usage_events VALUES (?,?,?,?,?,?,?,?,?,?)", [row, row]
            )
        con.execute("DROP TABLE saved")
    spec = copy.deepcopy(spec)
    spec["provider_evidence"]["sqlite_sha256"] = digest(db.read_bytes())
    parent = tmp_path / "fresh-receipt-copy"
    parent.mkdir(mode=0o700)
    with OfflineRecovery.create(parent, originals, db.read_bytes()) as fresh:
        before = snapshot(fresh.root)
        if not variant.startswith("valid_"):
            with pytest.raises(ValueError, match="provider receipt"):
                fresh.prepare(spec)
            assert snapshot(fresh.root) == before
        else:
            manifest = fresh.prepare(spec)
            fresh.apply_to_copy(
                manifest["id"], expected_manifest_sha256=manifest["manifest_sha256"]
            )
            assert_projection(fresh, spec, state, originals, charge)
            once = snapshot(fresh.root)
            assert fresh.apply_to_copy(
                manifest["id"], expected_manifest_sha256=manifest["manifest_sha256"]
            )["duplicate"]
            assert snapshot(fresh.root) == once


@pytest.mark.parametrize(
    "variant", ["repeated_model", "repeated_spec", "two_distinct", "wrong_order", "missing_claim"]
)
def test_consistent_receipt_set_and_no_double_charge(case, tmp_path, variant):
    work, spec, state, originals, charge = case
    spec, originals = copy.deepcopy(spec), originals.copy()
    usage_name = f"projects/{spec['project_id']}/usage.jsonl"
    events_name = f"projects/{spec['project_id']}/events.jsonl"
    prefix_length = spec["usage_cuts"][0]["length"]
    known = json.loads(originals[usage_name][prefix_length:])
    model = copy.deepcopy(known["model_usage"][0])
    old_event = encoded(usage_recorded_event(UsageRecord.from_jsonable(known)))
    db = tmp_path / "receipt-set.sqlite"
    db.write_bytes((work.root / "provider.db").read_bytes())
    if variant == "repeated_spec":
        spec["provider_evidence"]["ids"] *= 2
    else:
        if variant != "repeated_model":
            model["usage_event_id"] += 1
            with sqlite3.connect(db) as con:
                row = list(con.execute("SELECT * FROM assistant_usage_events").fetchone())
                row[0] = model["usage_event_id"]
                con.execute("INSERT INTO assistant_usage_events VALUES (?,?,?,?,?,?,?,?,?,?)", row)
            for field in [
                "input_tokens",
                "cached_input_tokens",
                "cache_write_tokens",
                "output_tokens",
                "reasoning_output_tokens",
                "total_nano_aiu",
                "cost_usd",
            ]:
                known[field] *= 2
            spec["provider_evidence"]["ids"].append(model["usage_event_id"])
            spec["provider_evidence"]["nano_aiu"] *= 2
            charge *= 2
        known["model_usage"].append(model)
        if variant == "wrong_order":
            spec["provider_evidence"]["ids"].reverse()
        elif variant == "missing_claim":
            spec["provider_evidence"]["ids"].pop()
    originals[usage_name] = originals[usage_name][:prefix_length] + encoded(known)
    new_event = encoded(usage_recorded_event(UsageRecord.from_jsonable(known)))
    originals[events_name] = originals[events_name].replace(old_event, new_event)
    spec["event_cuts"][1]["offset"] += len(new_event) - len(old_event)
    spec["originals"] = {name: digest(raw) for name, raw in originals.items()}
    spec["provider_evidence"]["sqlite_sha256"] = digest(db.read_bytes())
    parent = tmp_path / "fresh-set-copy"
    parent.mkdir(mode=0o700)
    with OfflineRecovery.create(parent, originals, db.read_bytes()) as fresh:
        before = snapshot(fresh.root)
        if variant != "two_distinct":
            with pytest.raises(ValueError):
                fresh.prepare(spec)
            assert snapshot(fresh.root) == before
        else:
            manifest = fresh.prepare(spec)
            fresh.apply_to_copy(
                manifest["id"], expected_manifest_sha256=manifest["manifest_sha256"]
            )
            assert_projection(fresh, spec, state, originals, charge)
            once = snapshot(fresh.root)
            assert fresh.apply_to_copy(
                manifest["id"], expected_manifest_sha256=manifest["manifest_sha256"]
            )["duplicate"]
            assert snapshot(fresh.root) == once
