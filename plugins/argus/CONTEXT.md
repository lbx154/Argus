# Argus Plugin Glossary

**Argus plugin**
: The installable host package that lets Codex or Claude Code invoke Argus. It
  is an access surface, not another autonomous runtime.

**Argus runtime**
: The persistent Manager, Planner, Engineer, and Reviewer system that owns
  projects, evidence, execution, review, and completion state.

**Host**
: Codex or Claude Code, from which an operator invokes the Argus plugin.

**Argus project**
: A persistent unit of work with one execution directory and an Argus-owned
  state directory.

**Medical vertical**
: Argus's biomedical and pharmaceutical evidence workflow. It owns its own
  stage machine, role guidance, dossier checks, and independent-review gate. It
  ships in the `argus-verticals` package (`pip install argus-verticals` into the
  Argus runtime) and is selected like any other vertical. It is not a separate plugin.

**Evidence record**
: One source-addressable literature or trial record whose provenance,
  missingness, limitations, and claim relevance remain explicit.

**Target-disease dossier**
: A reviewed research package about one target and disease scope. It supports
  research decisions and is not diagnosis or treatment advice.
