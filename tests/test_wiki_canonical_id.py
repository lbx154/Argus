from __future__ import annotations

import json
from pathlib import Path

from argus_skill.wiki.ingest import canonical_paper_id, ingest_refs_bib
from argus_skill.wiki.store import WikiStore


def _wiki(tmp_path: Path) -> WikiStore:
    root = tmp_path / ".autors" / "demo" / "wiki"
    for sub in ("sources/papers", "data"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return WikiStore(root)


def test_canonical_id_prefers_arxiv_over_key():
    assert (
        canonical_paper_id(
            url="https://arxiv.org/abs/1707.06347v2",
            doi=None,
            key="schulman2017ppo",
        )
        == "arxiv-1707.06347"
    )


def test_canonical_id_doi_when_no_arxiv():
    assert (
        canonical_paper_id(
            url="https://doi.org/10.1007/BF00992696",
            doi=None,
            key="williams1992reinforce",
        )
        == "doi-10.1007__bf00992696"
    )


def test_canonical_id_falls_back_to_key():
    assert canonical_paper_id(url=None, doi=None, key="schulman2017ppo") == "schulman2017ppo"


def test_ingest_same_paper_two_keys_makes_one_source(tmp_path: Path):
    store = _wiki(tmp_path)
    bib = tmp_path / "refs.bib"
    bib.write_text(
        """
@article{schulman2017ppo,
  title={Proximal Policy Optimization Algorithms},
  year={2017},
  url={https://arxiv.org/abs/1707.06347}
}
@article{ppo_arxiv,
  title={Proximal Policy Optimization Algorithms},
  year={2017},
  url={https://arxiv.org/pdf/1707.06347v2.pdf}
}
""",
        encoding="utf-8",
    )
    written = ingest_refs_bib(store, bib_path=bib, ingested_by="test")
    assert len(written) == 1
    assert written[0].name == "arxiv-1707.06347.md"
    aliases = json.loads((store.root / "data" / "paper_aliases.json").read_text())
    assert aliases["schulman2017ppo"] == "arxiv-1707.06347"
    assert aliases["ppo_arxiv"] == "arxiv-1707.06347"


def test_ingest_idempotent_with_alias_index(tmp_path: Path):
    store = _wiki(tmp_path)
    bib = tmp_path / "refs.bib"
    bib.write_text(
        """
@article{schulman2017ppo,
  title={Proximal Policy Optimization Algorithms},
  year={2017},
  url={https://arxiv.org/abs/1707.06347}
}
""",
        encoding="utf-8",
    )
    first = ingest_refs_bib(store, bib_path=bib, ingested_by="test")
    second = ingest_refs_bib(store, bib_path=bib, ingested_by="test")
    assert len(first) == 1
    assert second == []
    aliases = json.loads((store.root / "data" / "paper_aliases.json").read_text())
    assert aliases == {"schulman2017ppo": "arxiv-1707.06347"}
