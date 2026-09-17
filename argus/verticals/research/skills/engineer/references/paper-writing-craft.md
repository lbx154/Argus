# The craft of writing a paper

Read this while drafting or revising a manuscript. It says how strong accepted
papers are written, so that the writer can make the same choices, not so that a
checker can count them. Nothing here is a quota. The reference standard remains
the best accepted papers at the selected venue; when this file and those papers
disagree on a surface convention, the venue wins.

## 1. Decide the paper's job before writing

A paper that designs something and measures it uses the terse empirical
register: short declarative sentences, claim-first paragraphs, measured results
asserted plainly. A position or analysis paper may argue at greater length and
hedge its reasoning, but it still states its measurements flat. Pick the
register from the paper's job and hold it; do not drift between them.

The one sentence to settle first: "This paper shows that X, because Y, as
evidenced by Z." If that sentence cannot be written, the problem is not prose.
Go back to the evidence and the thesis in the research notes, `RESEARCH_NOTES.md`.

## 2. The introduction is written twice

**Draft 0** comes first, from the thesis in the research notes, before the results section
exists in prose: the stakes, the structural gap, the key idea, a one-paragraph
mental model of the method, and the contribution claims. It is scaffolding. Its
purpose is to fix what the evaluation must establish.

**The final introduction** is rewritten from a blank page after the results
section stands. Every claim in it maps to one results subsection; the results
preview carries the actual headline numbers. If a Draft 0 promise has no
evidence, the promise goes, not the evidence. Do not polish Draft 0 into the
final version; the framing is baked into its sentences.

The six moves of a strong introduction, in order:

1. **Stakes.** Who has the problem and why it matters; open with the problem or
   domain, never with the technology.
2. **Structural gap.** Why existing approaches, named, cannot solve it: an
   assumption that fails, not merely a number that is too small.
3. **Key idea.** The paper's contribution as a named concept. If the idea has no
   name yet, it usually emerges while writing the method and results; name it
   then and use the name everywhere.
