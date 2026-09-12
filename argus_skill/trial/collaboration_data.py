"""Read-only, purpose-gated task and role views of retained public observations.

This is a display projection, not a new training source. Task association comes
only from the journal's explicit association or producer-bound mission_id.
Runtime run labels identify roles; prompt text and temporal adjacency never do.
No runtime files or private reasoning are read. V2 retained observations are
summarized independently of sample approval. A role observation is not evidence
that another role delegated to it.
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter

from .analytics import AnalyticsError
from .interaction_capture import _valid_task_id
from .training_capture import (
    HOSTED_EPISODE_BYTES,
    HOSTED_PROFILE,
    MAX_EPISODE_BYTES,
    MAX_EPISODES,
    OBSERVED_POLICY,
    PROFILE,
    _hosted_sensitive,
)
from .training_data import (
    MAX_EVENT_BYTES,
    MAX_PROJECT_EVENTS,
    MAX_PROJECTS,
    MAX_SOURCE_BYTES,
    PURPOSES,
    REVIEWER_KINDS,
    _hash,
    _json,
    _sensitive,
    _timestamp,
)

ROLES = {
    "manager": "统筹", "planner": "规划", "engineer": "执行",
    "reviewer": "审查", "operator": "用户", "unknown": "角色未记录",
}
# Browsing returns content-free episode summaries, not an export package. Its
# separate source-read ceiling matches the retained hosted capture capacity.
MAX_OVERVIEW_SOURCE_BYTES = 128 * 1024 * 1024
LIMITATIONS = [
    "角色取自运行时标签和公开事件字段；不会从提示词猜测角色。",
    "活动按观察时间排列，不代表依赖或交接；未记录的交接关系保持未知。",
    "任务结果、过程采集、训练质量分别展示；工具成功不代表任务成功。",
    "仅展示当前用途授权之后的保留记录；整个多智能体任务的完整性未经证实。",
    "历史开始事件不能证明任务仍在运行；没有终态记录时，任务结果保持未确认。",
]
_CODE = re.compile(r"[a-z][a-z0-9_.-]{0,100}\Z")
_ROLE_LABEL = re.compile(r"(manager|planner|engineer|reviewer)(?:[.-][a-z0-9_.-]+)?\Z")
_OUTCOMES = {
    "unknown": "任务结果未确认", "start": "已开始", "continue": "执行中",
    "resume": "已恢复", "pause": "已暂停", "cancel": "已取消",
    "fail": "运行时报告失败", "incomplete": "尚未完成", "complete": "运行时报告完成",
    "settled_unknown": "结束状态未确认",
}
_EVENT_LABELS = {
    "http.request": "用户提交任务", "http.response": "用户请求响应",
    "life.mission.started": "任务开始", "life.mission.completed": "任务状态更新",
    "life.planner.task_added": "任务已加入计划", "engineer.progress": "角色活动",
    "manager.activity": "统筹活动", "round.review.completed": "审查记录",
    "ui.argus": "对用户的公开回复", "ui.operator": "用户消息",
}
_TOOL_LABELS = {
    "read": "读取文件", "write": "写入文件", "edit": "修改文件", "bash": "执行命令",
    "grep": "搜索内容", "find": "查找文件", "ls": "查看目录",
}


def _code(value, fallback="unknown"):
    return value if isinstance(value, str) and _CODE.fullmatch(value) and not _sensitive(value) else fallback


def _task_id(value):
    return value if _valid_task_id(value) and not _sensitive(value) else None


def _role(value):
    if not isinstance(value, str):
        return "unknown"
    if value in ROLES:
        return value
    match = _ROLE_LABEL.fullmatch(value)
    return match[1] if match else "unknown"


def _event_role(payload):
    roles = {_role(payload.get(key)) for key in ("agent_layer", "role", "actor")}
    roles.discard("unknown")
    return roles.pop() if len(roles) == 1 else "unknown"


def _text(value, limit=4096):
    return value[:limit] if isinstance(value, str) and not _sensitive(value) else None


def _identity(tenant, sid, task):
    return _hash(_json([tenant, sid, task]).encode())


def _bucket(tenant, sid, task):
    return {
        "id": _identity(tenant, sid, task), "tenant_id": tenant, "sid": sid, "task_id": task,
        "title": "未关联到具体任务的项目活动" if task is None else f"任务 {task}",
        "request": None, "mission_title": None, "objective": None, "mission_brief_source": None,
        "roles": [],
        "task_outcome": {"state": "unknown", "label": _OUTCOMES["unknown"], "evidence_event_ids": [],
                         "last_lifecycle": None},
        "collection": {"states": {}, "accepted_episodes": 0, "quarantined_episodes": 0, "gaps": 0,
                       "retained_episodes": 0, "observed_events": 0},
        "quality": {"approved_samples": 0, "candidates": 0}, "last_observed_at": None,
        "segments": [], "episodes": [], "handoffs": [], "gaps": [],
        "unassigned_observations": 0, "global_complete": False,
    }


class CollaborationData:
    """A bounded projection reusing the training authorization and validators."""

    def __init__(self, training):
        self.training = training
        self.analytics = training.analytics
        # Cache only hashes and content-free summaries. Permission, retention,
        # metadata and review receipts are checked again for every request.
        self._validated = {}
        self._cache_lock = threading.Lock()

    def overview(self, purpose="internal_training", *, offset=0, tenant=None, query=""):
        self._purpose(purpose)
        if type(offset) is not int or not 0 <= offset <= 2_147_483_647:
            raise ValueError("Invalid project offset")
        if not isinstance(query, str) or len(query) > 160:
            raise ValueError("Project search must be at most 160 characters")
        if tenant is not None:
            self.analytics._tenant(tenant)
        tenants = [tenant] if tenant is not None else sorted(self.analytics.tenants)
        placeholders = ",".join("?" for _ in tenants) or "NULL"
        where = f"notice_version=? AND tenant_id IN ({placeholders}) AND instr(sid,?)>0"
        args = (self.analytics.notice_version, *tenants, query)
        with self.analytics._db() as db:
            db.execute("BEGIN")
            total = db.execute(f"SELECT count(*) FROM journey_projects WHERE {where}", args).fetchone()[0]
            rows = db.execute(
                f"SELECT tenant_id,sid FROM journey_projects WHERE {where} "
                "ORDER BY tenant_id,sid LIMIT ? OFFSET ?", (*args, MAX_PROJECTS, offset),
            ).fetchall()
            projects, tasks, remaining = [], [], MAX_OVERVIEW_SOURCE_BYTES
            for row in rows:
                project, projected, used = self._project(db, purpose, row["tenant_id"], row["sid"], remaining)
                projects.append(project)
                tasks.extend(projected)
                remaining -= used
        projects, tasks = self._recheck(purpose, projects, tasks)
        tasks.sort(key=lambda item: (-(item["last_observed_at"] or 0), item["id"]))
        return {
            "tasks": [self._summary(task) for task in tasks], "projects": projects,
            "offset": offset, "next_offset": offset + len(rows) if offset + len(rows) < total else None,
            "has_more_projects": offset + len(rows) < total, "total_projects": total,
            "counts": {
                "tasks": sum(task["task_id"] is not None for task in tasks),
                "roles": len({role["role"] for task in tasks for role in task["roles"]
                              if role["role"] not in {"unknown", "operator"}}),
                "tool_pairs": sum(role["tool_pairs"] for task in tasks for role in task["roles"]),
                "approved_samples": sum(task["quality"]["approved_samples"] for task in tasks),
                "candidates": sum(task["quality"]["candidates"] for task in tasks),
                "observed_episodes": sum(task["collection"]["retained_episodes"] for task in tasks),
                "observed_events": sum(task["collection"]["observed_events"] for task in tasks),
            },
            "purpose": purpose, "limitations": list(LIMITATIONS), "global_complete": False,
            "completeness": self._completeness(
                projects, MAX_OVERVIEW_SOURCE_BYTES, MAX_OVERVIEW_SOURCE_BYTES - remaining,
                offset + len(rows) < total,
            ),
        }

    def detail(self, purpose, tenant, sid, task_id=None):
        self._purpose(purpose)
        self.training._selection([{"tenant_id": tenant, "sid": sid}])
        if task_id is not None and _task_id(task_id) is None:
            raise ValueError("Invalid task identity")
        with self.analytics._db() as db:
            db.execute("BEGIN")
            project, tasks, used = self._project(db, purpose, tenant, sid, MAX_SOURCE_BYTES)
        projects, tasks = self._recheck(purpose, [project], tasks)
        project = projects[0]
        if not project["eligible"]:
            raise AnalyticsError(403, project["reason"])
        task = next((item for item in tasks if item["task_id"] == task_id), None)
        if task is None:
            raise AnalyticsError(404, "retained_task_not_found")
        return {**task, "project": project, "purpose": purpose, "limitations": list(LIMITATIONS),
                "completeness": self._completeness([project], MAX_SOURCE_BYTES, used, False)}

    @staticmethod
    def _completeness(projects, budget, used, has_more):
        reasons = Counter()
        for project in projects:
            reasons.update(project.get("display_reason_counts", {}))
        return {
            "scope": "selected_project_page", "global_complete": False,
            "page_truncated": any(project.get("display_truncated", False) for project in projects),
            "reason_counts": dict(reasons), "has_more_projects": has_more,
            "source_bytes_examined": used, "source_byte_limit": budget,
        }

    def _purpose(self, purpose):
        if purpose not in PURPOSES:
            raise ValueError("Unknown training purpose")
        if self.training.journal is None:
            raise AnalyticsError(503, "training_journal_unavailable")

    def _recheck(self, purpose, projects, tasks):
        """Keep expensive validation off the capture path; recheck before release.

        The body was projected from one SQLite read snapshot. A new transaction
        under the shared deletion/revocation lock rejects any changed grant, so
        a concurrent revoke/regrant cannot reveal the old snapshot's content.
        """
        permitted = set()
        with self.training.controls.capture_lock, self.analytics._db() as db:
            db.execute("BEGIN")
            for project in projects:
                if not project["eligible"]:
                    continue
                grant, denied = self.training._grant(db, project["tenant_id"], project["sid"], purpose)
                if denied or grant != project["granted_at"]:
                    project.update(eligible=False, title=project["sid"], reason=denied or "authorization_changed")
                else:
                    permitted.add((project["tenant_id"], project["sid"]))
        return projects, [task for task in tasks if (task["tenant_id"], task["sid"]) in permitted]

    @staticmethod
    def _summary(task):
        return {key: value for key, value in task.items()
                if key not in {"segments", "episodes", "handoffs", "gaps"}}

    def _project(self, db, purpose, tenant, sid, budget):
        grant, denied = self.training._grant(db, tenant, sid, purpose)
        project = {"tenant_id": tenant, "sid": sid, "title": sid, "eligible": denied is None}
        if denied:
            return {**project, "reason": denied}, [], 0
        project["granted_at"] = grant
        state = db.execute(
            "SELECT pruned_events FROM journey_projects WHERE tenant_id=? AND sid=? AND notice_version=?",
            (tenant, sid, self.analytics.notice_version),
        ).fetchone()
        if state is None:
            return {**project, "eligible": False, "reason": "journal_not_found"}, [], 0
        now = self.analytics.clock()
        cutoff = max(grant, now - min(30, self.analytics.retention_days) * 86400)
        buckets, gaps, used = {}, Counter(), 0

        def bucket(task):
            return buckets.setdefault(task, _bucket(tenant, sid, task))

        rows = db.execute(
            "SELECT sequence,id,ingested_at,length(CAST(record AS BLOB)) AS size "
            "FROM journey_events WHERE tenant_id=? AND sid=? AND notice_version=? AND ingested_at>=? "
            "ORDER BY sequence LIMIT ?",
            (tenant, sid, self.analytics.notice_version, cutoff, MAX_PROJECT_EVENTS + 1),
        ).fetchall()
        if len(rows) > MAX_PROJECT_EVENTS:
            gaps["display_event_limit"] += 1
        if state["pruned_events"]:
            gaps["retention_or_capacity_pruned"] += state["pruned_events"]
        for row in rows[:MAX_PROJECT_EVENTS]:
            if row["size"] > MAX_EVENT_BYTES:
                gaps["display_event_size_limit"] += 1
                continue
            if used + row["size"] > budget:
                gaps["display_byte_limit"] += 1
                continue
            # Inspect metadata before reading a body; exhausted budgets must
            # not still fetch a page of records that will only be discarded.
            raw = db.execute("SELECT record FROM journey_events WHERE id=?", (row["id"],)).fetchone()[0]
            used += row["size"]
            event = self._journal_event({**dict(row), "record": raw}, tenant, sid, grant, now)
            if event is None:
                gaps["event_not_displayable"] += 1
                continue
            if event["kind"] == "journal.gap":
                gaps[_code(event["payload"].get("code"), "recording_gap")] += 1
                continue
            task = bucket(event["task_id"])
            self._add_event(task, event)
        episodes = db.execute(
            "SELECT id,started_at,updated_at,state,reason,length(CAST(record AS BLOB)) AS size,"
            "substr(grants,1,4097) AS grants,substr(runtime_metadata,1,16385) AS runtime_metadata,"
            "runtime_profile FROM training_tool_episodes WHERE tenant_id=? AND sid=? AND started_at>=? "
            "ORDER BY id LIMIT ?", (tenant, sid, cutoff, MAX_EPISODES + 1),
        ).fetchall()
        if len(episodes) > MAX_EPISODES:
            gaps["display_episode_limit"] += 1
        for row in episodes[:MAX_EPISODES]:
            try:
                grants, runtime = json.loads(row["grants"]), json.loads(row["runtime_metadata"])
                if not isinstance(grants, dict) or grants.get(purpose) != grant or not isinstance(runtime, dict):
                    continue
            except (ValueError, TypeError, RecursionError):
                gaps["episode_metadata_invalid"] += 1
                continue
            if not cutoff <= row["started_at"] <= row["updated_at"] <= now:
                gaps["episode_time_unverified"] += 1
                continue
            task = bucket(_task_id(runtime.get("mission_id")))
            episode, cost = self._episode(db, row, runtime, purpose, tenant, sid, grant, budget - used)
            used += cost
            if episode.get("reason", "").startswith("display_"):
                gaps[episode["reason"]] += 1
            task["episodes"].append(episode)
            self._add_episode(task, episode)
        if gaps and not buckets:
            bucket(None)
        for task in buckets.values():
            task["gaps"] = [{"code": code, "count": count} for code, count in sorted(gaps.items())]
            task["collection"]["gaps"] = sum(gaps.values())
            task["segments"].sort(key=lambda item: (item["started_at"] or 0, item["id"]))
            task["roles"] = self._roles(task["segments"])
            task["unassigned_observations"] = sum(
                len(item["segments"]) for key, item in buckets.items() if key is None
            )
        # The task's original request is a better heading than opaque project IDs.
        titled = next((task["title"] for task in buckets.values() if task["request"]), None)
        if titled:
            project["title"] = titled
        project["display_reason_counts"] = {code: count for code, count in gaps.items() if code.startswith("display_")}
        project["display_truncated"] = bool(project["display_reason_counts"])
        return project, list(buckets.values()), used

    def _journal_event(self, row, tenant, sid, grant, now):
        try:
            if row["size"] > MAX_EVENT_BYTES:
                return None
            event = json.loads(row["record"])
            if (not isinstance(event, dict) or event.get("id") != row["id"]
                    or event.get("tenant_id") != tenant or event.get("sid") != sid
                    or event.get("notice_version") != self.analytics.notice_version
                    or not isinstance(event.get("payload"), dict)
                    or not isinstance(event.get("association"), dict)
                    or not isinstance(event.get("kind"), str)
                    or (event.get("lifecycle") is not None and not isinstance(event["lifecycle"], str))):
                return None
            if event.get("source_kind") == "journal_gap":
                return event if event["kind"] == "journal.gap" else None
            stamp = _timestamp(event.get("source_timestamp"))
            if stamp is None or not grant <= stamp <= row["ingested_at"] <= now:
                return None
            event["display_timestamp"] = stamp
            event["task_id"] = (_task_id(event.get("task_id"))
                                if event["association"].get("kind") == "authoritative" else None)
            event["content_displayable"] = not _sensitive(event)
            return event
        except (ValueError, TypeError, RecursionError, OverflowError):
            return None

    @staticmethod
    def _add_event(task, event):
        payload, stamp = event["payload"], event["display_timestamp"]
        role = _event_role(payload)
        kind = _code(event.get("kind"))
        state = event.get("lifecycle")
        if state in _OUTCOMES and task["task_id"] is not None:
            outcome = "unknown" if state in {"start", "continue", "resume", "settled_unknown"} else state
            task["task_outcome"] = {
                "state": outcome, "label": _OUTCOMES[outcome], "evidence_event_ids": [event["id"]],
                "last_lifecycle": {"state": state, "label": _OUTCOMES[state], "timestamp": stamp,
                                   "event_id": event["id"]},
            }
        if event["content_displayable"]:
            if task["task_id"] is not None and kind in {"life.mission.started", "life.planner.task_added"}:
                mission_title = _text(payload.get("title"), 256)
                objective = _text(payload.get("objective"))
                if mission_title or objective:
                    task["mission_title"] = mission_title
                    task["objective"] = objective
                    task["mission_brief_source"] = {
                        "event_id": event["id"], "kind": kind, "timestamp": stamp,
                        "association": event["association"].get("kind"),
                    }
            input_data = payload.get("input")
            original = input_data.get("text") if kind == "http.request" and isinstance(input_data, dict) else None
            title = _text(original)
            if title and task["request"] is None:
                task["request"] = {
                    "text": title, "event_id": event["id"], "timestamp": stamp,
                    "association": event["association"].get("kind"),
                    "truncated": len(original) > len(title) or bool(event.get("warnings")),
                }
                task["title"] = " ".join(title.split())[:120]
            elif task["request"] is None and kind in {"life.mission.started", "life.planner.task_added"}:
                title = _text(payload.get("title"), 120) or _text(payload.get("objective"), 120)
                if title:
                    task["title"] = title
        summary = _EVENT_LABELS.get(kind, "公开运行事件")
        tool_name = _code(payload.get("tool_name"), None)
        if tool_name not in _TOOL_LABELS:
            tool_name = None
        if tool_name:
            summary = f"工具活动：{_TOOL_LABELS[tool_name]}"
        task["segments"].append({
            "id": event["id"], "event_id": event["id"], "role": role, "label": ROLES[role],
            "source_kind": "journal_event", "kind": kind, "started_at": stamp, "ended_at": None,
            "timestamp_basis": "source_reported", "role_evidence": [key for key in ("agent_layer", "role", "actor")
                                                                     if role != "unknown" and _role(payload.get(key)) == role],
            "status": _code(payload.get("status"), "observed"), "summary": summary, "tool_pairs": 0,
            "tool_name": tool_name,
            "content_withheld": not event["content_displayable"],
        })
        task["last_observed_at"] = max(task["last_observed_at"] or 0, stamp)

    def _episode(self, db, row, runtime, purpose, tenant, sid, grant, budget):
        role = _role(runtime.get("run_label"))
        key = _hash(_json(["pi_episode", tenant, sid, row["id"]]).encode())
        state = row["state"] if row["state"] in {"complete", "capturing", "quarantined", "interrupted"} else "unknown"
        episode = {
            "episode_id": row["id"], "sample_event_id": key, "role": role, "label": ROLES[role],
            "state": state, "started_at": row["started_at"],
            "ended_at": row["updated_at"] if state != "capturing" else None,
            "last_observed_at": row["updated_at"], "quality_approved": False, "quality_evidence": None,
            "tool_pairs": [], "sample_eligible": False,
            "role_evidence": "runtime.run_label" if role != "unknown" else None,
        }
        if runtime.get("capture_policy") == OBSERVED_POLICY:
            from .training_observations import observed_summary

            summary = observed_summary(db, row["id"])
            episode.update(
                capture_policy=OBSERVED_POLICY, observed_event_count=summary["event_count"],
                raw_available=bool(summary["event_count"]), tool_pairs=summary["tool_pairs"],
                tool_pairs_total=summary["tool_pairs_total"], tool_pairs_truncated=summary["tool_pairs_truncated"],
                collection_issues=summary["issues"], quality_status="not_evaluated",
            )
            if row["reason"]:
                episode["reason"] = _code(row["reason"], "capture_warning")
            return episode, 0
        if state != "complete":
            episode["reason"] = _code(row["reason"], "capture_not_settled")
            return episode, 0
        if row["runtime_profile"] not in {HOSTED_PROFILE, PROFILE}:
            episode["reason"] = "runtime_profile_unknown"
            return episode, 0
        record_limit = HOSTED_EPISODE_BYTES if row["runtime_profile"] == HOSTED_PROFILE else MAX_EPISODE_BYTES
        if row["size"] > record_limit:
            episode["reason"] = "display_episode_size_limit"
            return episode, 0
        if row["size"] > budget:
            episode["reason"] = "display_byte_limit"
            return episode, 0
        raw = db.execute("SELECT record FROM training_tool_episodes WHERE id=?", (row["id"],)).fetchone()[0]
        cache_key = (tenant, sid, row["id"], grant, row["runtime_profile"], _task_id(runtime.get("mission_id")),
                     _hash(raw.encode()))
        with self._cache_lock:
            validated = self._validated.get(cache_key)
        if validated is None:
            try:
                events = json.loads(raw)
                hosted = row["runtime_profile"] == HOSTED_PROFILE
                if (_hosted_sensitive(events, sid=sid, mission_id=runtime.get("mission_id"))
                        if hosted else _sensitive(events)):
                    raise ValueError
                sample = self.training.capture._sample(events, grant, hosted=hosted)
            except (ValueError, TypeError, KeyError, IndexError, RecursionError, OverflowError):
                episode["reason"] = "malformed_tool_episode"
                return episode, row["size"]
            validated = (_hash(_json(sample).encode()), self._pairs(events), len(events))
            with self._cache_lock:
                if len(self._validated) >= MAX_EPISODES:
                    self._validated.pop(next(iter(self._validated)))
                self._validated[cache_key] = validated
        sample_hash, pairs, event_count = validated
        episode["sample_eligible"] = True
        episode["raw_available"] = True
        episode["observed_event_count"] = event_count
        episode["tool_pairs"] = [dict(pair) for pair in pairs]
        review = db.execute(
            "SELECT * FROM training_sample_reviews WHERE tenant_id=? AND sid=? AND purpose=? AND event_id=?",
            (tenant, sid, purpose, key),
        ).fetchone()
        cutoff = self.analytics.clock() - min(30, self.analytics.retention_days) * 86400
        if (review is not None and cutoff <= review["reviewed_at"] <= self.analytics.clock()
                and review["sample_sha256"] == sample_hash
                and review["grant_at"] == grant and review["notice_version"] == self.analytics.notice_version
                and review["reviewer_kind"] in REVIEWER_KINDS and review["context_approved"]):
            episode["quality_approved"] = True
            episode["quality_evidence"] = {
                "reviewer_kind": review["reviewer_kind"], "human_reviewed": review["reviewer_kind"] == "human_operator",
                "evidence_sha256": review["evidence_sha256"], "reviewed_at": review["reviewed_at"],
            }
        return episode, row["size"]

    @staticmethod
    def _pairs(events):
        calls, pairs = {}, []
        for event in events:
            payload = event["payload"]
            if event["kind"] == "tool_call":
                calls[payload["toolCallId"]] = event
            elif event["kind"] == "tool_result":
                call = calls.get(payload["toolCallId"])
                if call is None:
                    continue
                pairs.append({
                    "call_id": payload["toolCallId"], "name": payload["toolName"],
                    "status": "error" if payload.get("isError") else "success",
                    "call_timestamp": call.get("observed_at"), "result_timestamp": event.get("observed_at"),
                    "result_characters": sum(len(block.get("text", "")) for block in payload.get("content", [])),
                })
        return pairs

    @staticmethod
    def _add_episode(task, episode):
        collection, quality = task["collection"], task["quality"]
        state = episode["state"]
        collection["states"][state] = collection["states"].get(state, 0) + 1
        collection["accepted_episodes"] += int(episode["sample_eligible"])
        collection["quarantined_episodes"] += int(state == "quarantined")
        collection["retained_episodes"] += int(episode.get("raw_available", False))
        collection["observed_events"] += episode.get("observed_event_count", 0)
        quality["candidates"] += int(episode["sample_eligible"])
        quality["approved_samples"] += int(episode["quality_approved"])
        task["segments"].append({
            "id": f"episode-{episode['episode_id']}", "episode_id": episode["episode_id"],
            "sample_event_id": episode["sample_event_id"], "role": episode["role"], "label": episode["label"],
            "source_kind": "tool_episode", "started_at": episode["started_at"], "ended_at": episode["ended_at"],
            "timestamp_basis": "observer_received", "role_evidence": episode["role_evidence"],
            "status": state, "summary": "角色过程采集", "tool_pairs": episode.get("tool_pairs_total", len(episode["tool_pairs"])),
            "quality_approved": episode["quality_approved"],
        })
        task["last_observed_at"] = max(task["last_observed_at"] or 0, episode["last_observed_at"])

    @staticmethod
    def _roles(segments):
        roles = {}
        for item in segments:
            role = item["role"]
            row = roles.setdefault(role, {"role": role, "label": ROLES[role], "observations": 0,
                                          "episodes": 0, "tool_pairs": 0})
            row["observations"] += 1
            row["episodes"] += int(item["source_kind"] == "tool_episode")
            row["tool_pairs"] += item["tool_pairs"]
        return list(roles.values())
