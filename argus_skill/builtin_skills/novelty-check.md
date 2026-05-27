---
name: novelty-check
description: "Verify research idea novelty against recent literature. Searches arXiv, Semantic Scholar, and web sources to identify closest prior work. Use when starting a new research direction or before committing to a claim of novelty."
category: literature
version: "1.0"
scientist_model: gpt-5.4
created_at: "2025-07-27"
---

# Novelty Check

Verify whether a proposed method/idea has already been done in the literature.

## When to Use

- Before committing significant effort to a research idea
- When framing contributions for a paper submission
- After literature review to double-check coverage
- User says "查新", "novelty check", "有没有人做过"

## Workflow

### Phase A: Extract Key Claims

1. Read the method description
2. Identify 3-5 core technical claims that would need to be novel:
   - What is the method?
   - What problem does it solve?
   - What is the mechanism?
   - What makes it different from obvious baselines?

### Phase B: Multi-Source Literature Search

For EACH core claim, search using ALL available sources:

1. **Web Search** (arXiv, Google Scholar, Semantic Scholar):
   - Use specific technical terms from the claim
   - Try at least 3 different query formulations per claim
   - Include year filters for recent work (last 2 years)

2. **Known venues**: Check against top venues:
   - ICLR, NeurIPS, ICML, EMNLP, ACL, NAACL (most recent 2 years)
   - Recent arXiv preprints

3. **Read abstracts**: For each potentially overlapping paper, fetch and read abstract + related work

### Phase C: Independent Verification

Use a separate model/reasoning pass with high effort to evaluate:
- The proposed method description
- All papers found in Phase B
- Question: "Is this method novel? What is the closest prior work? What is the delta?"

### Phase D: Novelty Report

```markdown
## Novelty Check Report

### Proposed Method
[1-2 sentence description]

### Core Claims
1. [Claim 1] — Novelty: HIGH/MEDIUM/LOW — Closest: [paper]
2. [Claim 2] — Novelty: HIGH/MEDIUM/LOW — Closest: [paper]
...

### Closest Prior Work
| Paper | Year | Venue | Overlap | Key Difference |
|-------|------|-------|---------|----------------|

### Overall Novelty Assessment
- Score: X/10
- Recommendation: PROCEED / PROCEED WITH CAUTION / ABANDON
- Key differentiator: [what makes this unique, if anything]
- Risk: [what a reviewer would cite as prior work]

### Suggested Positioning
[How to frame the contribution to maximize novelty perception]
```

## Important Rules

- Be BRUTALLY honest — false novelty claims waste months of research time
- "Applying X to Y" is NOT novel unless the application reveals surprising insights
- Check both the method AND the experimental setting for novelty
- If the method is not novel but the FINDING would be, say so explicitly
- Always check the most recent 6 months of arXiv — the field moves fast
- Never fabricate paper titles, arXiv IDs, or DOIs — mark uncertain entries as `[UNVERIFIED]`

## Integration

- Called by `auto-research-pipeline` during the literature stage
- Feeds into `research-brief-to-experiment-plan` (positioning section)
- Results referenced by `emnlp-paper-drafting` for related work framing
