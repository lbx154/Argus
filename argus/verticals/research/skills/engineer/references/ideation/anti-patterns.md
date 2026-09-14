# Compositions that have more often led to rejected papers

These findings call for **care, not prohibition**. The 1,891-paper multi-label tagging identifies three 2-way pattern compositions whose Oral rate $p_O = n_O / (n_O + n_R)$ is at least 12 percentage points below the dataset baseline ($p_O = 58.4\%$). They are not forbidden — sometimes a problem calls for one — but Phase 3.2's `anti_pattern_check` must explicitly confirm that the documented failure mode has been addressed before such a composition proceeds.

This is the **only place the historical acceptance prior enters the design**. Use it when examining a proposed composition for structural risks, not when generating ideas.

## The three 2-way compositions associated with rejection

The selection criterion is $n_O + n_R \geq 30$ AND $\Delta p_O \leq -12\,\text{pp}$ (i.e. $p_O \leq 46.4\%$). Three compositions meet it; **all three involve `heterogeneous_decomposition`** as one component. The evidence suggests that pairing "decompose heterogeneity for differentiated treatment" with another constructive move adds two ad-hoc choices that must justify each other.

| Composition | $n_O$ | $n_R$ | $p_O$ | $\Delta p_O$ | Failure mode | Required mitigation if used |
|---|---|---|---|---|---|---|
| `heterogeneous_decomposition` + `self_supervised_signal_engineering` (`audit_decomp_supervisor`) | 17 | 36 | 32.1% | −26.3 | "We made up the groups AND made up the labels." Decomposing into sub-populations is one un-derived choice; manufacturing supervision for each sub-population is a second un-derived choice; the paper is reviewed as two stacked heuristics, neither of which is testable independently of the other. By a wide margin the dataset's strongest reject signal. | The decomposition criterion must be derivable from observed structure or task-level supervision (not just intuition), AND the manufactured signal must be the *unique* signal the decomposition implies. An ablation that holds the decomposition fixed and varies the signal (or vice versa) must show the two are not conflated. |
| `heterogeneous_decomposition` + `structural_prior_encoding` (`audit_decomp_prior`) | 42 | 51 | 45.2% | −13.2 | "The prior masks the decomposition, or the decomposition masks the prior — which is doing the work?" Encoding a structural prior on top of a heterogeneous-decomposition pipeline asks the prior to handle both the homogeneous core and the inter-group differences; reviewers cannot tell which axis carries the contribution. The highest-volume reject-favored combination in the corpus ($n_{O+R} = 93$). | An ablation that turns off either the decomposition or the prior in isolation must show a clear differential effect (not just a smaller combined number). Theoretically, the prior must encode a property the decomposition does not already imply — otherwise the two are doing the same job. |
| `architectural_operator_substitution` + `heterogeneous_decomposition` (`audit_decomp_operator`) | 18 | 21 | 46.2% | −12.2 | "The operator IS the architecture." Substituting a more expressive operator and asking it to handle multi-population heterogeneity stacks two distinct tradeoffs in one design choice — the operator's inductive bias is being asked to deliver both the homogeneous-data gain and the cross-group differentiation. Reviewers consistently say the contribution is over-attributed to a single change. | The operator's expressivity gain must be demonstrated on homogeneous data (where decomposition is unnecessary), and the decomposition's separate effect must be demonstrated on heterogeneous data with the operator held fixed. Each leg's contribution must be independently identifiable. |

## How to examine a composition in Phase 3.2

In Phase 3.2, carry out `anti_pattern_check` as follows:

1. Form `composition_set` from the set of `main_pattern` values across the candidate's `gap_closure[]` entries.
2. Compare each 2-way subset with the 3 documented compositions in the table above.
3. If a subset matches, record `matched_pattern_id` (`audit_decomp_supervisor` | `audit_decomp_prior` | `audit_decomp_operator`) and the corresponding `required_mitigation`. Then judge whether the candidate's `core_mechanism` (and supporting fields like `theoretical_leg` / `engineering_leg`) substantively addresses the problem, and record `mitigation_substantively_delivered: true | false`.
4. If a composition matches, the problem is not addressed, and Phase 3.3 revision cannot address it, the candidate must be abandoned: record `decision = abandon`. Otherwise the candidate may proceed with the explanation recorded in `reviewer_concerns_and_responses`.

The remedy must be **visible in the candidate's `core_mechanism`**, in what the proposed method actually does. Claiming it in the framing or mentioning the right words is not enough.

## How to interpret the evidence

- **These compositions can succeed.** The data shows that papers using them receive Oral recognition 32–46% of the time. The prior indicates risk; it does not decide whether the proposed idea holds.
- **The prior does not guide generation.** Phase 2 selection never proposes a composition because of its acceptance prior. Consult the prior only after the candidate is generated, during Phase 3.2, to check for the documented failure mode.
- **Not the only source of reviewer risk.** Phase 3.2's `gap_closure_reject_check` checks per-cluster (sub-pattern) reject lessons. Anti-patterns are a level above: composition-level failure modes that aren't visible in any single sub-pattern card.
- **Not exhaustive.** Five additional combinations at $-11\,\text{pp} \leq \Delta p_O \leq -8\,\text{pp}$ are mildly reject-favored; they are watch-list candidates but the empirical signal is weaker than the three flagged above. Notably, `heterogeneous_decomposition + reframe_as_solvable_object` ($p_O = 47.4\%$, $n = 95$) just misses the threshold and may warrant inclusion as the corpus grows.
