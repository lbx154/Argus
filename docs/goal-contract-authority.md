# Goal contract authority

A clause has two independent properties:

| Field | Values | Meaning |
|---|---|---|
| `kind` | `precise`, `semantic` | Whether checking it is mechanical or requires judgment |
| `authority` | `operator`, `manager` | Who may change the requirement |

For example, “keep unpublished source material private” can be a semantic,
operator-owned requirement. A numerical exploration parameter can be precise
and manager-owned. The Manager may refine its own working parameters within
the operator's boundaries; checkability never grants that permission.

```python
boundary = make_clause("semantic", "Use only approved data sources")
working = make_clause("precise", "Explore in batches of 8", authority="manager")
```

`operator` is the default. Legacy clauses without authority also load as
operator-owned, without rewriting the file or changing clause ids. Existing
semantic working parameters should be explicitly delegated before autonomous
revision. That delegation requires the same specific confirmation as removing
an operator-owned requirement.

Changes to operator-owned clauses, the objective, and explicit exclusions need
a confirmation bound to the changed ids and current contract revision. Use
`confirmation_changes()` to enumerate them, including exclusion ids. The
operator front door already uses that path for authorized new instructions;
it does not ask the operator to confirm the same instruction again.

Ambiguities remain editable unanswered questions. They do not authorize a
change to a requirement. Stored clauses and role briefings preserve authority
separately from verification kind.
