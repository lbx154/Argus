from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from argus_skill.wiki.ingest import (
    ingest_lit_matrix,
    ingest_refs_bib,
    parse_bib_entries,
)
from argus_skill.wiki.schema import SourcePaper
from argus_skill.wiki.store import WikiStore

SAMPLE_BIB = """
@article{williams1992reinforce,
  title={Simple statistical gradient-following algorithms for connectionist reinforcement learning},
  author={Williams, Ronald J.},
  journal={Machine Learning},
  volume={8},
  pages={229--256},
  year={1992},
  doi={10.1007/BF00992696}
}

@inproceedings{schulman2017ppo,
  title={Proximal Policy Optimization Algorithms},
  author={Schulman, John and Wolski, Filip and Dhariwal, Prafulla and Radford, Alec and Klimov, Oleg},
  year={2017},
  url={https://arxiv.org/abs/1707.06347}
}

@article{xu2023imagereward,
  title={ImageReward: Learning and Evaluating Human Preferences for Text-to-Image Generation},
  author={Xu, Jiazheng and others},
  year={2023},
  url={https://arxiv.org/abs/2304.05977}
}
"""


SAMPLE_TSV = """id\tyear\ttype\tvenue\turl\trelevance_to_bagel_rl_diagnosis
williams1992reinforce\t1992\tclassic\tMachine Learning\thttps://doi.org/10.1007/BF00992696\tPolicy-gradient basis; zero group variance implies no useful advantage.
schulman2017ppo\t2017\tclassic\tarXiv\thttps://arxiv.org/abs/1707.06347\tKL/clipping/update stability anchor.
xu2023imagereward\t2023\trecent\tNeurIPS\thttps://arxiv.org/abs/2304.05977\tVisual reward-model reliability and preference correlation.
"""


