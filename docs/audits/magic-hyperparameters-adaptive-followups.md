# 待改为自适应的硬编码参数(129 处,审计原始数据见同目录 JSON)

按文件排序。每条:位置 · 符号=值 · 触发后果 · 建议。

- `argus_skill/adapters/agent_cli_backend/_core.py:43` · _RepeatedToolCallGuard limit = 3  
  后果:The turn is killed. Legitimate identical polling — repeatedly running the same job-status command while a GPU job runs — is indistinguishable from a livelock and gets terminated.  
  建议:A livelock breaker is load-bearing in kind, but 3 identical calls is aggressively low. Require identical call AND identical result over a larger streak, or exempt known polling idioms, so healthy wait-and-check loops survive.
- `argus_skill/agent_cli/_run_exec.py:50` · _ORPHAN_GROUP_DETACH_GRACE_SECONDS = 0.5  
  后果:Under scheduler pressure a legitimate durable child (e.g. a just-launched GPU job) that has not yet executed setsid is classified as a leaked orphan and SIGTERMed — silently killing intended long-running work.  
  建议:The 0.5s window races the OS scheduler. Re-scan group membership over a few short intervals and only sweep members that persist, or verify the child's start command against tracked external work, instead of one fixed grace.
- `argus_skill/agent_cli/copilot_acp.py:45` · _DEFAULT_SESSION_RECYCLE = 12  
  后果:The 12th call pays session re-setup latency and loses in-session context; bounds unbounded resume-cost growth.  
  建议:The right trigger is measured context size or per-call latency/cost climbing past a threshold, not a fixed call count; 12 is a pure judgment call, though env-overridable.
- `argus_skill/apps/_runtime_construction.py:206` · default_watchdog_stalled_idle_seconds (ARGUS_SKILL_RUNNER_STALLED_IDLE_SECONDS fallback) = 1800 (30*60)  
  后果:The session is flagged stalled and stall-handling kicks in, even if a legitimate long GPU/compile step is simply quiet on the event stream.  
  建议:For a daemon that launches multi-hour GPU training, 30 minutes of event silence is common while a foreground training script runs. Stalledness should be judged by process/GPU liveness (child process alive, GPU utilization, log file mtime) rather than event-stream silence; note the hard-kill tier correctly defaults to 0 (disabled), which is the right pattern.
- `argus_skill/apps/_self_reply.py:34` · _SELF_LEARNING_REVIEW_INTERVAL = 5  
  后果:Below the threshold, no self-learning review runs; potential lessons from recent chats wait.  
  建议:A fixed 5-turn cadence is a judgment call about when enough new material exists; the trigger could instead be content-driven (e.g. review when idle, or when the accumulated un-reviewed turns exceed a token budget) — though the cost of the current value is low.
- `argus_skill/apps/_self_reply.py:817` · watchdog_hard_idle_seconds (ARGUS_SKILL_SELF_HARD_IDLE_SECONDS fallback) = 120  
  后果:The SELF turn is killed mid-flight; the operator's chat message gets an error path instead of an answer.  
  建议:Idle-based (liveness-style) rather than wall-clock, which is right, but 120s is tight for a SELF turn that legitimately runs a slow shell command or a long reasoning stretch with buffered output. Should scale with the execution contract chosen for the turn (micro vs implement) or at minimum match the runner-wide idle defaults.
- `argus_skill/apps/cli/_parser.py:238` · --mission-width default = 2  
  后果:Out of the box, at most 2 missions run concurrently regardless of machine capacity or how parallelizable the planner's task graph is.  
  建议:Width is a capacity judgment: on the operator's 4×A6000 box with disjoint tasks, 2 leaves hardware idle; on a laptop it may thrash. A default derived from available resources (GPU count, provider concurrency budget) fits the philosophy better than a fixed 2 — mitigated by being a visible, documented CLI default rather than a buried constant.
- `argus_skill/core/agent_probe.py:93` · run_read_only_agent_prompt watchdog defaults (soft/stalled/hard idle) = 15 / 45 / 90 seconds  
  后果:At 90s idle the probe's provider process is killed and setup/readiness verification reports the backend as not ready. A slow-but-healthy backend (e.g. a reasoning model with long time-to-first-token) can false-fail setup.  
  建议:The code comment is honest ('bounds a one-shot readiness probe, not a production role turn') and some bound is needed so a wedged CLI cannot hang setup forever, but 90s is a guess about provider latency. Deriving the hard tier from observed backend first-token latency (or retrying once with a longer bound) would remove false 'not ready' verdicts without unbounding the probe.
- `argus_skill/core/knobs.py:148` · ARGUS_SKILL_SUBAGENT_FAMILY_FAILURE_WINDOW_HOURS default = 72.0 hours  
  后果:A slow-failing family (e.g. multi-day GPU jobs) may never accumulate a streak, or a fast-failing one trips quickly; the window shape decides which failures 'count'.  
  建议:Unlike the streak count, the window duration should track observed job duration: 72h is 3+ streak-triggering failures for hour-long jobs but can be under one attempt for multi-day training runs, silently disabling the circuit breaker exactly where failures are most expensive. Deriving the window from the family's own attempt durations (e.g. N times median runtime) would make the streak semantics uniform.
- `argus_skill/core/metrics.py:480` · SLO evaluation thresholds (lines 480-489) = provider: attempts>=5 and success<0.95; command: attempts>=3 and success<0.98; web 5xx rate > 0.01  
  后果:A violation line appears in the SLO report — alerting/observability only; no work is gated, paused, or killed.  
  建议:These five numbers (5, 0.95, 3, 0.98, 0.01) are pure judgment calls about what 'unhealthy' means, buried in code rather than the knob registry, so an operator cannot tune alert sensitivity without a code change. Consequence is only report noise, so no urgency — but they belong in the knob registry or derived from trailing baselines rather than hardcoded.
- `argus_skill/core/mission_view/_view_state.py:25` · MISSION_BOOTSTRAP_MAX_BYTES = 8 MiB  
  后果:For a long campaign whose event log exceeds 8 MiB, everything earlier is silently excluded from the rebuilt view — cumulative fields reduced from events (counters, role histories, early milestones) can be permanently wrong in the cockpit after a rebuild, with no truncation flag.  
  建议:This is the one mission-view cap where truncation is both silent and potentially semantic: a rebuild is supposed to reproduce the view, and the tail-read quietly changes reduction results once the log outgrows 8 MiB — exactly on the long campaigns Argus is built for. Streaming the whole file (reduction is line-at-a-time; memory need not scale with file size), or at minimum stamping the rebuilt view with a bootstrap_truncated flag, would remove the silent divergence.
- `argus_skill/core/research_contract.py:239` · normalize_research_result evidence/limitations caps (lines 237-254) = [:12] items, [:500] chars each  
  后果:Evidence items beyond the 12th vanish from the normalized record that downstream completion checks and the certified result are built on — the scientific record of what supports a claimed result is silently thinned.  
  建议:Evidence lists are the load-bearing scientific artifact here, and research_completion_issue gates on result['evidence'] being non-empty — the caps affect certified content, not display. A rich result with 20 evidence pointers loses 8 with no flag. The bound should be generous and overflow should be flagged or spilled to the evidence ledger, not discarded; 12/500 encodes an unstated judgment about how much evidence a result may have.
- `argus_skill/core/role_session.py:100` · repository map entry cap (and checkpoint open-items cap at line 130) = [:80] workdir entries / [:20] open questions  
  后果:In a large workdir, files past the 80th sorted entry never appear in the role's map (sorted order means late-alphabet files are systematically invisible); open questions beyond 20 are dropped from the handoff.  
  建议:Both caps ration prompt space, so the honest bound is token-budget-derived per model rather than fixed counts; the sorted-prefix repository listing additionally has a systematic bias (alphabetical survivorship) that a smarter selection (recently modified, or role-relevant) would fix. Silent loss of open questions is planner-memory decay, same family as the frontier caps.
- `argus_skill/core/role_session.py:295` · role session capsule text truncations (lines 295, 309, 324, 401, 436) = [:2000] / [:1000] chars  
  后果:Tail of long decisive outputs is silently cut from cross-turn memory; the full text exists only in the transcript.  
  建议:Same context-rationing family as the frontier text caps: fixed character counts stand in for a token budget the system already knows per model. Low urgency (transcript retains the full text) but the truncation is silent where a marker or summary would be nearly free.