4. **Mental model.** One paragraph on how the method works, at pipeline level.
5. **Contributions.** A short numbered list, each item a claim ("We show
   that ...") with the evidence it rests on, not a process description.
6. **Results preview.** The headline numbers and the headline figure.

## 3. Results are organized by claims, and each cluster ends in a takeaway

Order the evaluation by the questions that establish the thesis, not by the
order experiments were run. A reader should be able to say of each subsection,
in one sentence, what it shows.

- **Setup**, compact: datasets, baselines, metrics, hardware, each in a labeled
  paragraph, only what reproduction needs.
- **Head-to-head** against named baselines under the metrics defined in setup.
  This is the core evidence; if it does not support an introduction claim,
  change the claim or run the experiment.
- **Disaggregation**: where the method helps most and least. Honest
  disaggregation strengthens a paper.
- **Takeaway** after each cluster: the pattern and its implication, tied to a
  contribution, readable without the surrounding numbers. Takeaways are where
  the author controls the reader's interpretation.
- **Ablation and sensitivity**: which design choices matter. If a reduced
  variant beats the full method, say so and explain it.
- **Robustness**: conditions outside the main setup, cost and overhead.

If the paper makes two different kinds of claim (it works correctly; it improves
a downstream outcome), give them separate subsections with their own baselines
and metrics.

## 4. Numbers: the prose carries the pattern, the table carries the matrix

- A number in prose is there to make a comparison. Give it the precision that
  comparison needs, usually two or three significant digits, and keep full
  precision in the table. "2.03× [1.18, 4.10]" belongs in the abstract; six
  decimal places do not.
- Prose selects the comparisons that change the inference and explains why.
  Complete matrices live in tables and the appendix. A paragraph that has
  become a list of numbers has stopped arguing; replace it with the pattern and
  a table reference.
- Every number maps to a raw result that exists. A number that cannot be traced
  is cut, not softened.
- A headline number may recur in the abstract, introduction, results, caption
  and conclusion when each place does a different job. A full result matrix
  recurs nowhere.
- Numbers replace adjectives. "2 to 4× faster", never "significantly faster";
  named mechanisms, never "a novel approach".

## 5. Confidence matches evidence, once, where it applies

- State what the evidence establishes. Assert measured results plainly; a
  measurement is not hedged.
- Hedge only a claim that outruns its evidence, and then once, at the place
  the reader needs it. Stacked hedges ("these preliminary results might
  potentially suggest") and a caveat at the end of every paragraph are not
  caution; they are noise that hides the real limits.
- Each material limit gets one precise statement with its consequence, at its
  natural location; a Limitations section, if the venue expects one, collects
  them without repeating them elsewhere.
- Write from the science, not from an imagined hostile reviewer. Repair the
  common defensive patterns:

| Defensive pattern | Repair |
|---|---|
| "To address potential reviewer concerns, we include an ablation." | "The ablation isolates the contribution of [component]." |
| "Although our method is only a simple extension, ..." | Describe the operation and the capability it provides; drop the apology. |
| "We do not claim to solve the general problem; we only study [setting]." | "We study [problem] in [setting]." |
| "It should be emphasized that these results do not guarantee ..." | State the evaluated setting once; keep a specific generalization gap only if it changes interpretation. |
| "We carefully ensure a fair and rigorous comparison." | "All methods use [the shared split and protocol]." |
| "This is non-causal / post hoc / preregistered ..." repeated per result | Say once what the design does and does not identify, then report results as results. |
| Paragraph ending on "Nevertheless, the method is not without limitations." | End on the paragraph's finding; discuss the limit where it matters. |

The manuscript never contains the terms used to organize its writing: names
for internal task limits, declarations of readiness, stage decisions,
generated outputs, missions, rounds, transfers between roles, checking tools,
inspections, or lists of required checks. The evidence-role labels (headline,
mechanism, control, scope, completeness) used while planning it also stay out.

## 6. Sentences and paragraphs

- Claim first, evidence after. A paragraph opens with its assertion and cites
  the table or figure afterward; it never opens with background.
- Active voice, present tense, direct verbs. Verbs over nominalizations: "we
  compile the plan", not "compilation of the plan is performed".
- Plain words over Latinate ones: use, show, let, enough.
- One canonical term per concept for the whole paper; do not rotate synonyms
  for variety. Define each term once, at first use, plainly.
- The load-bearing element goes at the end of the sentence; the second
  strongest position is the start.
- One idea per paragraph, three to six sentences, and the paragraph does
  exactly one of: make a claim, present evidence, state a takeaway. Delete the
  uplifting closing sentence that restates the paragraph.
- Sentence rhythm varies because the argument varies, not by rule; a run of
  identically shaped sentences reads as generated.

## 7. Headings, captions, and figures

- Headings state the section's conclusion where the venue's papers do so
  ("Stream-once screening removes the per-query traversal"); ML venues often
  prefer a method or question phrase with a colon subtitle. Either way, a
  skim-reader should recover the argument from the headings alone.
- A caption tells the reader what to see: the question the figure answers, the
  conditions that matter, and the decisive number when the number is the point
  or the pattern when the pattern is the point. It stands on its own.
- Figure 1 explains the idea or the mechanism; the first table carries the main
  result. No prose panels inside figures; numbers go on the structure they
  describe.

## 8. Related work and positioning

Name the closest work and state the actual technical difference. Do not
pre-empt an accusation of incrementalism, and do not shrink prior work to make
the contribution look larger. Ground a critique of prior work in a number or a
concrete failure case, not an assertion. Every technical claim carries a
citation or a cross-reference to where the citation lives.

## 9. Abstract and conclusion

The abstract states the problem, the insight or mechanism, the decisive
evidence, and what follows, at the length and density the venue's accepted
papers use. Two or three headline figures with their uncertainty are usually
all the numbers it needs; a large speedup is stated as a speedup, a narrow
margin with its interval, a mechanism finding perhaps with no number at all.
Scope belongs in the task statement, not in a closing disclaimer; the last
sentence lands on the contribution.

The conclusion says what was established and why it matters within the studied
setting. Future work and caveats are optional, not a closing ritual.

## 10. Compress after expanding

The first complete draft should be comprehensive. The final pass then removes
what does not serve an explicit claim, without a target reduction fraction.
Apply the manuscript-length target in `research-paper-playbook.md` for the
selected submission type, focusing expansion on principle-level analysis:
the mechanism, derivations, and design tradeoffs. When compression is needed,
remove in order:
repeated motivation, tutorial background the venue's reviewers already know,
generic adjectives, duplicate definitions, long transitions, implementation
detail that can move to the appendix, secondary analyses that can move to the
appendix, low-impact related-work sentences, caveats that duplicate the
Limitations section. Protect the problem, the mechanism, every contribution
claim and its numbers, named baselines and datasets, limits that bound the
claims, and what the venue requires for reproducibility.

## 11. Read it once as a stranger

Before compiling the final draft, read the paper as someone who has not seen
the project. Ask, and fix what fails:

- Can I reconstruct the argument from the headings, the takeaways and the
  captions alone?
- Does the introduction promise exactly what the results deliver, no more?
- Is the central finding recoverable after the first page?
- Does each number in prose do a job, and does its precision match that job?
- Is every hedge attached to a specific claim that needs it, and is each limit
  stated once?
- Is one term used for each concept from title to conclusion?
- Is there any sentence a reviewer at this venue would read as written for a
  process rather than for a reader?
