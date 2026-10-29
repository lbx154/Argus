# Counterexample Lab setup

> **Outdated (checked 2026-10-22, RL-4):** the workbench page for the Counterexample Lab was
> removed from the Web UI (`948cbb4d`), and the read-only `GET /api/projects/{sid}/counterexamples`
> projection described below was removed in the TS-reference cleanup (step 1, B4). For the current workbench see
> [web-control-responsiveness.md](web-control-responsiveness.md).

The integration adds two independent capabilities without including any
private campaign data:

- a read-only Counterexample Lab in the research workbench;
- a source-update button that follows the currently checked-out published
  branch and refuses dirty, detached, or non-fast-forward updates.

## Install from source

Follow the source-checkout and virtual-environment instructions in the main
README. From a checkout containing this integration, inside that environment:

```bash
python -m pip install -e .
argus --version
argus doctor --advisor none --verify
```

## Feed the Counterexample Lab

The lab is a bounded projection of files already present in an Argus project
workspace:

```text
inputs/priority_pool.csv
outputs/results.csv
outputs/rejected.csv
parallel/<ID>/...
evidence/<ID>/README.md
research/MATH_STATE.json
```

`priority_pool.csv` supplies the rows and should contain `ID`, `题目`,
`具体描述`, `分类`, `来源等级`, and `验证级别`. A row in `results.csv` is
shown as verified; a row in `rejected.csv` is shown as rejected; files under
`parallel/<ID>` and `evidence/<ID>` advance the live construction and evidence
states. The API is read-only and caps file sizes, candidate counts, identifiers,
and recursive scans, including empty directories. Paths resolving outside the
workspace are excluded. These labels project the supplied files; they do not
grant Reviewer acceptance or complete an Argus mission.

## Update safely from the workbench

Open **Workbench → Runtime controls** and press **Pull latest version**. The updater
checks the branch currently in use, pulls only the matching branch from the
published repository with `--ff-only`, reinstalls the editable checkout when
the revision changes, and then asks for a safe cockpit/daemon restart. Local
changes, detached checkouts, unpublished branches, and divergent histories fail
closed.

Checks preserve a pending restart warning. Interrupted update workers are
reported as failed rather than remaining permanently busy. If installation
fails after a successful fast-forward, the status reports the changed source
revision and installation error; it does not claim that the checkout was
unchanged.
