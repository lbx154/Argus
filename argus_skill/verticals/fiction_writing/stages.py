"""fiction_writing-vertical stage definitions.

A vertical whose deliverable is NOT a number and NOT a paper, but a piece of
NARRATIVE PROSE (a short story or a chapter, zh or en) written from a brief or
continued from existing text — together with a faithful, structured
``story_state`` that keeps characters, world, timeline, items, threads, and
foreshadowing consistent across chapters.

The 6 stages (``completion_gate="none"`` — the reviewer verdict ends the
mission; there is no metric and no paper submission):

1. **intake**: normalize the operator's request into ``fiction/creative_brief.json``
   (language, form, mode=from_scratch|continuation, genre, market_style, length,
   viewpoint, tense, constraints) and a checkable ``fiction/style_profile.json``
   (abstract style features — sentence rhythm, narrative distance, dialogue
   ratio, imagery density, exposition level, ending strategy). Style is captured
   as ABSTRACT FEATURES, never as "imitate author X".

2. **plan**: produce ``fiction/story_plan.json`` (premise, arc, beats, cast) and
   ``fiction/chapter_goal.json`` (what THIS chapter must accomplish). For a
   continuation, the plan is grounded in the EXISTING ``story_state``.

3. **draft**: write ``fiction/draft.md`` — the chapter prose, honoring brief +
   style profile + (for continuation) the established state.

4. **state_update**: extract what CHANGED into ``fiction/state_patch.json`` and
   apply it through the safe patch engine to produce
   ``fiction/story_state.json``. The writer NEVER hand-rewrites the whole state
   — it emits a structured patch that is program-validated (idempotent, no
   silent deletion of prior state, valid id references, parseable timeline).
   See ``argus_skill.verticals.fiction_writing.state``.

5. **review**: the reviewer produces ``fiction/review.json`` — typed, severity-
   tagged, evidence-located findings across CONTINUITY (dead characters
   returning, knowledge the character shouldn't have, item teleport, location
   clashes, timeline breaks, world-rule violations, motive-incoherent actions,
   foreshadowing dropped/leaked, viewpoint/tense drift, language drift) and
   CRAFT (style consistency, character-voice distinctness, scene concreteness,
   show-don't-tell, over-summarization, mechanical twists, pacing, ending
   closes the core question, obvious AI-tells). Craft/aesthetic items are
   HEURISTIC self-checks + non-blocking comments + a few observable proxies —
   NOT a deterministic pass/fail gate (there is no ground-truth score).

6. **revise**: apply the review's targeted fixes into ``fiction/final.md`` and
   ``fiction/updated_story_state.json``. The reviewer verdict ends the mission.

Design invariants (harness-enforced; all narrative judgment is the agent's):

* ``story_state`` is only ever mutated through a validated ``state_patch``; the
  engine refuses to delete undeclared prior state and rejects dangling id
  references — so history cannot be silently lost (``state.apply_patch``);
* language is an ADAPTER over one shared narrative core: characters, world,
  timeline, plot, threads, and memory are language-agnostic; only style
  guidelines and anti-AI patterns are per-language (zh/en);
* genre / market style (suspense, romance, web-fiction, literary, ...) are
  PROFILES/data in the brief, never separate verticals;
* aesthetic "AI-flavor" quality is never faked as a deterministic score — it is
  a self-check + non-blocking reviewer comment layer.
"""
from __future__ import annotations

from ...skills.stage_checklists import ChecklistItem

STAGE_ORDER = ["intake", "plan", "draft", "state_update", "review", "revise"]

#: This vertical's success is reviewer-certified narrative prose + a consistent
#: story_state, NOT a numeric metric and NOT a paper. ``"none"`` suppresses both
#: the paper (``full_emnlp``) and the metric prompt-framing regimes.
completion_gate = "none"

# Generic across verticals; a private copy (mirrors learning/speedrun).
_PIPELINE_CHECK = ("Pipeline state present", "test -f research/PIPELINE_STATE.json")

