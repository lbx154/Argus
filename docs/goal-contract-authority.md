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

`operator` is the default for new clauses. For compatibility, legacy files
without authority preserve the previous precise/operator and semantic/manager
mapping only during loading. This does not rewrite files or change clause ids;
it also does not retroactively infer ownership of old qualitative requirements.
Record explicit authority when migrating them. Once authority is present,
checkability never changes it. Unknown explicit authority remains operator-owned.
Delegating an operator-owned clause to the Manager requires specific confirmation.

Changes to operator-owned clauses, the objective, and explicit exclusions need
a confirmation bound to the changed ids and current contract revision. Use
`confirmation_changes()` to enumerate them, including exclusion ids. The
operator front door already uses that path for authorized new instructions;
it does not ask the operator to confirm the same instruction again.

Ambiguities remain editable unanswered questions. They do not authorize a
change to a requirement. Stored clauses and role briefings preserve authority
separately from verification kind.
