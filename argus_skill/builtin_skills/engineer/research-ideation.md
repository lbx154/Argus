---
name: research-ideation
description: "Structured brainstorming frameworks for discovering research ideas. Provides 10 complementary ideation lenses: problem-first thinking, abstraction ladder, tension hunting, cross-pollination, what-changed principle, failure analysis, simplicity test, stakeholder rotation, composition/decomposition, and explain-it test."
category: ideation
version: "1.0"
scientist_model: gpt-5.5
created_at: "2025-07-27"
---

# Research Ideation: Structured Brainstorming

Ten complementary frameworks for moving from vague curiosity to concrete research proposals.

## When to Use

- Starting a new research direction
- Feeling stuck on a current project
- Evaluating whether a half-formed idea has potential
- Transitioning between research areas
- Looking for underexplored gaps in a field

## Framework Selection Guide

| Your Situation | Start With |
|---------------|------------|
| "I don't know what area to work in" | Tension Hunting → What Changed |
| "I have a vague area but no specific idea" | Abstraction Ladder → Failure Analysis |
| "I have an idea but not sure it's good" | Explain-It Test → Simplicity Test |
| "I have a good idea but need a fresh angle" | Cross-Pollination → Stakeholder Rotation |
| "I want to combine existing work" | Composition/Decomposition |
| "I found a technique and want to apply it" | Problem-First Check → Stakeholder Rotation |
| "I want to challenge conventional wisdom" | Failure Analysis → Simplicity Test |

## The 10 Frameworks

### 1. Problem-First vs. Solution-First

**Problem-First** (pain point → method):
- Start with a concrete failure or unmet need
- Naturally yields impactful work
- Ask: Who suffers? How much?

**Solution-First** (new capability → application):
- Start with a new tool seeking application
- Risk: hammer looking for nail
- Must identify at least two genuine problems it addresses

### 2. The Abstraction Ladder

| Direction | Action | Outcome |
|-----------|--------|---------|
| **Up** (generalize) | Turn specific result into broader principle | Framework papers |
| **Down** (instantiate) | Test under concrete constraints | Empirical papers |
| **Sideways** (analogize) | Apply to adjacent domain | Transfer papers |

### 3. Tension and Contradiction Hunting

Breakthroughs resolve tensions between conflicting goals:

| Tension | Opportunity |
|---------|-------------|
| Performance ↔ Efficiency | Match SOTA with 10x less compute? |
| Privacy ↔ Utility | Close accuracy gap with federated methods? |
| Generality ↔ Specialization | When does fine-tuning beat prompting? |
| Safety ↔ Capability | Can alignment improve capability? |
| Interpretability ↔ Performance | Do insights enable better architectures? |
| Scale ↔ Accessibility | Can small models replicate emergent behaviors? |

Ask: Is this trade-off fundamental or an artifact of current methods?

### 4. Cross-Pollination (Analogy Transfer)

Borrow structural ideas from other disciplines:

| Source Field | Transferable Concepts |
|-------------|----------------------|
| Neuroscience | Attention, memory consolidation, hierarchical processing |
| Physics | Energy-based models, phase transitions |
| Economics | Mechanism design, auction theory, incentive alignment |
| Ecology | Population dynamics, niche competition |
| Control Theory | Feedback loops, stability, adaptive regulation |

Requirements: structural fidelity, non-obvious connection, testable predictions.

### 5. The "What Changed?" Principle

Revisit old problems under new conditions:

| Change Type | Research Implication |
|------------|---------------------|
| Compute 10x faster | Previously expensive methods become feasible |
| Trillion-token data | Statistical arguments may now hold at scale |
| New regulations | Creates demand for compliant alternatives |
| High-profile failures | Exposes gaps in existing approaches |

Frame: "X was impractical because Y, but Z has changed."

### 6. Failure Analysis and Boundary Probing

Probe where methods break:
- **Distributional**: Out-of-distribution inputs?
- **Scale**: 10x or 0.1x typical scale?
- **Adversarial**: Can it be deliberately broken?
- **Compositional**: Multiple capabilities combined?
- **Temporal**: Concept drift over time?

### 7. The Simplicity Test

Before accepting complexity, ask if simpler suffices:

Warning signs of unnecessary complexity:
- Many hyperparameters with narrow optimal ranges
- Ablations show most components contribute marginally
- Simple baseline never properly tuned
- Improvement within noise on most benchmarks

Contribution: "We show [simple method] with [one modification] matches [complex SOTA]"

### 8. Stakeholder Rotation

| Stakeholder | Key Questions |
|-------------|---------------|
| End User | Usable? Unacceptable errors? Latency? |
| Developer | Debuggable? Maintenance burden? |
| Theorist | Why does it work? Formal guarantees? |
| Adversary | How to exploit? Attack surfaces? |
| Regulator | Auditable? Explainable? |

### 9. Composition and Decomposition

**Compose**: Combine two methods that solve complementary subproblems → emergent capability?
**Decompose**: Break apart monolithic system → which component is the bottleneck?

### 10. The Explain-It Test

Two-sentence template:
> **S1** (Problem): "[Domain] struggles with [problem], which matters because [consequence]."
> **S2** (Insight): "We [approach] by [mechanism], which works because [reason]."

If you can't fill this → the idea isn't ready.

## Integrated Workflow

### Phase 1: Diverge (10-20 candidates)
1. Scan for tensions (F3)
2. Check what changed (F5)
3. Probe boundaries (F6)
4. Cross-pollinate (F4)
5. Compose/decompose (F9)

### Phase 2: Converge (top 3-5)

| Filter | Kill Criterion |
|--------|----------------|
| Explain-It Test | Can't state in two sentences → not clear |
| Problem-First Check | No one suffers → drop |
| Simplicity Test | Simpler approach works → simplify |
| Stakeholder Check | No clear beneficiary → drop |
| Feasibility | Clearly infeasible → park for later |

### Phase 3: Refine (top 1)
1. Write two-sentence pitch
2. Identify core tension being resolved
3. List 3 concrete validating experiments
4. Anticipate strongest objection
5. Define 2-week feasibility pilot

## Integration

- Called by `auto-research-pipeline` at the ideation stage
- Feeds into `novelty-check` (validate novelty of top ideas)
- Feeds into `research-brief-to-experiment-plan` (turn winner into plan)