- `argus_skill/core/secret_guard.py:187` · _MAX_ARTIFACT_BYTES = 32 MiB  
  后果:Secrets embedded in a >32 MiB text artifact are NOT redacted before the artifact is shared/published — a security-coverage gap, though the report flags that the scan was truncated.  
  建议:The cap exists because the file is read wholly into memory; scanning in streamed chunks (with overlap for boundary-straddling matches) removes the need for any size cap on the security-critical path. Until then the flag prevents silent failure, but a hardcoded ceiling on secret-scan coverage is the wrong place to economize.
- `argus_skill/core/task_frontier.py:55` · _merge cumulative list cap (also per-field limit=80 in from_dict, lines 140-145) = limit=80, keeps last 80 ([-limit:])  
  后果:On a long campaign the oldest unresolved obligations and evidence references are silently forgotten — planner-visible research memory decays with no flag and no recovery path in the frontier itself.  
  建议:This cap erodes exactly the state Argus's months-long campaigns depend on: an obligation dropped at item 81 is an open commitment the planner will never see again, and the loss is silent. The bound should derive from what the consuming prompt can carry (token budget of the planner context), with overflow spilled to a durable side ledger or summarized rather than discarded.
- `argus_skill/core/task_frontier.py:81` · frontier text field truncations (lines 81-85, 192-198, 214, 220-221) = [:1000] / [:2000] chars  
  后果:Text beyond the cap is silently cut from planner-visible memory — a long hypothesis or uncertainty statement loses its tail with no marker.  
  建议:These fields are the model's working memory across turns, so the right bound is a function of the consuming model's context budget, not a fixed 1000/2000. Silent truncation of mid-sentence reasoning is worse than either a visible '[truncated]' marker or a summarize-on-overflow step.
- `argus_skill/core/task_frontier.py:158` · frontier transition history cap (also line 225) = history[-100:]  
  后果:Transitions older than the last 100 are silently dropped from frontier state; early-campaign reasoning history becomes unreachable through the frontier.  
  建议:Same silent-memory-decay pattern as the _merge cap, milder because history is more audit trail than active planning input; long campaigns exceed 100 transitions. Deriving from consumer needs, or archiving evicted records instead of discarding, fits the system's otherwise lossless posture (events.jsonl, gzip history).
- `argus_skill/core/vault_preflight.py:46` · DEFAULT_PROBE_TIMEOUT_S (also CLI --timeout default at lines 303-306) = 10.0 seconds  
  后果:The route is marked not alive; if it is a required route, preflight fails and the daemon refuses to start. A loaded-but-healthy endpoint that takes >10s to answer a 16-token completion false-fails boot.  
  建议:This is a wall-clock timeout whose false positive blocks legitimate work (daemon start) — the worst-offender class under the operator philosophy. Preflight runs once at boot where seconds are cheap: retrying with escalating timeouts (10s, 30s, 60s) before declaring a route dead would eliminate false boot failures while still catching black-holed endpoints.
- `argus_skill/daemon/_life_worker_admission.py:44` · _CLEAN_LAUNCH_TIMEOUT_SECONDS = 15.0  
  后果:TimeoutExpired is treated as a failed spawn (rc 2); the mission does not start.  
  建议:On a loaded box (cold FS cache, Python import storms) a healthy interpreter launch can exceed 15s, turning machine load into spurious spawn failures. Scale with observed launch latency or check that the probe process is alive and making progress instead of a fixed wall clock.
- `argus_skill/daemon/_life_worker_admission.py:45` · _CLEAN_LAUNCH_WINDOWS_MARGIN_SECONDS = 15.0  
  后果:Same as the base timeout: probe declared failed, spawn aborted, at 30s total on Windows.  
  建议:Same wall-clock-versus-load problem as the base constant; the fixed +15s acknowledges Windows is slower but still encodes a guess.
- `argus_skill/daemon/_life_worker_runtime_context.py:70` · ARGUS_SKILL_MAX_ROUNDS fallback = 0 (unbounded)  
  后果:With the default, a reviewer that keeps answering 'continue' loops indefinitely; only the daily budget cap eventually fences the burn.  
  建议:This is the one place the codebase lacks a loop-stopper where the philosophy says one is usually load-bearing. A generous finite default, or a progress-based stopper (no diff/decision change across N rounds), would end silent infinite loops well before the budget fence.
- `argus_skill/daemon/_life_worker_runtime_context.py:191` · poll_interval_seconds (supervisor config) = 2.0  
  后果:Operators tuning poll_interval in daemon config silently do not affect the supervisor loop.  
  建议:A config knob already exists for exactly this; the literal should be replaced with cfg.poll_interval so one setting governs both loops.
- `argus_skill/daemon/config.py:144` · mission_width fallback = 2  
  后果:At most 2 missions execute in parallel; backlog beyond that serializes.  
  建议:Fixed 2 encodes a guess about machine and provider-quota capacity; could derive from cores, quota headroom, or budget while keeping the config override.
- `argus_skill/daemon/foreground_waits.py:167` · ForegroundWaitGuard.minimum_age_seconds = 15.0  
  后果:A wait shell the agent deliberately started is killed roughly 15-20s in; the agent's foreground command returns killed and the turn is expected to yield. A legitimate short sleep-and-recheck loop the agent chose is terminated.  
  建议:The mechanism itself protects turns from blocking on local polls while durable external work runs, but 15s is a fixed judgment call barely three scan intervals long. Scale the threshold with interval_seconds and require the same wait idiom observed on consecutive scans before terminating.
- `argus_skill/daemon/handoff.py:119` · standby_timeout = 30.0  
  后果:A healthy but slow-starting candidate (cold caches, loaded box) is killed and the deployment handoff fails; the old runtime keeps running.  
  建议:A wall-clock gate on legitimate startup work. A liveness/progress check on the candidate (process alive and importing, standby file partially advanced) or a threshold scaled to observed startup times would avoid killing slow-but-healthy candidates. Failure mode is at least safe (rollback, incumbent survives).
- `argus_skill/daemon/life_worker.py:334` · ARGUS_TEAM_DEFAULT_WIDTH fallback = 8  
  后果:Team missions spawn up to 8 concurrent teammates — a concurrency and cost policy baked into a fallback string.  
  建议:Eight parallel LLM-driven teammates is a large cost/capacity judgment to hardcode; derive from budget headroom, provider quota, or mission_width instead of a fixed literal.
- `argus_skill/daemon/process.py:31` · _DAEMON_PUBLISH_TIMEOUT_SECONDS = 5.0  
  后果:The launcher reports failure rc 2 although the grandchild may still come up — a false negative that can trigger operator/automation retries (double-start is fenced by the pid lock).  
  建议:5s of wall clock stands in for 'did the daemon start', which the status file plus process liveness answers directly; on a loaded machine startup routinely exceeds 5s and the launcher lies.
- `argus_skill/daemon/process.py:32` · _WINDOWS_DAEMON_PUBLISH_TIMEOUT_SECONDS = 180.0  
  后果:A daemon slow to publish (AV scanning, cold NTFS) is killed at 180s — startup work destroyed, not just misreported.  
  建议:This one kills rather than misreports, making the fixed wall clock genuinely destructive; check that the worker process is alive and progressing instead, or make the kill contingent on liveness failure.
- `argus_skill/engineer/external_work.py:50` · ExternalWorkRecord.stale_after_seconds default (also fallback at line 232) = 1800.0  
  后果:The work is reported as stalled to the reviewer/settlement; the mission stops treating it as healthy waiting, so stall counting resumes and the mission can be retired while the underlying GPU job may in fact still be running with a silent heartbeat writer.  
  建议:A fixed 30-minute default misclassifies any legitimate job whose wrapper heartbeats less often (long epochs, checkpoint-only touch). The subagent path already derives staleness adaptively at line 291 (max(monitor_interval, cap) * 2); the heartbeat default should similarly derive from the record's declared poll cadence.