# Lenient artifact-existence checks. The reviewer is the real gate; these only
# confirm the stage produced *something* to review.
STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "intake": [
        _PIPELINE_CHECK,
        ("Creative brief produced",
         "test -s fiction/creative_brief.json"),
        ("Style profile produced",
         "test -s fiction/style_profile.json"),
        ("Source registry is well-formed",
         "{python} -m argus_skill.verticals.fiction_writing.source_check "
         "validate-registry"),
    ],
    "plan": [
        _PIPELINE_CHECK,
        ("Story plan and chapter goal produced",
         "test -s fiction/story_plan.json && test -s fiction/chapter_goal.json"),
    ],
    "draft": [
        _PIPELINE_CHECK,
        ("Chapter draft written", "test -s fiction/draft.md"),
    ],
    "state_update": [
        _PIPELINE_CHECK,
        ("Structured state patch produced",
         "test -s fiction/state_patch.json"),
        ("Story state present after patch apply",
         "test -s fiction/story_state.json"),
    ],
    "review": [
        _PIPELINE_CHECK,
        ("Structured review produced", "test -s fiction/review.json"),
        ("Review conforms to the literary review contract",
         "{python} -m argus_skill.verticals.fiction_writing.review_check "
         "validate fiction/review.json"),
        ("Source-usage ledger produced (explicit, empty uses[] if none consulted)",
         "test -s fiction/source_usage.json"),
        ("Every recorded source use is rights-defensible",
         "{python} -m argus_skill.verticals.fiction_writing.source_check "
         "check-usage fiction/source_usage.json"),
    ],
    "revise": [
        _PIPELINE_CHECK,
        ("Final prose and updated state produced",
         "test -s fiction/final.md && test -s fiction/updated_story_state.json"),
        ("Revision plan derived from the review",
         "test -s fiction/revision_plan.json"),
        ("Revision plan covers every blocking finding with its must_not_break",
         "{python} -m argus_skill.verticals.fiction_writing.review_check "
         "check-plan fiction/review.json fiction/revision_plan.json"),
        ("Artifact manifest records the produced chain",
         "test -s fiction/artifact_manifest.json"),
        ("Artifact manifest conforms to the shared lineage contract",
         "{python} -m argus_skill.verticals.fiction_writing.manifest_check "
         "validate fiction/artifact_manifest.json"),
        ("Every artifact the manifest records is present on disk",
         "{python} -m argus_skill.verticals.fiction_writing.manifest_check "
         "check-content fiction/artifact_manifest.json"),
        ("Final prose traces back to its draft and review (provenance closed)",
         "{python} -m argus_skill.verticals.fiction_writing.manifest_check "
         "check-lineage fiction/artifact_manifest.json"),
    ],
}

