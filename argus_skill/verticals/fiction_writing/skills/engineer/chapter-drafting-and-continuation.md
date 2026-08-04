---
name: "Chapter Drafting And Continuation"
description: "Write the chapter prose (fiction/draft.md) from the plan + brief + style profile, holding one viewpoint and tense and the brief's language; for a continuation, stay faithful to the established story_state. Applies the language-specific style and anti-AI reference set. The draft stage of fiction_writing."
---

## Title
Chapter Drafting And Continuation

## Description
Produce the actual narrative prose for one chapter/scene, executing the chapter
goal while honoring the style profile and the language adapter. For a
continuation, everything the draft asserts must be consistent with `story_state`.

## Category
fiction-drafting

## When to use
- The `draft` stage of `fiction_writing`, after planning.
- Both from-scratch drafting and continuation of an existing work.

Do NOT use for expository/academic writing, summaries, or non-narrative text.

## How to solve
1. **Load** `chapter_goal.json`, `story_plan.json`, `style_profile.json`, and —
   for a continuation — `story_state.json`. Load the language adapter reference
   for the brief's language (`references/zh/…` or `references/en/…`) and the
   shared craft references (`references/shared/…`).
2. **Draft `fiction/draft.md`** executing the chapter goal:
   - hold ONE viewpoint and ONE tense throughout, in the brief's LANGUAGE
     (a zh original stays zh; an en original stays en);
   - realize the intended irreversible change; keep pressure on the scene;
   - use concrete, sensory specifics; let characters' voices differ.
3. **Respect established facts** (continuation): a dead/absent character does not
   act on-stage without an explained return; a character only uses what they
   `know`; items stay with their holder/location until moved on-page; events
   respect timeline order and world_rules; do not pay off a foreshadow before it
   is planted, nor leak it.
4. **Apply the anti-AI discipline** from the language reference: no slogan/uplift
   summarizing ending, no abstract-emotion telling in place of showing, no
   piled-on synonyms or homogeneous imagery, no telegraphed/mechanical twist.
   Prefer to end on an image and withhold the thesis. Avoid the **register-level
   tells** that survive plagiarism checks but read as "AI 味": the 情绪涌动 fill-in
   ("心中涌起一股暖流"), 凝固时刻 ("空气仿佛凝固"), 时刻拔高 ("这一刻/那一瞬间"),
   虚化感受 ("说不出的…/难以言喻"), manner-adverb stacking (无声地/静静地/轻轻地);
   in en, "the air seemed to freeze", "an odd sense of", filter words.
5. **Do NOT copy the source ('不能抄')**: for a continuation, capture the author's
   VOICE through the voice card's abstract features (register / 称谓 / 句式 /
   character idiolect) — NEVER by reproducing their sentences. Do not lift verbatim
   spans from `reference_text.md`; the deterministic novelty gate BLOCKS a long
   copied run. Allude to and echo the source; do not transcribe it.
6. **Do NOT touch story_state here** — state changes are extracted in the next
   stage as a structured patch.

## When NOT to use
- To update `story_state` (that is the state_update stage).
- When plan/brief are missing.

## Common pitfalls
- Viewpoint/tense/language drift mid-chapter.
- Contradicting `story_state` on a continuation.
- Slogan endings, adverb-stuffed dialogue tags, abstract emotion-naming.