@pytest.fixture
def wiki(tmp_path: Path) -> WikiStore:
    root = tmp_path / ".autors" / "demo" / "wiki"
    for sub in (
        "sources/papers",
        "sources/repos",
        "sources/runs",
        "pages/techniques",
        "pages/conflicts",
        "pages/patterns",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return WikiStore(root)


def test_parse_bib_entries_basic():
    entries = parse_bib_entries(SAMPLE_BIB)
    assert len(entries) == 3
    keys = [e["key"] for e in entries]
    assert keys == ["williams1992reinforce", "schulman2017ppo", "xu2023imagereward"]
    schulman = next(e for e in entries if e["key"] == "schulman2017ppo")
    assert schulman["title"] == "Proximal Policy Optimization Algorithms"
    assert schulman["year"] == "2017"
    assert schulman["url"] == "https://arxiv.org/abs/1707.06347"


def test_parse_bib_handles_missing_url_via_doi():
    entries = parse_bib_entries(SAMPLE_BIB)
    williams = next(e for e in entries if e["key"] == "williams1992reinforce")
    # No `url` field, but `doi` should fall back to https://doi.org/<doi>
    assert williams["url"].startswith("https://doi.org/10.1007/BF00992696")


def test_parse_bib_resyncs_after_unclosed_entry():
    entries = parse_bib_entries(
        """
@article{broken,
  title={This entry never closes},

@article{valid2026,
  title={Valid Entry},
  year={2026},
  url={https://arxiv.org/abs/2601.00001}
}
"""
    )
    assert [entry["key"] for entry in entries] == ["valid2026"]


def test_ingest_refs_bib_writes_one_source_per_entry(wiki: WikiStore, tmp_path: Path):
    bib = tmp_path / "refs.bib"
    bib.write_text(SAMPLE_BIB, encoding="utf-8")
    written = ingest_refs_bib(
        wiki,
        bib_path=bib,
        ingested_by="wiki-curator@test-mission",
    )
    assert len(written.written) == 3
    # Files exist on disk with the expected paths
    for path in written.written:
        assert path.exists()
    # Round-trip the first one
    src = wiki.read_source(SourcePaper, "papers/doi-10.1007__bf00992696")
    assert src.title.startswith("Simple statistical")
    assert src.url.startswith("https://doi.org/")
    assert src.ingested_by == "wiki-curator@test-mission"


def test_ingest_refs_bib_is_idempotent(wiki: WikiStore, tmp_path: Path):
    bib = tmp_path / "refs.bib"
    bib.write_text(SAMPLE_BIB, encoding="utf-8")
    first = ingest_refs_bib(wiki, bib_path=bib, ingested_by="x")
    second = ingest_refs_bib(wiki, bib_path=bib, ingested_by="x")
    assert len(first.written) == 3
    assert len(second.written) == 0  # sources are immutable; second call skips all


def test_ingest_lit_matrix_appends_relevance_to_source_body(
    wiki: WikiStore,
    tmp_path: Path,
):
    bib = tmp_path / "refs.bib"
    bib.write_text(SAMPLE_BIB, encoding="utf-8")
    ingest_refs_bib(wiki, bib_path=bib, ingested_by="x")

    tsv = tmp_path / "LIT_MATRIX.tsv"
    tsv.write_text(SAMPLE_TSV, encoding="utf-8")
    result = ingest_lit_matrix(wiki, tsv_path=tsv)
    assert result.enriched_count == 3
    src = wiki.read_source(SourcePaper, "papers/arxiv-1707.06347")
    assert "KL/clipping/update stability anchor." in src.body


def test_ingest_lit_matrix_skips_papers_not_in_sources(
    wiki: WikiStore,
    tmp_path: Path,
):
    # No bib ingested -> sources/papers/ empty -> LIT_MATRIX rows have no
    # target -> enrichment should be a no-op, not an error.
    tsv = tmp_path / "LIT_MATRIX.tsv"
    tsv.write_text(SAMPLE_TSV, encoding="utf-8")
    result = ingest_lit_matrix(wiki, tsv_path=tsv)
    assert result.enriched_count == 0


def test_ingest_lit_matrix_matches_punctuation_only_key_drift(
    wiki: WikiStore,
    tmp_path: Path,
):
    bib = tmp_path / "refs.bib"
    bib.write_text(
        """
@article{wallace2023diffusiondpo,
  title={Diffusion Model Alignment Using Direct Preference Optimization},
  author={Wallace, Bram},
  year={2023},
  url={https://arxiv.org/abs/2311.12908}
}
""",
        encoding="utf-8",
    )
    ingest_refs_bib(wiki, bib_path=bib, ingested_by="x")

    tsv = tmp_path / "LIT_MATRIX.tsv"
    tsv.write_text(
        "id\tyear\ttype\tvenue\turl\trelevance_to_demo\n"
        "wallace2023diffusion_dpo\t2023\trecent\tarXiv\t"
        "https://arxiv.org/abs/2311.12908\tPreference-optimization anchor.\n",
        encoding="utf-8",
    )
    result = ingest_lit_matrix(wiki, tsv_path=tsv)
    assert result.enriched_count == 1
    src = wiki.read_source(SourcePaper, "papers/arxiv-2311.12908")
    assert "Preference-optimization anchor." in src.body


def test_ingest_lit_matrix_continues_past_one_bad_source(
    wiki: WikiStore,
    tmp_path: Path,
):
    good = SourcePaper(
        id="papers/good2026",
        url="https://example.test/good",
        title="Good",
        ingested_at=date(2026, 6, 4),
        ingested_by="test",
        checksum="sha256:good",
        body="@misc{good2026, title={Good}}",
    )
    wiki.write_source(good)
    bad_path = wiki.root / "sources" / "papers" / "bad2026.md"
    bad_path.write_text("not frontmatter", encoding="utf-8")

    tsv = tmp_path / "LIT_MATRIX.tsv"
    tsv.write_text(
        "id\tyear\ttype\tvenue\turl\trelevance_to_demo\n"
        "bad2026\t2026\trecent\tX\thttps://example.test/bad\tBad relevance.\n"
        "good2026\t2026\trecent\tX\thttps://example.test/good\tGood relevance.\n",
        encoding="utf-8",
    )

    result = ingest_lit_matrix(wiki, tsv_path=tsv)

    assert result.enriched_count == 1
    assert result.warnings
    assert "bad2026" in result.warnings[0]
    src = wiki.read_source(SourcePaper, "papers/good2026")
    assert "Good relevance." in src.body
