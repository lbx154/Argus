#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
source "$here/config.env"

slot=${1:?slot 0 or 1 required}
comp=${2:?competition id required}
if [[ "$slot" != 0 && "$slot" != 1 ]]; then
  echo "slot must be 0 or 1" >&2
  exit 2
fi

public="$DATA_ROOT/$comp/prepared/public"
private="$DATA_ROOT/$comp/prepared/private"
if [[ ! -d "$public" || ! -d "$private" ]] \
   || [[ -z "$(find "$public" -mindepth 1 -maxdepth 1 -print -quit)" ]] \
   || [[ -z "$(find "$private" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "competition is not prepared: $comp" >&2
  exit 3
fi

project="$CAMPAIGN_ROOT/projects/$comp"
mkdir -p "$project/data" "$project/logs" "$CAMPAIGN_ROOT/grades"
sed -e "s/__COMPETITION__/$comp/g" -e "s/__GPU__/$slot/g" \
  "$here/objective.template.md" > "$project/OBJECTIVE.md"
sed -e "s/__COMPETITION__/$comp/g" -e "s/__GPU__/$slot/g" \
  "$here/AGENTS.template.md" > "$project/AGENTS.md"

copilot_home=/root/.copilot
minor=$(nvidia-smi -q | awk -F: '/Minor Number/{gsub(/[[:space:]]/, "", $2); print $2}' | sed -n "$((slot + 1))p")
if [[ -z "$minor" || ! -e "/dev/nvidia$minor" ]]; then
  echo "cannot resolve device node for GPU slot $slot (minor=$minor)" >&2
  exit 4
fi

bwrap_args=(
  --die-with-parent --new-session --unshare-pid --unshare-ipc --unshare-uts
  --ro-bind / /
  --proc /proc
  --dev /dev
)
for device in /dev/nvidiactl /dev/nvidia-uvm /dev/nvidia-uvm-tools /dev/nvidia-modeset "/dev/nvidia$minor"; do
  [[ -e "$device" ]] && bwrap_args+=(--dev-bind "$device" "$device")
done
[[ -e /dev/nvidia-caps ]] && bwrap_args+=(--dev-bind /dev/nvidia-caps /dev/nvidia-caps)

run_log="$project/logs/argus.log"
started_epoch=$(date +%s)
set +e
timeout --signal=TERM --kill-after=300 "$TASK_TIMEOUT_SECONDS" \
  bwrap "${bwrap_args[@]}" \
    --tmpfs /tmp \
    --tmpfs "$CAMPAIGN_ROOT" \
    --dir "$CAMPAIGN_ROOT/projects" \
    --dir "$project" \
    --bind "$project" "$project" \
    --ro-bind "$public" "$project/data" \
    --bind "$ARGUS_HOME" "$ARGUS_HOME" \
    --bind "$copilot_home" "$copilot_home" \
    --tmpfs "$DATA_ROOT" \
    --tmpfs /root/.kaggle \
    --tmpfs /root/.ssh \
    --chdir "$project" \
    --setenv HOME /root \
    --setenv COPILOT_HOME "$copilot_home" \
    --setenv CUDA_VISIBLE_DEVICES 0 \
    --setenv NVIDIA_VISIBLE_DEVICES 0 \
    --setenv PATH "/root/argus-mle-lite/argus-venv/bin:/usr/local/bin:/usr/bin:/bin" \
    --setenv ARGUS_SKILL_HOME "$ARGUS_HOME" \
    --setenv ARGUS_SKILL_SPECIAL_PROMPTS_DIR "$ARGUS_HOME/special_prompts" \
    --setenv ARGUS_SKILL_SOURCE_ROOT "$ARGUS_REPO" \
    --setenv ARGUS_SKILL_LIFE_BACKEND copilot \
    --setenv ARGUS_SKILL_RUNNER_BACKEND copilot \
    --setenv ARGUS_SKILL_MODEL "$MODEL" \
    --setenv ARGUS_SKILL_ENGINEER_REASONING_EFFORT xhigh \
    --setenv ARGUS_SKILL_REVIEWER_REASONING_EFFORT high \
    --setenv ARGUS_SKILL_PLANNER_REASONING_EFFORT xhigh \
    --setenv ARGUS_SKILL_MANAGER_REASONING_EFFORT xhigh \
    --setenv ARGUS_SKILL_CROSS_PROJECT_PROPAGATION 1 \
    --setenv ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING 1 \
    --setenv ARGUS_SKILL_MAX_ACTIVE_DAEMONS 2 \
    --setenv ARGUS_SKILL_REQUIRE_RELEASE_MATCH 1 \
    "$ARGUS_BIN" --daemon-fg --continuous --bounded --new --backend copilot \
      --objective "$(cat "$project/OBJECTIVE.md")" \
    >"$run_log" 2>&1
argus_rc=$?
set -e
elapsed_seconds=$(( $(date +%s) - started_epoch ))
printf '%s\n' "$argus_rc" > "$project/argus.exit_code"

submission="$project/submission.csv"
grade_log="$CAMPAIGN_ROOT/grades/reviewer-approved-history.jsonl"
grade_rc=92
if [[ -s "$submission" ]]; then
  grade_rc=93
  # The detached watcher grades only new hashes accepted by Reviewer. Give it
  # a short final window to record the terminal reviewed candidate.
  for _ in $(seq 1 12); do
    set +e
    python3 - "$grade_log" "$comp" "$submission" <<'PY'
import hashlib,json,sys
history,competition,submission=sys.argv[1:]
h=hashlib.sha256()
with open(submission,'rb') as f:
    for chunk in iter(lambda:f.read(1024*1024),b''):
        h.update(chunk)
digest=h.hexdigest()
try:
    rows=[json.loads(x) for x in open(history) if x.strip()]
except OSError:
    raise SystemExit(1)
ok=any(
    row.get('competition')==competition
    and row.get('submission_sha256')==digest
    and row.get('report') is not None
    for row in rows
)
raise SystemExit(0 if ok else 1)
PY
    checked=$?
    set -e
    if [[ "$checked" -eq 0 ]]; then
      grade_rc=0
      break
    fi
    sleep 5
  done
fi
printf '%s\n' "$grade_rc" > "$project/grade.exit_code"

python3 "$here/result_contract.py" \
  "$project/run-result.json" "$comp" "$slot" "$argus_rc" "$grade_rc" \
  "$submission" "$grade_log" "$elapsed_seconds"
exit 0
