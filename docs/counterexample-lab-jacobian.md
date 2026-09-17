# Counterexample Lab and Jacobian setup

The integration adds three independent capabilities without including any
private campaign data:

- a read-only Counterexample Lab in the research workbench;
- an isolated adapter to Jacobian's `math.find` and `math.run` MCP contracts;
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

## Enable Jacobian

Argus does not import Jacobian into its own process. It starts the published
`jacobian-mcp` executable as a restricted stdio sidecar, forwards only process
essentials, and preserves the operation id, request, typed output, protocol
version, and structured errors. This is process/dependency isolation, not an
operating-system sandbox; the executable retains the operator's filesystem
permissions.

Install Jacobian in its own environment according to its installation
instructions, then expose the absolute path of that environment's executable:

```bash
export ARGUS_SKILL_JACOBIAN_MCP_BIN="/path/to/jacobian-environment/bin/jacobian-mcp"
python -m argus.tools.jacobian status
python -m argus.tools.jacobian find --query "exact determinant"
```

The math Engineer and Reviewer receive the Jacobian capability note only when
the executable is available. Jacobian results are computational evidence, not
automatic proofs; Argus still requires statement alignment and independent
review.

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

Open Operations, choose Runtime, and press **Pull latest version**. The updater
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
