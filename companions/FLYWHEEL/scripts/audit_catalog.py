"""Deterministically validate the bundled planning catalog.

This check is deliberately offline: it reads only versioned seed files and never
contacts Argus, a conference site, or a research source.  It validates structural
coverage, not whether a forecast deadline has since become official.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "data" / "seeds"
PROMPT_CATALOG = ROOT / "runtime" / "prompt-catalog"
WINDOW_START = date.fromisoformat("2026-08-22")
WINDOW_END = date.fromisoformat("2027-08-22")
EXPECTED_STATUSES = {"official_confirmed", "forecast"}


def load(name: str) -> dict[str, Any]:
    return json.loads((SEEDS / name).read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def is_https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def main() -> None:
    calendar = load("conference_calendar_2026-08-22_2027-08-22.json")
    topic_catalog = load("topics_all_58x5.json")
    domain_catalog = load("domain_evidence.json")

    declared_window = calendar["submission_deadline_window"]
    require(declared_window["start"] == WINDOW_START.isoformat(), "window start changed")
    require(declared_window["end"] == WINDOW_END.isoformat(), "window end changed")
    require(declared_window["boundary"] == "inclusive", "planning window must be inclusive")

    venues = calendar["venues"]
    venue_keys = [venue["key"] for venue in venues]
    require(len(venues) == 58, f"expected 58 venues, found {len(venues)}")
    require(len(set(venue_keys)) == 58, "venue keys are not unique")
    require(calendar["venue_universe"]["count"] == 58, "declared venue count is inconsistent")
    override_audit = calendar.get("official_override_audit") or {}
    require(len(override_audit.get("source_sha256", "")) == 64, "override provenance hash missing")

    deadlines = [target for venue in venues for target in venue.get("targets_in_window", [])]
    require(len(deadlines) == 85, f"expected 85 deadline events, found {len(deadlines)}")
    for target in deadlines:
        deadline = date.fromisoformat(target["deadline_date"])
        require(WINDOW_START <= deadline <= WINDOW_END, f"deadline outside window: {deadline}")
        require(target["evidence_status"] in EXPECTED_STATUSES, "unknown deadline evidence status")
        if target["evidence_status"] == "forecast":
            require(target.get("requires_official_confirmation") is True, "forecast lacks confirmation gate")
            require(bool(target.get("forecast_window_start")), "forecast lacks lower interval")
            require(bool(target.get("forecast_window_end")), "forecast lacks upper interval")
            lower = date.fromisoformat(target["forecast_window_start"])
            upper = date.fromisoformat(target["forecast_window_end"])
            require(lower <= deadline <= upper, "forecast point is outside its stated interval")
            require(
                is_https_url((target.get("forecast_basis") or {}).get("source_url")),
                "forecast lacks a valid HTTPS historical source URL",
            )
        else:
            require(
                target.get("requires_official_confirmation") is False,
                "confirmed deadline must not require official confirmation",
            )
            require(
                is_https_url(target.get("source_url")),
                "confirmed deadline lacks a valid HTTPS official source URL",
            )

    topics = topic_catalog["topics"]
    require(len(topics) == 290, f"expected 290 ideas, found {len(topics)}")
    require(topic_catalog["topic_count"] == 290, "declared topic count is inconsistent")
    require(topic_catalog["topics_per_venue"] == 5, "expected five ideas per venue")
    source_fragments = topic_catalog.get("source_topic_files") or []
    require(len(source_fragments) == 3, "topic source-fragment provenance is incomplete")
    require(
        all(len(fragment.get("source_sha256", "")) == 64 for fragment in source_fragments),
        "topic source-fragment hash missing",
    )
    topic_counts = Counter(topic["venue_key"] for topic in topics)
    require(set(topic_counts) == set(venue_keys), "topic and venue key sets differ")
    require(all(count == 5 for count in topic_counts.values()), "every venue must have five ideas")
    topic_ranks: dict[str, set[int]] = {key: set() for key in venue_keys}
    required_topic_fields = {
        "title_zh", "problem_gap", "core_hypothesis", "method",
        "public_data_or_tasks", "strongest_baselines", "decisive_experiments",
        "compute_fit", "venue_fit_reason", "kill_criterion", "risk_level",
        "reusable_program",
    }
    for topic in topics:
        topic_ranks[topic["venue_key"]].add(int(topic["topic_rank_within_venue"]))
        missing = sorted(field for field in required_topic_fields if not topic.get(field))
        require(not missing, f"an idea is missing required brief fields: {', '.join(missing)}")
    require(
        all(ranks == {1, 2, 3, 4, 5} for ranks in topic_ranks.values()),
        "every venue must have exactly idea ranks 1..5",
    )

    domains = domain_catalog["domains"]
    categories = {venue["category_id"] for venue in venues}
    require(categories <= set(domains), "one or more venue categories lack an evidence contract")

    # The bundled catalog is an executable artifact, not just documentation.
    # Hash the exact bytes Argus will receive so Windows newline translation can
    # never silently invalidate content-addressed prompt manifests again.
    catalog_path = PROMPT_CATALOG / "CATALOG.json"
    require(catalog_path.is_file(), "bundled prompt CATALOG.json is missing")
    prompt_catalog = json.loads(catalog_path.read_bytes().decode("utf-8"))
    packets = prompt_catalog.get("packets") or []
    require(prompt_catalog.get("count") == 290, "bundled prompt catalog count is inconsistent")
    require(len(packets) == 290, "bundled prompt catalog must contain 290 packets")
    for packet in packets:
        packet_path = packet.get("packet_path")
        require(isinstance(packet_path, str) and packet_path, "prompt packet path is missing")
        packet_root = PROMPT_CATALOG / packet_path
        objective_path = packet_root / "OBJECTIVE.md"
        manifest_path = packet_root / "MANIFEST.json"
        require(objective_path.is_file(), f"prompt objective is missing: {packet_path}")
        require(manifest_path.is_file(), f"prompt manifest is missing: {packet_path}")
        disk_sha256 = hashlib.sha256(objective_path.read_bytes()).hexdigest()
        manifest = json.loads(manifest_path.read_bytes().decode("utf-8"))
        require(
            disk_sha256 == packet.get("prompt_sha256"),
            f"catalog hash does not match objective bytes: {packet_path}",
        )
        require(
            disk_sha256 == manifest.get("prompt_sha256"),
            f"manifest hash does not match objective bytes: {packet_path}",
        )

    status_counts = Counter(target["evidence_status"] for target in deadlines)
    print(
        "catalog-audit-ok: "
        f"{len(venues)} venues, {len(deadlines)} deadlines "
        f"({status_counts['official_confirmed']} official_confirmed, "
        f"{status_counts['forecast']} forecast), {len(topics)} ideas, "
        f"{len(domains)} domain evidence contracts"
    )


if __name__ == "__main__":
    main()