# (skill_to_load, review_instructions, files_to_read). Resolves to this
# vertical's own skills/reviewer/ copy.
_REVIEW_SKILL = "reviewer/continuity-style-and-plot-review.md"

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "intake": (
        _REVIEW_SKILL,
        "Verify the brief captures language, form, mode (from_scratch vs "
        "continuation), genre/market style, length, viewpoint and tense, and that "
        "the style profile is ABSTRACT features (rhythm/distance/dialogue "
        "ratio/imagery/exposition/ending), never 'imitate author X'. For a "
        "continuation, confirm an existing story_state was loaded, not invented.",
        ["fiction/creative_brief.json", "fiction/style_profile.json"],
    ),
    "plan": (
        _REVIEW_SKILL,
        "Gate the plan before drafting: does the chapter goal advance the arc? "
        "For a continuation, is the plan consistent with the established "
        "characters/world/timeline in story_state (no contradictions, no "
        "resurrected dead characters, no forgotten open threads)?",
        ["fiction/story_plan.json", "fiction/chapter_goal.json",
         "fiction/story_state.json"],
    ),
    "draft": (
        _REVIEW_SKILL,
        "First-pass read of the draft against brief + style profile. Flag "
        "viewpoint/tense drift, language drift (e.g. an en original continued in "
        "zh), and gross style mismatch. Craft notes are non-blocking here.",
        ["fiction/draft.md", "fiction/creative_brief.json",
         "fiction/style_profile.json"],
    ),
    "state_update": (
        _REVIEW_SKILL,
        "Verify the state_patch faithfully reflects what the draft changed and "
        "nothing more: every new character/item/location/thread/foreshadowing "
        "the chapter introduced is captured; id references are valid; the patch "
        "does not silently drop prior state. Confirm it applied cleanly to "
        "story_state (revision bumped, applied_patches records the patch_id).",
        ["fiction/state_patch.json", "fiction/story_state.json",
         "fiction/draft.md"],
    ),
    "review": (
        _REVIEW_SKILL,
        "Produce the typed, severity-tagged, evidence-located findings. "
        "CONTINUITY (EVERY item here is severity=blocking — including impossible "
        "knowledge and language drift; do not downgrade): dead/absent character "
        "returns unexplained; character knows what they cannot; item teleports; "
        "same character in two places at one time; timeline break; world-rule "
        "violation; motive-incoherent action; foreshadowing dropped or leaked "
        "before payoff; viewpoint/tense drift; language drift. CRAFT "
        "(NON-BLOCKING heuristic + observable proxies): style consistency, "
        "character-voice distinctness, scene concreteness, show-don't-tell, "
        "over-summarization, mechanical/telegraphed twist, pacing/pressure, "
        "ending closes the core question, obvious AI-tells (slogan endings, "
        "abstract-word piling, homogeneous imagery). Do NOT fake a numeric "
        "quality score.",
        ["fiction/draft.md", "fiction/story_state.json"],
    ),
    "revise": (
        _REVIEW_SKILL,
        "Verify the revision addressed every BLOCKING continuity finding with a "
        "concrete fix (cite the changed span), that no NEW contradiction was "
        "introduced, and that updated_story_state.json is consistent with "
        "final.md. Confirm fiction/revision_plan.json was derived from "
        "review.json via the literary review contract and that every blocking "
        "finding's must_not_break invariant is preserved. Confirm "
        "fiction/artifact_manifest.json records the produced chain — final "
        "traces back to its draft and review, and supersedes the draft it "
        "replaced. Craft findings may remain as accepted trade-offs with a "
        "rationale.",
        ["fiction/final.md", "fiction/updated_story_state.json",
         "fiction/review.json", "fiction/revision_plan.json",
         "fiction/artifact_manifest.json"],
    ),
}

CHECKLIST_STAGE_ORDER = tuple(STAGE_ORDER)

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "intake": (
        ChecklistItem(
            id="brief-complete",
            statement="creative_brief.json fixes language, form, mode, genre, "
            "length, viewpoint and tense; nothing critical left implicit.",
            evidence_hint="fiction/creative_brief.json fields non-empty",
        ),
        ChecklistItem(
            id="style-abstract",
            statement="style_profile.json is abstract, checkable features, not "
            "'imitate author X'.",
            evidence_hint="fiction/style_profile.json feature keys",
        ),
        ChecklistItem(
            id="continuation-grounded",
            statement="For mode=continuation, the existing story_state was loaded "
            "as the ground truth (not re-invented).",
            evidence_hint="story_state.json present and referenced when continuing",
        ),
    ),
    "plan": (
        ChecklistItem(
            id="chapter-goal-advances",
            statement="chapter_goal.json states a concrete goal that advances the "
            "arc (change/pressure), not a flat scene.",
            evidence_hint="fiction/chapter_goal.json goal + arc link",
        ),
        ChecklistItem(
            id="plan-consistent-with-state",
            statement="For a continuation, the plan contradicts nothing in the "
            "established characters/world/timeline/open threads.",
            evidence_hint="plan cross-checked against story_state.json",
        ),
    ),
    "draft": (
        ChecklistItem(
            id="viewpoint-tense-stable",
            statement="The draft holds a single viewpoint and tense as declared "
            "in the brief.",
            evidence_hint="no 1st/3rd or past/present drift in draft.md",
        ),
        ChecklistItem(
            id="language-consistent",
            statement="The draft is in the brief's language; a continuation stays "
            "in the source work's language.",
            evidence_hint="draft.md language == brief.language",
        ),
    ),
    "state_update": (
        ChecklistItem(
            id="patch-faithful",
            statement="The state_patch captures every entity/relationship/thread/"
            "foreshadowing the chapter introduced or changed, and nothing the "
            "chapter did not.",
            evidence_hint="state_patch.json ops vs draft.md",
        ),
        ChecklistItem(
            id="patch-safe",
            statement="The patch applied through the engine: idempotent by "
            "patch_id, valid id references, no silent deletion of prior state, "
            "timeline still parseable.",
            evidence_hint="story_state.json revision bumped + applied_patches",
        ),
    ),
    "review": (
        ChecklistItem(
            id="continuity-findings-evidenced",
            statement="Every continuity finding is typed, severity-tagged, and "
            "cites an evidence location in the draft/state.",
            evidence_hint="review.json findings[].{type,severity,location}",
        ),
        ChecklistItem(
            id="aesthetic-not-faked",
            statement="Craft/AI-flavor items are recorded as heuristic, "
            "non-blocking observations or observable proxies, not as a "
            "manufactured deterministic score.",
            evidence_hint="review.json craft findings marked non-blocking",
        ),
    ),
    "revise": (
        ChecklistItem(
            id="blocking-fixed",
            statement="Every BLOCKING continuity finding is resolved with a "
            "concrete change; no new contradiction introduced.",
            evidence_hint="final.md vs review.json blocking items",
        ),
        ChecklistItem(
            id="state-matches-final",
            statement="updated_story_state.json is consistent with final.md.",
            evidence_hint="updated_story_state.json vs final.md",
        ),
    ),
}