- `argus_skill/engineer/external_work.py:78` · value[:32] (activity/evidence path list cap) = 32  
  后果:Paths beyond the 32nd are never checked for mtime activity, so a job whose only recent writes land in dropped paths looks silent and can be classified stalled despite being alive.  
  建议:The cap protects against a runaway registry entry, but silently discarding liveness inputs can produce false stalls on legitimate long jobs — the worst-offender failure mode. At minimum log/flag the drop; better, dedupe to directories or watch the newest N by mtime.
- `argus_skill/engineer/round_config.py:38` · _CONTINUE_WORK_MAX_CHARS = 500  
  后果:The engineer's explicit request to continue working is silently discarded — the round settles as if no continuation was requested, purely because the engineer wrote a verbose next step.  
  建议:Rejecting the whole request over length is a judgment call encoded as a magic number; truncating the text (or accepting with a marker) preserves intent while still bounding prompt size. Silent drop of a control signal is the smell.
- `argus_skill/engineer/round_config.py:272` · SupervisedConfig.backend_failure_threshold = 2  
  后果:After just 2 consecutive backend failures the whole mission errors out — even though the expensive engineer work already completed and the failure is transient infrastructure.  
  建议:Failing loud instead of reviewing blind is correct, but 2 attempts spaced 15s apart means a ~30-second API blip can abandon hours of completed engineer work. Retry budget should scale with the cost of the work being settled (e.g., more attempts with growing backoff before surrendering).
- `argus_skill/engineer/round_config.py:273` · SupervisedConfig.backend_failure_backoff_seconds = 15.0  
  后果:Retries hammer a failing backend every 15s and exhaust the tiny threshold quickly during any outage longer than ~30 seconds.  
  建议:Flat 15s backoff paired with threshold 2 encodes 'give up after 30 seconds of outage'. Exponential backoff with a cap (or jittered retry over minutes) would survive routine transient outages without burning extra LLM calls.
- `argus_skill/engineer/round_config.py:280` · role_session_max_turns env fallback (ARGUS_SKILL_ROLE_SESSION_MAX_TURNS) = 6  
  后果:The role session is discarded and rebuilt from the static rubric, losing conversational continuity, purely on turn count.  
  建议:Turn count is a weak proxy for the thing actually being bounded (context size); the companion role_session_max_input_tokens knob already measures that directly. Recycling should key off the token budget, making the turn count redundant.
- `argus_skill/engineer/round_execution.py:460` · min(configured_threshold, 2) (hard-idle watchdog retry cap) = 2  
  后果:A turn that is legitimately quiet (long in-turn computation with no streamed output) gets at most 2 retries and then fails the round, even if the operator configured a larger retry budget.  
  建议:Silently overriding explicit operator configuration with a hardcoded 2 is the design smell the audit targets; the clamp's intent (don't replay an expensive turn that keeps idling) is sound, but the cap should respect config or be its own named, configurable knob.
- `argus_skill/engineer/round_settlement.py:436` · soft_limit_stalled two-verdict window (len(state.rounds) >= 2 and state.rounds[-2:]) = 2  
  后果:Escalation semantics engage after only 2 non-affirming verdicts, including verdicts that merely omitted FORWARD_PROGRESS, a stricter and differently-shaped test than the configured 4-strike explicit-false stall_threshold.  
  建议:Two stall definitions now coexist: the configured explicit-signal streak and this hardcoded absence-of-affirmation window. Tying the window to the config (or reusing the semantic streak counter) removes a hidden second threshold that can surprise operators who tuned stall_threshold.
- `argus_skill/life/context_packet.py:113` · _brief_text default limit = 600  
  后果:A reviewer's 'why this round failed' or the acceptance check is silently cut mid-sentence in the brief the next round is steered by.  
  建议:These fields carry the decisive round-to-round signal in a long campaign; silent mid-sentence truncation of a review reason can misdirect the next round. Cap should come from the brief's overall budget and truncation should be marked (an ellipsis at minimum).
- `argus_skill/life/context_packet.py:117` · _brief_items default limit (also resources[:6] at line 200) = 6 items (each item capped at 400 chars)  
  后果:A frontier with more than 6 remaining-work items shows only the first 6; the continuation round does not learn the others exist.  
  建议:Dropping remaining-work items without a '(+N more)' marker can make a continuation believe the missing conditions are fewer than they are. Either mark the elision or size the list from the brief budget.
- `argus_skill/life/context_packet.py:208` · engineer_summary brief cap (with the stored-handoff cap [:4000] at lines 366/422) = 1200 chars in MissionBrief; 4000 chars persisted per round handoff  
  后果:The 4000-char cut is permanent (the handoff file is the durable record); the 1200-char cut silently hides the tail of the prior round's account from the fresh continuation the docstring says should not have to infer work.  
  建议:The whole point of carrying the Engineer account (per the module docstring) is that a fresh continuation should not re-infer prior work; a fixed 1200-char slice of a 4000-char stored account undermines that silently. Derive both from the consuming prompt budget and mark truncation.
- `argus_skill/life/delivery.py:19` · MAX_DELIVERY_TARGETS = 6  
  后果:Reviewer-named evidence files or declared vertical deliverables beyond 6 do not appear in the completion receipt the operator opens.  
  建议:The candidate set is already bounded by construction (only reviewer-named artifacts and contract-declared deliverables, deduplicated, safety-filtered) — an arbitrary 6 on top of that silently hides evidence. If a bound is wanted for phone rendering, let the render surface decide how many to show and keep the receipt complete.
- `argus_skill/life/delivery.py:278` · receipt summary cap (str(summary).strip()[:1200]) = 1200  
  后果:The operator-facing summary of a completed campaign task is silently cut.  
  建议:Receipt fields are consumed by chat surfaces that already split long messages; a fixed silent cut is unnecessary. Minor compared to the targets cap, but the same silent-truncation pattern.
- `argus_skill/life/failure_experience.py:27` · _DEFAULT_SCAN_BYTES = 1_000_000 (1 MB)  
  后果:Older failure capsules silently fall out of the retrievable memory — they remain on disk but the direct/transfer/analogy channels can never surface them, so hard-won lessons age out by byte offset, not relevance.  
  建议:Bounding a per-prompt scan is reasonable, but the bound should track the store (scan enough bytes to cover a target number of capsules, or maintain a compact index) rather than a fixed megabyte that means '~250 capsules' today and 'last week only' once capsules grow. For a system whose pitch is learning from failures, a silent 1 MB horizon on that learning is a real policy choice.
- `argus_skill/life/failure_experience.py:28` · _DEFAULT_SCAN_RECORDS = 256  
  后果:Same silent horizon as the byte cap, in record units; combined with annotation rows (which share the budget) the effective experience pool can be much smaller than 256.  
  建议:Same reasoning as _DEFAULT_SCAN_BYTES — the two caps jointly define how far back failure memory reaches, and annotations consuming record slots makes the effective window unpredictable. Derive from desired candidate count.
- `argus_skill/life/failure_experience.py:316` · retrieve max_entries default (and hardcoded candidate pool of 64 at line 326) = 4 capsules injected; 64-candidate pool  
  后果:Only 4 prior failures inform a mission regardless of how many are relevant; the pool for scoring is the newest 64.  
  建议:The 4-slot channel mix is a thoughtful design, but both numbers encode 'how much failure memory a mission gets' and should scale with prompt budget and store size. The code itself is careful to say facets 'never reject a new attempt', so widening this adaptively is safe.
- `argus_skill/life/failure_experience.py:399` · render_context max_chars = 6_000  
  后果:Later capsules (and possibly the tail of the current one) are silently dropped from the prompt.  
  建议:Same prompt-budget family as memory.py's 6_000 defaults (memory.py lines 2375/2899 pass the same number). One knob derived from model context should govern these, not four copies of 6_000.
- `argus_skill/life/memory.py:592` · EventJournal.tail default n = 20  
  后果:Callers relying on the default see only the 20 newest journal entries; older history exists on disk but is not surfaced.  
  建议:A read-window default that silently shapes what 'recent history' means to every default caller (status cards, layer detection). Callers with real needs already pass n; the default should be derived from the consuming surface (e.g. how many entries the prompt/display budget can hold) rather than a fixed 20.
