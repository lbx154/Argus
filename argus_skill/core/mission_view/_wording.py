"""Every sentence the mission view shows to a person, keyed by a stable code.

The reducers never write a human sentence directly. They name what happened
with a short code (``kind``) and ask :func:`say` for the sentence that goes
with it, in the language of the session when that language is knowable and
in plain English otherwise. The frontend localizes by the code; the sentence
stored next to it is what a reader sees when no mapping exists for their
language, so it has to stand on its own: a complete sentence that someone
outside the team understands, without the runtime's own vocabulary.

Technical facts (exit codes, retry counts, raw status strings) never go into
these sentences. Rows that need them carry a separate ``technical`` field.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

_CJK_RE = re.compile(r"[㐀-鿿]")
# A Latin word or number set into a Chinese sentence reads best with a thin
# gap on each side ("因为 Argus 被停止"); parameters are inserted raw, so the
# gap is added afterwards wherever the two scripts touch.
_CJK_THEN_LATIN_RE = re.compile(r"(?<=[㐀-鿿])(?=[A-Za-z0-9])")
_LATIN_THEN_CJK_RE = re.compile(r"(?<=[A-Za-z0-9])(?=[㐀-鿿])")

# (English, Chinese) for every code. Placeholders are filled by ``say``.
_SENTENCES: dict[str, tuple[str, str]] = {
    # Manager: understanding the request.
    "grounding_started": (
        "Reading the request to work out what it asks for",
        "正在阅读这项请求，弄清楚它要做什么",
    ),
    "goal_framed": (
        "The request has been understood and the goal for this project is set.",
        "已经理解这项请求，项目目标已经确定。",
    ),
    "goal_not_understood": (
        "I couldn't determine how to handle this request.",
        "没能确定该如何处理这项请求。",
    ),
    "goal_not_understood_detail": (
        "Nothing was queued. See the recorded diagnostic for details.",
        "没有安排任何工作；详情见记录的诊断信息。",
    ),
    "stage_advanced": (
        "The project moved on to the {stage} stage.",
        "项目进入{stage}阶段。",
    ),
    "stage_held": (
        "The project stays in the {stage} stage for now.",
        "项目暂时停留在{stage}阶段。",
    ),
    "stage_rolled_back": (
        "The project went back to the {stage} stage.",
        "项目回到{stage}阶段。",
    ),
    "stage_completed": (
        "The {stage} stage is complete.",
        "{stage}阶段已完成。",
    ),
    "stage_reconsidered": (
        "The {stage} stage was reconsidered.",
        "重新考虑了{stage}阶段。",
    ),
    # Planner: deciding what to do next.
    "planning_started": (
        "Planning the next piece of work",
        "正在规划下一步工作",
    ),
    "task_added": (
        "A new task was added to the plan.",
        "计划中加入了一项新任务。",
    ),
    "research_route_added": (
        "A new research route was added to the plan.",
        "计划中加入了一条新的研究路线。",
    ),
    "planning_complete": (
        "The plan for the next step is ready.",
        "下一步的计划已经排好。",
    ),
    "project_finished": (
        "The project has reached its goal; no further tasks are planned.",
        "项目已达成目标，不再安排新的任务。",
    ),
    "task_delivered": (
        "The task is complete and its result has been delivered.",
        "任务已完成，结果已交付。",
    ),
    "planner_waiting": (
        "Waiting for work outside Argus to finish before planning further",
        "等待 Argus 之外的工作完成后再继续规划",
    ),
    "planner_idle": (
        "Nothing is left to plan for now.",
        "目前没有需要规划的工作。",
    ),
    "planning_failed": (
        "Planning did not succeed.",
        "这次规划没有成功。",
    ),
    # Task lifecycle.
    "mission_started": (
        "Starting work on this task",
        "开始处理这项任务",
    ),
    "awaiting_engineer": (
        "Waiting for the Engineer to finish a round before checking it",
        "等待工程师完成一轮工作后再核对",
    ),
    "mission_completed": (
        "The task was completed.",
        "任务已完成。",
    ),
    "mission_continued": (
        "The task was completed and the project continues with the next one.",
        "任务已完成，项目继续进行下一项。",
    ),
    "mission_certified": (
        "The final submission was checked and approved.",
        "最终投稿经核对后获得认可。",
    ),
    "mission_incomplete": (
        "The task stopped with work still remaining.",
        "任务停止时仍有工作未完成。",
    ),
    "mission_stalled": (
        "The task stopped because recent rounds made no useful progress.",
        "最近几轮没有取得实质进展，任务已停止。",
    ),
    "mission_blocked": (
        "The task cannot continue until something outside it is resolved.",
        "任务需要先解决外部问题才能继续。",
    ),
    "mission_failed": (
        "The task could not be completed.",
        "任务没能完成。",
    ),
    "mission_paused": (
        "The task was paused before it finished because {why}; "
        "its progress is saved and it can be resumed.",
        "任务在完成前暂停，因为{why}；进度已保存，可以继续。",
    ),
    "mission_ended": (
        "The task ended without a recorded outcome.",
        "任务已结束，没有记录到明确的结果。",
    ),
    # Rounds of Engineer work.
    "round_in_progress": (
        "Working on round {round}",
        "正在进行第 {round} 轮工作",
    ),
    "round_started": (
        "Round {round} of work began.",
        "第 {round} 轮工作开始。",
    ),
    "progress_message": ("Reporting progress", "正在汇报进展"),
    "progress_command": ("Running a command", "正在运行命令"),
    "progress_reasoning": ("Thinking", "正在思考"),
    "progress_tool": ("Using a tool", "正在使用工具"),
    "progress_tool_result": ("Reading a tool's output", "正在查看工具输出"),
    "progress_waiting_model": (
        "Waiting for the model to respond",
        "正在等待模型回复",
    ),
    "progress_working": ("Working", "正在工作"),
    "venue_research_started": (
        "Studying what the target venue expects",
        "正在研究目标会议或期刊的要求",
    ),
    "venue_profile_ready": (
        "The target venue's expectations are written up.",
        "目标会议或期刊的要求已整理成文。",
    ),
    "venue_research_finished": (
        "Finished studying the target venue.",
        "已完成对目标会议或期刊的研究。",
    ),
    "idea_search_started": (
        "Searching for candidate research ideas",
        "正在寻找候选研究想法",
    ),
    "idea_candidates_ready": (
        "Candidate research ideas are ready.",
        "候选研究想法已整理好。",
    ),
    "engineer_round_finished": (
        "The Engineer finished this round; the results are ready to be checked.",
        "工程师完成了这一轮，结果已准备好供核对。",
    ),
    "checking_started": (
        "Reading the Engineer's results and checking the evidence",
        "正在阅读工程师的结果并核对证据",
    ),
    "continuing_before_check": (
        "Continuing to the next round before the results are checked",
        "先继续下一轮，稍后再核对结果",
    ),
    "check_postponed": (
        "The check of this round is postponed by one round",
        "这一轮的核对推迟一轮进行",
    ),
    # A round that produced no judgment, with the cause as a separate code.
    "round_not_judged": (
        "This round was not judged.",
        "这一轮没有人审阅。",
    ),
    "cause_engineer_service_dropped": (
        "The model service dropped the Engineer's session before it produced "
        "a result that could be checked, so there was nothing to judge. "
        "Argus retries in a fresh session.",
        "模型服务在工程师得出可核对的结果之前中断了会话，因此没有可以审阅"
        "的内容。Argus 会换一个新会话重试。",
    ),
    "cause_reviewer_unreachable": (
        "The Reviewer's session ended before it reached a conclusion. "
        "Argus tries the check again.",
        "审阅者的会话在得出结论前就结束了，Argus 会再试一次。",
    ),
    "cause_argus_stopped": (
        "Argus was stopped while this round was running; the work so far is saved.",
        "Argus 在这一轮进行中被停止；已完成的工作已保存。",
    ),
    "cause_task_cancelled": (
        "The task was cancelled at the operator's request.",
        "任务已应操作员的要求取消。",
    ),
    "cause_session_length_limit": (
        "The Engineer's session reached its length limit before finishing, "
        "so the same task continues in a fresh session with its progress kept.",
        "工程师的会话达到长度上限，同一任务会在新会话中继续，进度保留。",
    ),
    "cause_tools_unavailable": (
        "The tool environment the Engineer needs could not be started, "
        "so the work did not run.",
        "工程师需要的工具环境无法启动，工作没有运行。",
    ),
    "cause_model_unavailable": (
        "The configured model is unavailable, so neither the Engineer nor "
        "the Reviewer could run.",
        "配置的模型不可用，工程师和审阅者都无法运行。",
    ),
    "cause_sign_in_failed": (
        "Argus could not sign in to the model provider, so the work did not run.",
        "Argus 无法登录模型服务，工作没有运行。",
    ),
    "cause_paused": (
        "The work was paused before the Engineer finished this round because "
        "{why}; it resumes from the saved progress.",
        "在工程师完成这一轮之前，工作因为{why}而暂停；会从保存的进度继续。",
    ),
    "cause_unknown": (
        "The work did not reach a result that could be checked.",
        "执行没有得出可信的结果。",
    ),
    # Why a whole task stopped, when the runtime rather than a role wrote the
    # reason. These stand in for the runtime's own record in the task's
    # closing row; the record itself moves to the technical field.
    "task_stopped_reviewer_unreachable": (
        "The Reviewer could not reach a judgment {attempts} times in a row, so "
        "Argus stopped this task rather than settle a round without a real judgment.",
        "审阅者连续 {attempts} 次没能给出判断，Argus 选择停下这项任务，"
        "而不是在没有真正审阅的情况下结束一轮。",
    ),
    "task_stopped_reviewer_unreachable_repeatedly": (
        "The Reviewer repeatedly could not reach a judgment, so Argus stopped "
        "this task rather than settle a round without a real judgment.",
        "审阅者多次没能给出判断，Argus 选择停下这项任务，"
        "而不是在没有真正审阅的情况下结束一轮。",
    ),
    "task_stopped_engineer_service_dropped": (
        "The model service kept dropping the Engineer's session before it "
        "produced a result that could be checked, so Argus stopped this task; "
        "it can be retried later.",
        "模型服务一再在工程师得出可核对的结果之前中断会话，Argus 停下了这项任务；"
        "之后可以重试。",
    ),
    "task_stopped_argus_stopped": (
        "Argus was stopped while this task was running; its progress is saved.",
        "Argus 在这项任务进行中被停止；进度已保存。",
    ),
    "task_stopped_retry_wait_cut_short": (
        "Argus was stopped while waiting to retry after repeated model-service "
        "failures; the task can resume from its saved progress.",
        "Argus 在等待重试（此前模型服务多次失败）时被停止；任务可以从保存的进度继续。",
    ),
    # Judgments on a round.
    "self_check_accepted": (
        "The Engineer's own check of the result was accepted.",
        "工程师的自检结果被接受。",
    ),
    "self_check_held_up": (
        "The Engineer checked its own result and it held up",
        "工程师自行核验了结果，结果成立",
    ),
    "independent_check_not_needed": (
        "No independent check was needed for this round",
        "这一轮不需要独立核对",
    ),
    "results_accepted": (
        "The results were checked and accepted.",
        "结果经核对后被接受。",
    ),
    "another_attempt_requested": (
        "The results were not accepted; another attempt was requested.",
        "结果未被接受，需要再试一次。",
    ),
    "attempt_blocked": (
        "The results could not be accepted, and the work cannot continue "
        "until something outside it is resolved.",
        "结果未被接受，且需要先解决外部问题才能继续。",
    ),
    "replan_requested": (
        "The results were checked; the plan needs to change before the work continues.",
        "结果已核对，计划需要调整后才能继续。",
    ),
    "results_not_final": (
        "The results were checked; the research is not finished yet.",
        "结果已核对，研究尚未完成。",
    ),
    "next_action_prefix": ("Next action: ", "下一步："),
    # Role states written outside the event stream.
    "handed_off": (
        "Finished its part and passed the work on.",
        "已完成自己的部分，工作交给下一个角色。",
    ),
    "waiting": ("Waiting", "等待中"),
    # Capabilities and knowledge Argus keeps.
    "capability_learned": (
        "Argus learned a new capability.",
        "Argus 学会了一项新能力。",
    ),
    "capability_improved": (
        "Argus improved a capability it already had.",
        "Argus 改进了一项已有能力。",
    ),
    "capability_shared": (
        "A capability was promoted for use across projects.",
        "一项能力被提升为跨项目可用。",
    ),
    "knowledge_captured": (
        "A new knowledge page was written.",
        "新写了一页知识记录。",
    ),
    "knowledge_refined": (
        "A knowledge page was revised.",
        "修订了一页知识记录。",
    ),
    "knowledge_retired": (
        "A knowledge page was retired.",
        "一页知识记录已退役。",
    ),
    "knowledge_promoted": (
        "A knowledge page was promoted.",
        "一页知识记录被提升。",
    ),
    "knowledge_demoted": (
        "A knowledge page was demoted.",
        "一页知识记录被降级。",
    ),
}

# Stage identifiers are machine codes; readers of a Chinese session get the
# plain name. Unknown stages keep their identifier with the underscores
# replaced, which reads acceptably in both languages.
_STAGE_NAMES_ZH = {
    "idea": "选题",
    "experiment": "实验",
    "paper": "论文",
    "review": "评审",
    "paper_review": "论文评审",
    "implementation": "实现",
    "verification": "验证",
    "planning": "规划",
    "writing": "写作",
    "analysis": "分析",
    "design": "设计",
    "submission": "投稿",
    "run": "运行",
}


def say(kind: str, chinese: bool, **params: Any) -> str:
    """Return the sentence for *kind* in the requested language."""
    english, zh = _SENTENCES[kind]
    sentence = zh if chinese else english
    if not params:
        return sentence
    filled = sentence.format(**params)
    if chinese:
        filled = _LATIN_THEN_CJK_RE.sub(" ", _CJK_THEN_LATIN_RE.sub(" ", filled))
    return filled


def uses_cjk(text: str) -> bool:
    """Whether *text* is written in a CJK script.

    The same test ``argus_skill.core.operator_messages.uses_cjk`` applies to
    chat replies; it is repeated here so the read model does not import the
    chat transport.
    """
    return bool(_CJK_RE.search(str(text or "")))


def remember_language(view: dict[str, Any], text: str) -> None:
    """Record the session's language from the operator's own words."""
    sample = str(text or "").strip()
    if sample:
        view["language"] = "zh" if uses_cjk(sample) else "en"


def session_is_chinese(view: Mapping[str, Any], *texts: str) -> bool:
    """Whether sentences for this view should be written in Chinese.

    The operator's original request settles it once it has been seen; before
    that, the current task's own wording and any *texts* offered by the caller
    decide.
    """
    recorded = str(view.get("language") or "")
    if recorded == "zh":
        return True
    if recorded == "en":
        return False
    mission = view.get("mission") or {}
    sample = "\n".join(
        str(part or "")
        for part in (mission.get("objective"), mission.get("title"), *texts)
    )
    return uses_cjk(sample)


def stage_name(stage_id: str, chinese: bool) -> str:
    """The stage as a reader would name it; the identifier stays the code."""
    stage = str(stage_id or "").strip()
    if not stage:
        return ""
    if chinese:
        return _STAGE_NAMES_ZH.get(stage, f" {stage.replace('_', ' ')} ")
    return stage.replace("_", " ")


def stage_label(stage_id: str, chinese: bool) -> str:
    """The stage's display label for ``view["stage"]``."""
    name = stage_name(stage_id, chinese).strip()
    return name if chinese else name.title()


__all__ = [
    "remember_language",
    "say",
    "session_is_chinese",
    "stage_label",
    "stage_name",
    "uses_cjk",
]