def role_banner(role: str) -> str:
    """Hard-override framing per role. Suppresses the paper/metric regimes and
    reframes the mission as consistent, reviewer-gated narrative creation."""
    common = (
        "MISSION TYPE: FICTION WRITING. The deliverable is a piece of narrative "
        "prose (a short story or a chapter, zh or en) — written from a brief or "
        "continued from existing text — plus a faithful, structured story_state. "
        "It is NOT a benchmark score and NOT a research paper. Do not introduce "
        "paper or optimization framing.\n"
    )
    if role == "planner":
        return common + (
            "Drive intake -> plan -> draft -> state_update -> review -> revise, one "
            "bounded task per stage. Language is an adapter over one shared "
            "narrative core; genre/market style is a profile in the brief, never a "
            "new pipeline."
        )
    if role == "engineer":
        return common + (
            "(1) Normalize the request into a creative_brief + an ABSTRACT style "
            "profile (never 'imitate author X'). (2) For a continuation, LOAD the "
            "existing story_state as ground truth; do not re-invent it. (3) Plan "
            "the chapter's goal so it advances the arc. (4) Draft in the brief's "
            "language, holding one viewpoint and tense. (5) NEVER hand-rewrite the "
            "whole story_state — extract what changed into a structured state_patch "
            "and apply it through the patch engine. (6) Keep prose concrete; avoid "
            "slogan endings, abstract-word piling, and telegraphed twists. (7) In "
            "revise, derive fiction/revision_plan.json from fiction/review.json "
            "via the literary review contract — address every BLOCKING finding "
            "first and never break a finding's must_not_break invariants. (8) "
            "Record fiction/artifact_manifest.json — the versioned artifact chain "
            "(brief -> plan -> draft -> state -> review -> revision_plan -> final) "
            "with each artifact's parents, producer_stage, content_path and "
            "status, so final.md's provenance (which draft + which review) is "
            "auditable and final SUPERSEDES the draft it replaced. (9) EVERY "
            "mission records fiction/source_usage.json — an explicit provenance "
            "ledger. If you consulted any registered source (queried a corpus, "
            "read/cited a public-domain text), log each use with its source_id, "
            "the exact allowed use, the stage, and (for a citation) the "
            "attribution; if you consulted NO external source, still write it with "
            "an empty uses[] — silence is not allowed. Never use a source for a "
            "purpose its allowed_uses forbids, and never present a queried, "
            "un-ingested source as a cited or learned fact."
        )
    if role == "reviewer":
        return common + (
            "You gate the chapter. BLOCK on hard continuity contradictions "
            "(dead character returns, impossible knowledge, item teleport, "
            "location/timeline clash, world-rule break, motive-incoherent action, "
            "dropped/leaked foreshadowing, viewpoint/tense/language drift), each "
            "finding typed + severity-tagged + evidence-located. Craft and "
            "AI-flavor are NON-BLOCKING heuristics + observable proxies — never a "
            "faked numeric score. Follow the 'Continuity, Style and Plot Review' "
            "skill. Emit fiction/review.json as {verdict, findings[]} per the "
            "shared literary review contract — each finding "
            "{id, type, severity(critical|major|minor|note), blocking(bool), "
            "location, evidence, suggested_action, must_not_break[]}; a blocking "
            "finding forces verdict='revise'."
        )
    return common