- `argus_skill/life/memory.py:2354` · LifeMemory.recent_journal defaults (max_entries=3, recency_n=30) = 3 entries surfaced, scanning the last 30 journal events  
  后果:Everything older than the 3 newest entries is invisible to the agent's memory context — silently, with no marker that history was elided.  
  建议:How much memory an autonomous researcher gets is a consequential judgment call encoded as '3'. It should scale with the consuming model's context budget and the campaign's length (a 200-mission campaign and a 5-mission one get identical memory), or at minimum be configured alongside the other prompt-budget knobs.
- `argus_skill/life/memory.py:2391` · render_prelude identity_chars = 600  
  后果:Identity/policy text beyond 600 chars is silently cut — an operator who writes a detailed identity card has the tail dropped from every prompt with no indication.  
  建议:Silent truncation of operator-authored directives is the kind of quiet data loss the audit targets. The cap should derive from the overall prompt budget, and truncation should at least be marked so the agent (and operator) know the card was cut.
- `argus_skill/life/research_plan.py:15` · RESEARCH_PLAN_PROMPT_CHARS = 8_000  
  后果:Middle sections of the plan (typically Established results / Dead ends bodies) are cut from the Planner's view for that cycle, with an explicit marker and a self-healing instruction.  
  建议:Well-engineered truncation (visible, self-correcting, milestone-preserving), but 8000 chars is a fixed stand-in for 'the Planner's dynamic context allowance' — it should be derived from the actual model/prompt budget so a larger-context Planner sees its whole plan and a smaller one isn't overrun.
- `argus_skill/life/research_plan.py:16` · RESEARCH_PLAN_MISSION_CHARS = 2_000  
  后果:Central hypotheses and the milestone are silently shortened in the Engineer's brief — the agent doing the actual experiments may see truncated hypotheses.  
  建议:Same prompt-budget family as the Planner cap; 2000 chars for the scientific core of a mission brief is a judgment call that should scale with the mission prompt's real budget. The internal 500/limit//3 apportioning is fine mechanics once the outer number is principled.
- `argus_skill/life/role_activity.py:279` · INFLIGHT_CALL_ACTIVE_WINDOW_S = 50 * 60.0 (50 minutes)  
  后果:Display only: past the window a silent-but-alive provider turn shows as inactive/'Waiting'. No work is affected.  
  建议:The number is a hardcoded shadow of another component's timeout (45 min + 5 margin, per its own comment). If the runner's idle window changes, this silently drifts and the panel lies again — derive it from the runner's configured value instead of re-encoding it here.
- `argus_skill/life/supervisor/_constants.py:35` · _REPLAN_STREAK_JOURNAL_WINDOW = 100  
  后果:If more than 100 journal entries land between an item's replans (chatty campaign, many parallel missions), earlier replans fall out of the window and the streak silently undercounts, delaying or defeating the escalation breaker.  
  建议:The breaker's correctness depends on journal chattiness, which varies by campaign. Counting replans per item id directly (or scanning until the item's last non-replan outcome) would make the breaker exact instead of window-dependent.
- `argus_skill/life/supervisor/_core.py:1302` · delivery receipt lookback = journal.tail(80)  
  后果:If more than 80 journal entries landed after the last successful mission (long wind-down, chatty settlement), the terminal receipt silently reports no completed mission found despite one existing.  
  建议:A terminal artifact — the campaign's final receipt — should not depend on journal chattiness. Query by event type without an arbitrary window, or persist the last-success pointer as it happens.
- `argus_skill/life/supervisor/_mission_execution_settlement.py:1179` · mission_summary truncation = [:1200]  
  后果:The durable record of what a mission accomplished loses everything past 1200 chars, with no ellipsis and no full-text copy; downstream planner history rendering then truncates further to 1800 chars per entry.  
  建议:This truncation is at the storage layer, so the data is unrecoverable — unlike rendering-time caps. Store the full summary (or a pointer to it) and truncate only at render time where token budgets actually apply.
- `argus_skill/life/supervisor/_mission_execution_settlement.py:1204` · referenced_delivery_paths limit = limit=12  
  后果:A mission that produced more than 12 referenced artifacts gets a receipt listing only the first 12; the rest are silently absent from the durable delivery record.  
  建议:Delivery receipts are the campaign's proof of work; silently dropping artifacts corrupts that record. Record all paths durably and cap only rendered previews, or at minimum record an overflow count.
- `argus_skill/life/supervisor/_mission_execution_settlement.py:1282` · delivery_candidates cap = [:12]  
  后果:Same silent artifact drop as the limit=12 above, applied at a second point in the same pipeline.  
  建议:Duplicated magic cap in one flow; whatever replaces the limit=12 finding should unify both into a single named policy.
- `argus_skill/life/supervisor/_planner_orchestration.py:21` · _PLANNER_RECENT_HISTORY_WINDOW = 20  
  后果:Quarantine lifetime is measured in journal entries, not time or attempts: a chatty journal expires quarantines in minutes (re-burning LLM calls on known-dead items), a quiet one quarantines nearly forever.  
  建议:The unit is wrong, not just the number — journal chattiness varies wildly across campaigns. Quarantine should key on time and/or intervening successful missions rather than raw entry count. Also duplicated at _core.py:126 (drift risk).
- `argus_skill/life/supervisor/_planner_orchestration.py:112` · pipeline stages preview cap = sorted(stages.items())[:12]  
  后果:Stages beyond 12 are silently omitted from the digest.  
  建议:Same silent-drop pattern as the blockers cap; add an overflow count or prioritize non-terminal stages so the planner sees what is actually in flight.
- `argus_skill/life/supervisor/_planner_orchestration.py:185` · checkpoint blockers cap = len(blockers) >= 8  
  后果:Blockers beyond the first 8 are silently absent from the digest, with no '+N more' marker — the planner cannot know they exist.  
  建议:Silent truncation of exactly the information the reality-check digest exists to surface. At minimum append a '+N more' count (as changed_preview at line 190-192 already does); >8 real blockers is also itself a signal worth showing the planner.
- `argus_skill/life/supervisor/_planner_rendering.py:16` · _PLANNER_HISTORY_COUNT = 3  
  后果:The planner reasons about only the last 3 mission outcomes; older outcomes exist only via the coarse campaign tally, so patterns spanning >3 missions (alternating failures, slow drift) are invisible to it.  
  建议:This is decision-quality-critical LLM context being capped by a fixed count. Budget it in tokens against the model's window (e.g. fill up to N tokens of history) rather than a fixed 3, which was presumably chosen for a smaller context era.
- `argus_skill/life/supervisor/_planner_rendering.py:17` · _PLANNER_HISTORY_ENTRY_CHARS = 1800  
  后果:Long mission summaries lose their tail — often the reviewer verdict or final error detail, since summaries are typically chronological — degrading the planner's understanding of why a mission ended.  
  建议:Silent tail-drop of decision context. Prefer structure-aware compression (keep outcome/verdict fields whole, truncate the middle) or a token budget shared with _PLANNER_HISTORY_COUNT.
- `argus_skill/life/supervisor/_planner_rendering.py:128` · campaign tally journal window = journal.tail(4096)  
  后果:Campaigns whose journal exceeds 4096 entries get a silently understated tally; the planner believes fewer missions have run than actually have.  
  建议:Correctness of a count should not depend on a tail window. Maintain running counters incrementally (or persist the tally) so the window is only a rendering concern; long campaigns are exactly when accurate tallies matter most.
- `argus_skill/life/supervisor/_planner_rendering.py:166` · recency scan window = journal.tail(64)  
  后果:Activity older than 64 entries is treated as absent; on chatty journals the recency horizon shrinks to minutes.  
  建议:Entry-count windows conflate chattiness with time. A time-based horizon (or typed-event filter before windowing) gives stable semantics across campaigns.
- `argus_skill/life/supervisor/_planner_rendering.py:187` · context packet truncation = context_packet[:600]  
  后果:Everything past 600 chars of the packet is silently invisible to the planner — the packet exists precisely to carry decision context forward.  
  建议:600 chars is very tight for a 'context packet'; either enforce the cap where the packet is authored (so authors know the budget) or raise it to a token-budgeted share of the prompt. Silent mid-packet truncation is the worst option.
- `argus_skill/life/supervisor/_planning_context.py:1409` · operator-wait turn re-grant cadence = IDLE_BACKOFF_CAP_SECONDS (300.0)  
  后果:While operator-blocked, the planner gets a fresh turn at most every 5 minutes — but only because that happens to be the idle backoff cap.  
  建议:Constant reuse across unrelated policies: retuning idle poll latency would silently change operator-wait re-grant cadence (an LLM-call-frequency policy). Give this its own named constant even if the value stays 300.
- `argus_skill/life/supervisor/_planning_cycle_enqueue.py:90` · forward-progress lookback = memory.journal.tail(32)  
  后果:Progress older than 32 entries is invisible to the check; on chatty journals genuine recent progress can be missed and on quiet ones stale progress can look fresh.  
  建议:Another entry-count window standing in for a time/semantic horizon; same fix as the other tail() windows — filter to the relevant typed events or use a time bound.
- `argus_skill/life/supervisor/_subagent_family_failures.py:80` · failure reason truncation = [:300]  
  后果:Long failure reasons lose their tail in the durable record; distinct root causes with identical first 300 chars become indistinguishable when reviewing why a family was quarantined.  
  建议:Silent data drop from a diagnostic record. Cheap fix: keep a short prefix for display but store a hash or full reason for identity/forensics, or truncate at a size that preserves tracebacks (a few KB).
- `argus_skill/loop.py:95` · SkillLoopConfig.backend_failure_backoff_seconds = 15.0  
  后果:The loop waits a flat 15s before the retry, regardless of the failure kind.  
  建议:A flat 15s is wrong in both directions: transient socket errors clear in <1s, while provider rate limits typically state a longer retry-after. The delay should come from the error (Retry-After header / error class), not a constant.
- `argus_skill/loop.py:117` · role_session_max_turns (ARGUS_SKILL_ROLE_SESSION_MAX_TURNS fallback) = 6  
  后果:The role's conversational context is dropped and rebuilt from the context packet — accumulated in-session nuance is silently lost.  
  建议:6 turns is a proxy for 'context getting big'. The real quantity is token usage, which the sibling knob already measures; turn count should be derived from measured input-token growth rather than fixed.
- `argus_skill/loop.py:122` · role_session_max_input_tokens (ARGUS_SKILL_ROLE_SESSION_MAX_INPUT_TOKENS fallback) = 120000  
  后果:Session reset; the role restarts from the handoff packet, silently losing live conversational state.  
  建议:120k encodes an assumed model context window. The right value depends on which model the role runs (gpt-5.5 vs others) and should be derived from the active model's advertised context length, not hardcoded.
- `argus_skill/manager/_front_door_ops.py:87` · watchdog_hard_idle_seconds = 120  
  后果:The classify call is aborted; classify_front_door biases each decision axis to its safe default, so the operator turn gets degraded routing instead of the model's judgment. No mission work is lost, but a slow-but-legitimate model turn (e.g. operator overrides ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT to high and the model reasons silently for >2 min) is killed.  
  建议:It is an idle-liveness check, not a total-duration cap, so some bound is defensible — but 120 is a role-specific magic number that contradicts the codebase's own stated discipline: planner.py:274-279 deliberately uses the provider-default hard-idle watchdog because 'the provider default is the one already used by every other role, not another Planner-specific timeout'. Either defer to the provider default like every other role, or scale it with the configured reasoning effort.
- `argus_skill/manager/_helpers.py:19` · _DEFAULT_FAST_ROUTE_MIN_CONFIDENCE = 0.75  
  后果:Below 0.75 the fast decision is discarded and the Manager falls through to the grounded route — an extra, more expensive LLM call. No data loss; pure cost/latency policy.  
  建议:0.75 is a judgment call about a model's self-reported calibration, which varies by model. The system already persists routing outcomes (classification_contract streaks, decision events); the acceptance threshold could be calibrated from observed fast-route/grounded-route agreement per model rather than fixed.
- `argus_skill/manager/_helpers.py:20` · _DEFAULT_FAST_ROUTE_MAX_TASK_CHARS = 12_000  
  后果:Longer tasks silently skip the fast route and pay for the grounded classification call. No failure, just cost.  
  建议:A char count standing in for 'fits comfortably in a small classify prompt' should be derived from the classify model's context length together with the fixed prompt scaffolding size, not fixed at 12k.
- `argus_skill/manager/_helpers.py:21` · _DEFAULT_FAST_ROUTE_MAX_PROMPT_CHARS = 24_000  
  后果:If the assembled prompt exceeds it, the fast pass is skipped and the grounded route runs instead. Cost-only consequence.  
  建议:Same as the task-chars cap: it encodes model context length as a fixed constant; derive from the resolved model.
- `argus_skill/manager/_helpers.py:22` · _DEFAULT_GROUNDED_ROUTE_MAX_PROMPT_CHARS = 32_000  
  后果:_vertical_ops.py:736 raises VerticalDecisionError ('Manager grounded-route prompt exceeds configured context cap') — the entire Manager handoff FAILS and the operator's mission is never enqueued. Same hard failure on the grounding retry at _vertical_ops.py:915.  
  建议:This is the worst route cap: hitting it abandons legitimate operator work outright, and the 32k-chars number is a proxy for model context that has no relationship to the actual model in use. Derive from the resolved model's context window (chars-per-token estimate) instead of a fixed fallback; a long task plus a large route-contract/snapshot block should not be a fatal condition.
- `argus_skill/manager/_vertical_ops.py:91` · _routing_workspace_snapshot entries[:40] = 40  
  后果:Entries beyond 40 are silently dropped — and project_markers is computed FROM the truncated list (lines 100-104), so in a root with >40 entries a marker like pyproject.toml or requirements.txt that sorts after position 40 becomes invisible to routing, potentially misclassifying a repository-sensitive decision as needing no grounding.  
  建议:Bounding the snapshot is legitimate (it feeds a prompt), but the marker scan should run over the full directory listing before truncation, and the rendered-entry budget should follow the prompt cap rather than a fixed 40.
- `argus_skill/manager/_vertical_ops.py:298` · grounding brief cap = 8_000 (truncate to 7_999 + ellipsis)  
  后果:The tail of the grounding brief (which the prompt asks to end with 'recommended decomposition') is cut off with only an ellipsis; the Engineer receives partial architecture/build/test evidence with no signal anything is missing beyond the ellipsis character.  
  建议:The budget should relate to the Engineer prompt budget/model context, and truncation of structured evidence should prefer dropping whole trailing sections or asking the model for a shorter brief; a fixed 8k chars encodes a context-size judgment twice removed from the model that will consume it.
- `argus_skill/manager/directive.py:21` · STEERING_MAX_ENTRIES = 10  
  后果:The 11th-oldest standing operator steering directive is silently no longer seen by Planner/Engineer even though the operator never cleared it — standing guidance quietly stops standing.  
  建议:The real constraint is the prompt budget, which STEERING_MAX_CHARS already enforces; a separate fixed entry count double-caps and can drop short directives that would fit. Render under the char budget alone, or derive the entry window from it.
- `argus_skill/manager/directive.py:22` · STEERING_MAX_CHARS = 4_000  
  后果:Older standing directives silently fall out of every role's prompt; one directive may appear cut off mid-sentence.  
  建议:A budget must exist (steering is injected into every role turn), but 4,000 chars is a fixed guess at what the prompt can afford; derive it from the consuming model's context budget, and surface (event/log) when active directives are being dropped so the operator can consolidate them.
- `argus_skill/manager/skill_review.py:128` · placement-judge task excerpt = [:2000]  
  后果:The judge classifies placement (global / vertical / stay) from a prefix of the task; context past 2,000 chars is silently invisible.  
  建议:Placement quality gates where a skill propagates across all future projects; the budget should follow the judge model's context rather than a fixed prefix, or at least mark the truncation in the prompt.
- `argus_skill/manager/skill_review.py:129` · placement-judge content excerpt = [:12000]  
  后果:A long skill is judged on its head; sections that would reveal it is project-specific (and should stay local) can be cut, biasing toward wrong promotion.  
  建议:Same as the task cap: fixed char prefixes standing in for model context, feeding a decision with cross-project blast radius.
- `argus_skill/manager/skill_tidy.py:19` · _MAX_CANDIDATE_FILES = 8  
  后果:Candidate #9 onward is silently absent from the evidence bundle — it can never be promoted this round, with no record that it was skipped.  
  建议:The real budget is the reviewer prompt size, already enforced by _MAX_CANDIDATE_CHARS; the fixed file count can exclude small candidates that would fit. Fill under the char budget (or the consuming model's context) and emit a diagnostic naming what was omitted.
- `argus_skill/manager/skill_tidy.py:20` · _MAX_CANDIDATE_CHARS = 12_000  
  后果:Later candidates are truncated to whatever budget remains (possibly a useless prefix) or dropped entirely; the reviewer judges promotion on partial text without knowing it.  
  建议:A prompt budget must exist, but 12k chars is a fixed stand-in for the reviewer model's context length; derive it from the resolved model and prefer whole-file inclusion with explicit '(N candidates omitted)' markers over silent prefix truncation.
- `argus_skill/manager/skill_tidy.py:267` · mission_result excerpt in team-learning prompt = [:2000]  
  后果:The promotion reviewer sees only the head of a long mission result; the prompt simultaneously forbids it from inspecting any other source, so evidence past 2,000 chars is unreachable by design.  
  建议:Since the prompt explicitly makes this excerpt 'the complete mission evidence', silently cutting it directly weakens the review it feeds; the budget should come from the reviewer model's context, with the truncation at least marked in the prompt.
- `argus_skill/manager/skill_tidy.py:306` · mission_objective excerpt in team-learning prompt = [:4000]  
  后果:Long objectives are silently cut; the reviewer may miss constraints that distinguish 'followed explicit operator constraints' from 'durable general procedure'.  
  建议:Same context-budget argument as the mission-result cap; the pair of fixed numbers (2000/4000) encodes a guess about the reviewer model's affordable prompt size.
- `argus_skill/planner/planner.py:40` · _PLANNER_REPAIR_TEXT_LIMIT = 8000  
  后果:The repair model loses the TAIL of its prior output — and the decision footer being repaired sits at the end of the reply, so the truncation preferentially removes exactly the malformed footer the model is asked to correct.  
  建议:A bound on quoting your own prior transcript is reasonable, but it should be derived from the model's context budget, and the kept region should be the tail (or head+tail) rather than the head, since the footer is what the repair is about.
- `argus_skill/planner/planner.py:60` · PlannerConfig.role_session_max_turns = 6  
  后果:Session context is rotated — accumulated in-thread context is dropped and rebuilt from durable state. Work continues; only continuity is affected.  
  建议:Two fixed proxies (turns and tokens) guard the same resource — context pressure. The token cap on line 61 is the direct measure; the turn count is a redundant guess that can rotate a cheap session early or fail to catch six enormous turns. Rotate on measured token pressure (already tracked per-result as input_tokens) instead.
- `argus_skill/planner/planner.py:61` · PlannerConfig.role_session_max_input_tokens = 120_000  
  后果:Context rotation only — never stops planning work.  
  建议:120k is a hardcoded stand-in for 'most of the model's context window', which varies by model (the backend/model are both configurable right next to it). Derive from the resolved model's actual context length minus a safety margin.
- `argus_skill/provider_integrations/copilot_guard.py:42` · _DEFAULT_RATE_COOLDOWN_SECONDS = 30*60 (1800)  
  后果:A single 429 pauses every Copilot-backed project on the host for 30 minutes, even when the provider's actual limit clears in seconds.  
  建议:Rate-limit responses carry (or imply) a real retry horizon; a flat 30-minute host-wide freeze massively over-penalizes short bursts. Parse Retry-After / use escalating cooldowns starting near the provider's stated window.
- `argus_skill/provider_integrations/copilot_usage.py:168` · read_copilot_usage_since timeout default = 0.75 (with 0.05s poll at line 187 and sqlite timeout 0.2 at line 277)  
  后果:Returns None (or partial rows) — the call's real cost silently never enters Argus accounting, under-counting spend against the premium budget.  
  建议:Usage rows land whenever the CLI flushes, which varies with load; a fixed 0.75s races that flush. Silently dropped cost data corrupts the very budget the guard enforces — wait keyed on observed DB write latency, or reconcile missed rows asynchronously instead of a sub-second fixed window.
- `argus_skill/provider_integrations/copilot_usage.py:196` · find_copilot_usage_near lookback window = started_at fallback completed_at-5.0, padded ±1.0s  
  后果:Rows created outside the window are silently not attributed — cost accounting misses them.  
  建议:The fallback assumes calls last ≤5s, which is false for real LLM turns. When started_at is unknown the window should be derived from the call's own recorded duration, not a fixed 5 seconds.
- `argus_skill/reviewer/_parsing.py:398` · next_action [:1500] = 1500  
  后果:A multi-part directive is cut mid-instruction; the engineer executes an incomplete work package and the reviewer then judges the round against the full directive it thinks it issued, a silent divergence.  
  建议:Unlike prose fields, this string is executed: truncating it changes what work happens. Either the cut should be visible (append an omission marker so both roles can see it) or the budget should be validated at emission time with the reviewer asked to shorten — silent mid-directive truncation is the smell.
- `argus_skill/roles/prompts/reviewer.py:270` · engineer_account head/tail truncation (> 6000 → [:4000] + [-1900:]) = 6000/4000/1900  
  后果:Evidence in the middle of a long engineer summary (intermediate results, caveats, failure details) is invisible to the reviewer; the marker discloses the omission but the reviewer's only recourse is the raw event-log pointer.  
  建议:The primary evidence stream for the completion gate is truncated with fixed char budgets that ignore how much context headroom the reviewer session actually has. Size the account against the remaining token budget (the code already computes per-block token stats) rather than a flat 6000 chars.
- `argus_skill/skills/checklist_store.py:55` · MAX_STATEMENT_LEN / MAX_EVIDENCE_LEN = 1600 / 1600 (line 56; applied at lines 121-122)  
  后果:A checklist statement longer than 1600 chars is cut mid-sentence with no ellipsis or marker — the reviewer receives a corrupted contract line and nothing flags that it happened.  
  建议:Bounding prompt bloat from historical rows is legitimate, but this is the silent-data-drop pattern: the cap should at minimum append a visible truncation marker, and the bound itself belongs derived from the prompt budget of the model in use rather than a bare 1600. A checklist item is policy text — corrupting it silently is worse than rejecting it loudly at write time.
- `argus_skill/skills/rl_training_health.py:57` · TAIL_WINDOW = 8  
  后果:Facts and advisory signals describe only the last 8 steps; a slow multi-hundred-step decay trend is invisible, and one recovered step can clear a signal. Advisory only — never blocks.  
  建议:Collapse is correctly framed as a tail property, but the right tail length depends on run length and logging cadence — 8 steps of a 1200-step run is a different fraction than 8 of 20. Derive from optimizer_steps or manifest minimum_accepted_optimizer_steps (e.g. max(8, 5% of steps)) with a prompt-size ceiling.
- `argus_skill/skills/run_contract.py:81` · MAX_PROMPT_REPETITION = 8.0  
  后果:A full-scale training launch is refused (subagent pre-launch interlock) with an actionable message; the agent must expand distinct tasks, shorten the run, or label the run smoke_only (which forfeits citing it as general-learning evidence).  
  建议:This is a research-quality judgment call frozen into the harness — exactly the class the module's own comment hedges ('Generous on purpose; the L2 reviewer still judges'). Whether 8x repetition is memorisation depends on task-family size, augmentation, and epoch semantics; SFT routinely runs multiple epochs legitimately. It blocks real launches, so it should be a contract-declared field the plan freezes (with 8.0 at most a default), or derived from the probe's own empirical saturation evidence, which the packet already carries. The mechanism (refuse unprobed degenerate curricula) is sound; the fixed universal ratio is the smell. The smoke_only escape hatch keeps it from being a hard wall.
- `argus_skill/skills/run_contract.py:90` · _REWARD_CEILING (launch interlock) = 0.99  
  后果:Full-scale launch refused; agent must pick harder tasks or label the run smoke_only.  
  建议:The mechanism prevents the documented GPU-burn failure (reward pinned at ceiling → zero advantage → no gradient), but 0.99 silently assumes rewards normalized to [0,1] with 1.0 as 'solved'. A vertical with unnormalized or differently-scaled rewards makes this bound either vacuous or falsely blocking. Derive the ceiling from the reward schema declared in the frozen contract (or from the probe's own max attainable reward) rather than assuming the RLVR 0/1 convention.
- `argus_skill/team/curator.py:273` · Curator default_width = 8  
  后果:A campaign without an explicit width runs at most 8 concurrent teammates; excess backlog waits. Nothing is killed.  
  建议:8 encodes a machine-capacity judgment with no relation to actual CPU/GPU headroom. Derive the default from host resources (cores, GPU lease availability) or from the campaign's own task shapes; the lead can already override per-campaign via pool width.
- `argus_skill/team/curator.py:509` · _terminate grace (SIGTERM→SIGKILL) = 2.0 s (plus wait(timeout=max(grace,5.0)) at line 517 and wait(timeout=5) post-SIGKILL at line 529)  
  后果:The entire process tree is SIGKILLed — any cleanup longer than 2s (flushing a result shard, board bookkeeping, closing an LLM stream) is cut off mid-write. The Curator compensates by failing the task itself, but a partially-written shard can be left behind.  
  建议:The TERM→KILL escalation is necessary (a wedged handler must not stall the reaper), but 2s is an arbitrary and aggressive guess for a Python process running a full LLM mission; a teammate legitimately writing its shard can lose it. Scale the grace to what the child is (e.g. tens of seconds for a mission process, seconds for a dead-on-arrival one) or make it a Curator parameter wired from the same config as teammate timeouts. The 5s post-SIGKILL waits are fine — after SIGKILL exit is imminent and they only bound the reaper's stall.
- `argus_skill/tools/gpu_lease.py:70` · _default_config keep-alive command: --util 0.5 --mem 0.5 = 0.5% util, 0.5% mem (fallback config)  
  后果:If the operator config is missing, park/release restarts a keep-alive so faint it may not defeat the scheduler's idle detection at all, silently failing the tool's entire purpose (cards reclaimed despite 'keepalive_running: true'). It also assumes gpu_load.py sits in $HOME.  
  建议:A fallback that contradicts the loader's own documented defaults encodes no coherent policy; at minimum align it with gpu_load.py's defaults (or refuse to park without explicit config, since a false sense of protection is worse than a loud error).
- `argus_skill/tools/subagent/_discuss_run.py:65` · stdout_tail[-1500:] / stderr_tail[-800:] / _render_discussion(task_id, 3000) = 1500/800/3000 chars  
  后果:The discussing supervisor loses earlier discussion turns and most of the run output; it can re-litigate or contradict points already settled above the cut, degrading the quality of the stop/resume dialogue.  
  建议:Prompt budget should be derived from the supervisor model's context window; a discussion whose whole point is accumulated shared understanding is the worst place for a fixed 3000-char history.
- `argus_skill/tools/subagent/_discussion_log.py:105` · _render_discussion(..., max_chars=2000) default = 2000 chars  
  后果:Older discussion turns silently drop out of the rendered history.  
  建议:Same prompt-budget concern as the callers: size to the consuming model's context, not a constant.
- `argus_skill/tools/subagent/_registry.py:40` · SUPERVISOR_THREAD_MAX_CHECKS = 12  
  后果:The supervisor loses its whole-run observation history every 12 checks — trend judgments (loss slowly diverging, progress rate decaying) reset to a cold start at an arbitrary fixed cadence.  
  建议:The real constraint is token usage vs. the backend model's context length, both of which are measurable per check (usage tuples are already collected); rotate on measured token pressure, carrying forward a summary, instead of a magic check count.
- `argus_skill/tools/subagent/_registry.py:476` · _tail_file(..., 3000) in reconcile_terminal_task = 3000 chars  
  后果:The durable post-mortem record silently loses everything before the last 3000 chars — often the actual traceback for a crash that happened early.  
  建议:Same pattern as the live-run tails: derive from file size/consumer context, or store an error-aware excerpt; the full files exist on disk so the record could also just point at offsets.
- `argus_skill/tools/subagent/_reporting.py:57` · report-prompt tails: stdout[-2000:], stderr[-1000:], _tail_file 1200/800/800 = 2000/1000/1200/800/800 chars  
  后果:The final human-facing report is generated from only the last few KB of each stream; a run that crashed early is summarized from its shutdown noise rather than the actual failure.  
  建议:Report fidelity is bounded by these constants; derive the budget from the report model's context length and weight toward error-bearing regions instead of blind tails.
- `argus_skill/tools/subagent/_supervised_preflight.py:171` · _next_monitor_interval: return current * 2 = x2 per healthy check, no upper cap  
  后果:On a multi-hour healthy run the interval doubles unboundedly (120s -> 4m -> ... -> hours between checks), so a late-run collapse, hang, or divergence can go unobserved for a very long stretch before the next check even happens.  
  建议:The doubling mechanism is good but needs a ceiling derived from context — e.g. a fraction of elapsed run time or expected duration — so the maximum blind window stays proportionate to how much work would be lost.
- `argus_skill/tools/subagent/_supervised_run.py:125` · _tail_file(stdout_path, 2000) / stderr 1000 / progress 1500 / status 800 = 2000/1000/1500/800 chars  
  后果:The supervisor judges run health from only the last few KB; an error signature or context above the cut is silently invisible, which can produce a wrong healthy/degraded verdict on a run whose fate the supervisor can kill.  
  建议:The affordable tail size is a function of the supervisor model's context window and the run's log verbosity, not a universal constant; derive it from the backend's context length (and consider progress-aware sampling instead of a raw tail).
- `argus_skill/tools/subagent/_supervised_run.py:139` · _tail_file(progress_path, 1500) / _tail_file(status_path, 800) = 1500/800 chars  
  后果:A verbose progress file loses its earlier trajectory; the supervisor sees only the newest entries when judging whether the run is advancing.  
  建议:Same prompt-budget argument as the other supervision tails: size to the supervisor model's context, and prefer trajectory-preserving sampling (first+last) for progress files whose whole value is the trend.
- `argus_skill/tools/subagent/_supervised_run.py:503` · proc.wait(timeout=30) in _supervised_handle_early_stop = 30 seconds  
  后果:The process group is SIGTERM/SIGKILLed. A cooperative trainer that honors STOP by saving a final checkpoint can be killed mid-save if the checkpoint takes over 30s (routine for large models), corrupting the very artifact the early-stop was meant to preserve.  
  建议:Grace should scale with observed checkpoint size/save time or at minimum watch process liveness (still writing = still saving) rather than a fixed wall-clock 30s; this is exactly the wall-clock-kills-legitimate-work pattern.
- `argus_skill/tools/subagent/_supervised_run.py:528` · _tail_file(..., 3000) persisted stdout/stderr tails (also line 948) = 3000 chars  
  后果:Everything before the last 3000 chars is silently dropped from the durable record the engineer and reporting layer later read; the root-cause traceback of a crash often scrolls past this window.  
  建议:Derive from file size and downstream consumer (full path is on disk anyway; the record could store an error-aware excerpt or scale with consumer context limits).
- `argus_skill/tools/subagent/_text.py:62` · _tail_file(path, max_chars=3000) default = 3000 chars  
  后果:Every consumer that doesn't override it silently sees only the last 3000 chars of any file.  
  建议:One constant serves prompts to different models and durable records alike; the default should be derived per consumer (context length, file size) rather than baked into the utility.
- `argus_skill/verticals/chip_design/environment_audit.py:88` · _run timeout = 20.0s  
  后果:A slow-starting licensed EDA tool is reported unavailable, and the vertical's stage guidance narrows to the tools it thinks exist.  
  建议:Same shape as the kernel audit: keep a bound, but license-server-backed EDA tools routinely take >20s to answer --version; the bound should be per-tool-class, not one number.
- `argus_skill/verticals/fiction_writing/novelty.py:66` · _DEFAULTS (note_run/block_run) + _RATIO_NOTE_FLOOR + _DEFAULT_OVERLAP_BLOCK + _MIN_COVERED_FOR_RATIO_BLOCK = zh 12/24 tokens, en 6/12 words; 0.05; 0.5; 40 tokens  
  后果:The draft is rejected as near-verbatim copying and must be rewritten.  
  建议:The code itself labels these 'model-seed (BCC-pending)' and ships a calibration harness (evaluations/calibrate_novelty.py) that derives them from corpus false-positive rates - the calibrated values should replace the seeds. The mechanism (block wholesale copying) is load-bearing; the specific seeds are placeholders.
- `argus_skill/verticals/kernel_engineering/campaign.py:183` · --min-geomean / --min-row defaults = 1.01 / 0.995  
  后果:An attempt with, say, 0.8% geomean gain is marked passed=False and not retained as the campaign winner.  
  建议:The margin exists to separate real gains from timing noise, but the right margin IS the measured run-to-run variance of the benchmark on this box; deriving it from repeated baseline runs would stop both false winners on noisy benchmarks and discarded real wins on stable ones.
- `argus_skill/verticals/kernel_engineering/environment_audit.py:212` · _run timeout default + torch/pip probes = 10.0s default; 30.0s for pip check and torch probes (lines 267, 288, 397, 416)  
  后果:A cold torch import (NFS home, first CUDA init) exceeding 30s silently reports torch/CUDA as absent, and the audit tells the planner the environment is not ready - misdirecting a GPU campaign on a machine that is actually fine.  
  建议:Probes need a bound (a hung import would hang the audit), but 30s is within the real tail of first-time torch+CUDA imports; scale the bound to the probe class (generous for framework imports, tight for --version) or retry once before declaring absence.
- `argus_skill/verticals/math/context_projection.py:132` · _MAX_TEXT / _MAX_NEIGHBOUR_TEXT / _MAX_ROWS = 600 / 220 / 12  
  后果:A pasted-proof-sized 'statement' is clipped with a marker; a claim citing 120 theorems lists 12 and says how many were withheld.  
  建议:The report-what-you-withheld design is right, but the budgets should scale with the consuming model's context rather than freezing 12 rows for all missions; a wide claim neighbourhood is sometimes exactly what the prover needs.
- `argus_skill/verticals/medical/connectors.py:171` · fetch_pubmed retmax / fetch_clinical_trials page_size = 100 / 100 (line 251)  
  后果:Evidence beyond the first 100 hits is silently absent from the retrieval batch the dossier is built on.  
  建议:For a broad medical scope 100 records is an arbitrary slice of the literature; page through to the API's reported total (or a budget derived from the scope) and record how many hits exist versus how many were retrieved.
- `argus_skill/verticals/physics/downgrade.py:22` · _THRESHOLDS fallback defaults = model_execute_cap 4, pivot_cap 2, same_diagnostic_cap 2, reviewer_reject_cap 3, repeat_blocker_cap 3, tier_cost_fraction 0.6  
  后果:The campaign's ambition tier is forcibly lowered - scope of the research is permanently narrowed for the rest of the run.  
  建议:These encode 'how much struggle is too much' from a single historical campaign; two pivots is normal exploration in some domains. The counts should be calibrated against the distribution of successful past campaigns rather than one seed run - though having SOME downgrade path is legitimately protective against infinite model-execute ping-pong.
- `argus_skill/verticals/quant/analysis/walk_forward.py:60` · WalkForwardConfig defaults = train_size 90, test_size 14, step_size 14 (validation floors 10/1/1)  
  后果:Backtests run with a fixed quarter-train/fortnight-test scheme unless the caller overrides.  
  建议:Fold geometry should derive from the available data span and the strategy's holding period; a fixed 90/14/14 silently under-trains on short histories and under-tests on long ones.
- `argus_skill/verticals/quant/factor_toolkit/evolution.py:331` · evolve defaults = generations 5, population 20, elite 5, mutation_rate 0.7; max_evals default None  
  后果:Search explores at most ~100 candidates by default and stops, regardless of whether fitness was still improving.  
  建议:Search breadth/depth should be driven by the campaign's evaluation budget and the observed fitness trajectory (stop on plateau, extend while improving) rather than a fixed 5x20; the plumbing (history, max_evals) already exists.
- `argus_skill/verticals/quant/model_toolkit/selection.py:104` · walk-forward model-selection defaults = n_folds 4, purge 5, embargo 2; per-day IC needs >=10 names (line 46)  
  后果:Models are ranked on 4 purged folds; days with <10 stocks contribute no IC.  
  建议:Purge/embargo should equal the label horizon (which the caller knows) rather than fixed 5/2; fold count should scale with data span. The >=10-names floor is statistically load-bearing and fine.
- `argus_skill/verticals/research/academic_language_review.py:48` · REVIEW_SOURCE_CONTEXT_CHAR_LIMIT / PINNED / NUMBERED = 70000 / 32000 / 42000  
  后果:Later sections of a long paper are cut from the reviewer's context, so its advisory review is computed from a partial manuscript.  
  建议:A budget must exist, but it should derive from the configured reviewer model's context window and the actual manuscript size, not three frozen numbers tuned for an unknown model.
- `argus_skill/verticals/research/idea_portfolio.py:20` · DEFAULT_PORTFOLIO_SIZE = 12  
  后果:A campaign that explored 8 or 15 ideas cannot complete selection; the state machine refuses the payload and the pipeline stalls until exactly 12 exist.  
  建议:Portfolio width is a judgment call that should scale with campaign compute, topic breadth, and operator intent. Freezing it at 12 (policy literally named fixed_twelve) forces make-work ideas or blocks legitimate narrower explorations.
- `argus_skill/verticals/research/paper_infrastructure_review.py:51` · PAPER_INFRASTRUCTURE_REVIEW_SOURCE_CHAR_LIMIT = 140000  
  后果:Tail of a long manuscript is cut from the reviewer's context.  
  建议:Should be derived from the reviewer model's context length rather than fixed; ideally reports what was withheld.
- `argus_skill/verticals/research/prompt_policy.py:14` · _CONTEXT_CHAR_LIMIT = 32000  
  后果:Prompt context beyond 32K chars is dropped (marked, not silent).  
  建议:Should track the planner model's context window rather than a fixed 32K chars; the visible marker is good practice and should stay.
- `argus_skill/webapi/daemon_lifecycle.py:491` · replace_project_daemon force-stop timeout = 2.0 (plus 5.0s release wait at line 497, 0.05s poll)  
  后果:The victim daemon — possibly mid-mission with a live engineer session and GPU job — is force-killed after 2s; its state is parked for later resume, but in-flight round work is lost.  
  建议:The replacement is operator-initiated, but 2s cannot drain a round that is mid-provider-call. The grace window should scale with what the victim is doing (idle: kill fast; mid-round: allow the round to checkpoint), or at least use the same drain machinery the upgrade path uses (defer via scheduled upgrade instead of killing).
- `argus_skill/webapi/daemon_lifecycle.py:772` · stop_project_daemon timeout = 1.0 if force else 10.0  
  后果:Force: process tree killed after 1s. Graceful: stop attempt gives up after 10s and reports rc while active work may still be draining.  
  建议:Both windows are judgment calls about how long legitimate teardown takes. Force-stop is an explicit interrupt (1s is defensible, mirroring Codex-style interrupt), but the 10s graceful drain can abandon a drain that a busy mission genuinely needs; drain should wait on round-boundary liveness (is the daemon still making teardown progress?) rather than a flat 10 seconds.
- `argus_skill/webapi/manager_session_intent.py:28` · contextualize_operator_turn bounds = last_team_task[:600], last 4 prior turns, each [:300] (lines 28-35)  
  后果:The Manager resolves pronouns/corrections against amputated context — a long prior answer's tail (often where the actual decision lives) is silently absent, and turns older than 4 vanish entirely.  
  建议:These clip data fed to a model whose context window is five orders of magnitude larger. The budget should be token-based and derived from the Manager model's context length, keeping whole turns and trimming oldest-first, rather than fixed char slices that cut mid-sentence.
