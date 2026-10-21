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
    "requested_work_done": ("All the requested work is done.", "要求的工作已经全部完成。"),
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
    "planner_no_new_work": (
        "Nothing new to plan until the current work moves.",
        "手头的工作还没做完，先不安排新的。",
    ),
    "planner_idle": (
        "Nothing is left to plan for now.",
        "目前没有需要规划的工作。",
    ),
    "planning_failed": (
        "Planning did not succeed.",
        "这次规划没有成功。",
    ),
    # What the operator is told while work waits or stops. Each names what
    # happened and what happens next, never the mechanism behind it.
    "waiting_for_answer": (
        "Waiting for your answer: {question} (work that doesn't need it carries on)",
        "在等你回答：{question}（不需要这个答复的工作照常进行）",
    ),
    "planner_needs_direction": (
        "The Planner can't see a next step and is asking you for a direction.",
        "规划者拿不准接下来做什么，在等你定个方向。",
    ),
    "planner_failing": (
        "The Planner hasn't had an answer from the model service for {minutes} "
        "minutes. Argus keeps trying at a slower pace; if this goes on, switch "
        "the Planner's model in Settings.",
        "规划者连着 {minutes} 分钟没能从模型服务拿到回应，Argus 会放慢节奏继续试；"
        "如果一直这样，可以在设置里换一个规划者用的模型。",
    ),
    "planner_internal_error": (
        "Planning has hit an internal error in Argus for {minutes} minutes. "
        "Argus keeps trying at a slower pace; please report it with the run log.",
        "规划时 Argus 自己出错已经 {minutes} 分钟了，会放慢节奏继续试；"
        "麻烦把运行日志一起反馈给我们。",
    ),
    "planning_paused_host": (
        "The program that runs code for Argus could not start, so planning is "
        "paused. Reinstall Codex, then turn continuous work back on to try again.",
        "为 Argus 运行代码的程序启动不了，规划先停下了。把 Codex 装好后，"
        "重新开启持续工作就会再试。",
    ),
    "planning_paused_content_filter": (
        "The model service's content filter refused this plan, so work is "
        "paused. Please rephrase the goal.",
        "模型服务的内容过滤拦下了这次规划，工作先暂停；请换个说法描述目标。",
    ),
    "work_paused_service": (
        "The model service kept turning work away after every automatic retry, "
        "so Argus has stopped calling it. Click Run, switch the backend or "
        "model, or send a message to try again.",
        "模型服务在自动重试后仍然拒绝工作，Argus 先不再调用它。点“运行”、"
        "换个后端或模型，或者发条消息，就会再试。",
    ),
    "work_paused_internal": (
        "Argus keeps hitting the same internal error, so new work is on hold "
        "until a fixed Argus is installed.",
        "Argus 自己反复遇到同一个内部错误，新的工作先停下，等装上修好的版本再继续。",
    ),
    "service_back": (
        "The model service is answering again; paused work picks up where it stopped.",
        "模型服务恢复了，之前停下的工作会接着做。",
    ),
    "project_report_pending": (
        "The project is done, but its summary isn't written yet; Argus will try "
        "again shortly.",
        "项目已经完成，只是总结还没写好，Argus 过一会儿再试。",
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
        "You cancelled this task.",
        "你取消了这项任务。",
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
    # A round that never had a working model service behind it. ``cause`` is
    # the line of the runner's record that names what was wrong, kept as the
    # CLI wrote it ("connect ECONNREFUSED 127.0.0.1:18765"): a local address
    # or path is what makes the sentence actionable.
    "cause_service_unreachable": (
        "The model service could not be reached: {cause}. "
        "Argus pauses this task and retries it after a short wait.",
        "模型服务连不上：{cause}。Argus 会暂停这项任务，稍后重试。",
    ),
    "cause_service_error": (
        "The model service returned an error: {cause}. "
        "Argus pauses this task and retries it after a short wait.",
        "模型服务返回了错误：{cause}。Argus 会暂停这项任务，稍后重试。",
    ),
    "cause_service_tls": (
        "The secure connection to the model service could not be established: "
        "{cause}. Argus pauses this task and retries it after a short wait.",
        "无法与模型服务建立安全连接：{cause}。Argus 会暂停这项任务，稍后重试。",
    ),
    "cause_service_proxy": (
        "The proxy in front of the model service failed: {cause}. "
        "Argus pauses this task and retries it after a short wait.",
        "模型服务前的代理出了问题：{cause}。Argus 会暂停这项任务，稍后重试。",
    ),
    "cause_cli_missing": (
        "The model CLI could not be started: {cause}. "
        "Argus pauses this task and retries it after a short wait.",
        "模型命令行工具无法启动：{cause}。Argus 会暂停这项任务，稍后重试。",
    ),
    "cause_service_quota": (
        "The model service reported that its quota is used up: {cause}. "
        "Argus pauses this task and retries it after a short wait.",
        "模型服务提示配额已用完：{cause}。Argus 会暂停这项任务，稍后重试。",
    ),
    "cause_model_catalog": (
        "The model service could not list its models: {cause}. "
        "Argus pauses this task and retries it after a short wait.",
        "模型服务无法列出可用模型：{cause}。Argus 会暂停这项任务，稍后重试。",
    ),
    "cause_sign_in": (
        "Argus could not sign in to the model service: {cause}. The work did not run.",
        "Argus 无法登录模型服务：{cause}。工作没有运行。",
    ),
    # The same causes as clauses, completing "The task was paused because …".
    "why_service_unreachable": (
        "the model service could not be reached ({cause})",
        "模型服务连不上（{cause}）",
    ),
    "why_service_error": (
        "the model service returned an error ({cause})",
        "模型服务返回了错误（{cause}）",
    ),
    "why_service_tls": (
        "the secure connection to the model service could not be established ({cause})",
        "无法与模型服务建立安全连接（{cause}）",
    ),
    "why_service_proxy": (
        "the proxy in front of the model service failed ({cause})",
        "模型服务前的代理出了问题（{cause}）",
    ),
    "why_cli_missing": (
        "the model CLI could not be started ({cause})",
        "模型命令行工具无法启动（{cause}）",
    ),
    "why_service_quota": (
        "the model service reported that its quota is used up ({cause})",
        "模型服务提示配额已用完（{cause}）",
    ),
    "why_model_catalog": (
        "the model service could not list its models ({cause})",
        "模型服务无法列出可用模型（{cause}）",
    ),
    "why_sign_in": (
        "Argus could not sign in to the model service ({cause})",
        "Argus 无法登录模型服务（{cause}）",
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
    # Questions Argus itself puts to the operator.
    "ask_direction": (
        "I'm not sure which direction to take next.\nYou can name a direction, "
        "supply the missing decision or resource, or tell me to stop.",
        "接下来往哪个方向做，我拿不准。\n你可以指定方向、补上缺的决定或资源，或者让我先停下。",
    ),
    "ask_direction_because": (
        "I'm not sure which direction to take next: {why}\nYou can name a direction, "
        "supply the missing decision or resource, or tell me to stop.",
        "接下来往哪个方向做，我拿不准：{why}\n你可以指定方向、补上缺的决定或资源，或者让我先停下。",
    ),
    "ask_adopt_reviewed_change": (
        "The Reviewer read the change for “{title}” and it holds. "
        "Publish this reviewed version and use it at the next task boundary?",
        "审阅者看过“{title}”的改动，结论成立。要发布这个审阅过的版本，并在下一项任务开始时换上吗？",
    ),
    "ask_fix_model_configuration": (
        "The model configuration for this task has failed the same way {streak} "
        "times in a row: {reason} I have stopped retrying this configuration so the "
        "failures stop costing money. Fix the model name or provider access, then "
        "reply here to resume this task — or tell me to drop it.",
        "这项任务用的模型配置已经连续 {streak} 次以同样的方式失败：{reason} 为了不再白花钱，"
        "我已停止用这个配置重试。修好模型名称或服务的访问权限后在这里回复，就会继续这项任务；"
        "也可以让我放下它。",
    ),
    # A task's update in the operator's conversation.
    "this_task": ("the current task", "这项任务"),
    "update_done": ("Done: {subject}.", "完成了：{subject}。"),
    "update_canceled": ("Canceled: {subject}.", "已取消：{subject}。"),
    "update_continuing": ("Still working on {subject}.", "还在继续：{subject}。"),
    "update_stuck": ("Stuck for now: {subject}.", "「{subject}」暂时做不下去了。"),
    "update_new_approach": ("Needs a different approach: {subject}.", "「{subject}」需要换个做法。"),
    "update_not_finished": ("Couldn't finish: {subject}.", "没能完成：{subject}。"),
    "update_provider_fenced": (
        "The model provider is restricting calls, so {subject} is paused.",
        "模型服务限制了调用，「{subject}」先停下了。",
    ),
    "update_run_to_continue": (
        "Once it is resolved, click Run to continue; nothing retries on its own until then.",
        "问题解决后点“运行”就能继续；在那之前不会自动重试。",
    ),
    "update_reason": ("Reason: {why}", "原因：{why}"),
    "update_next": ("Next: {action}", "接下来：{action}"),
    "update_advice": ("Next: {action}", "建议：{action}"),
    "update_your_call": ("Your call: {action}", "需要你定：{action}"),
    "update_needs_decision": (
        "I need your decision before this can continue.",
        "需要你定一下才能继续。",
    ),
    "update_will_investigate": (
        "Next: I'll find out what went wrong and pick a way forward.",
        "接下来：我先查清原因，再找个能走通的办法。",
    ),
    # What was decided while the operator could not be asked.
    "assumed_head": (
        "Decided without asking you (please look these over):",
        "这些是没问你就定下的，请过目：",
    ),
    "assumed_by_manager": ("the Manager decided", "经理替你定了"),
    "assumed_by_me": ("I went with", "我按这个理解做的"),
    "assumed_choice": ("{conflict} → {verb}: {reading}", "{conflict} → {verb}：{reading}"),
    "assumed_reading": ("{verb}: {reading}", "{verb}：{reading}"),
    "assumed_conflict": (
        "{conflict} (the reading chosen is in CHECKPOINT.md)",
        "{conflict}（选定的理解记在 CHECKPOINT.md）",
    ),
    # Notices Argus posts to the operator's conversation on its own.
    "planner_retries_stopped_internal": (
        "Planning kept hitting an internal error in Argus for three hours, so I "
        "stopped retrying. Send me a message to try again, and please report it "
        "along with the run log.",
        "规划时 Argus 自己连着出错三个小时，我先不再自动重试。"
        "给我发条消息就会再试；也请把运行日志一起反馈给我们。",
    ),
    "planner_retries_stopped_service": (
        "The Planner's model service kept failing for three hours, so I stopped "
        "retrying. Send me a message, or switch the Planner's model in Settings, "
        "to try again.",
        "规划者用的模型服务连着三个小时都失败，我先不再自动重试。"
        "给我发条消息，或者在设置里换一个规划者用的模型，就会再试。",
    ),
    "wrap_up_paused": (
        "I've tried to wrap this project up {count} times in a row, and each time it "
        "was turned back for the same reason: {reason}\nI'm pausing those attempts so "
        "they stop costing money. I'll try again when the work changes or you reply "
        "here; if the requirement itself is wrong, tell me and I'll take a different path.",
        "我已经连续 {count} 次想收尾，每次都因为同一个原因被退回：{reason}\n"
        "为了不再白花钱，我先不试了。等任务有变化或你回复后再试；"
        "如果这个要求本身不对，直接告诉我，我换个做法。",
    ),
    "background_calls_paused": (
        "The background made {calls} model calls in a row without any progress: no "
        "new task, no finished work, and no message from you. I've stopped those "
        "background calls so they stop costing money; running tasks are not affected. "
        "They resume as soon as something changes or you reply here.",
        "后台连着调用了 {calls} 次模型，一直没有进展：没有新任务、没有做完的工作，"
        "你也没发新消息。为了不再白花钱，我先停了这些后台调用，正在做的任务不受影响。"
        "一有新进展，或者你在这里回一句，就会恢复。",
    ),
    "manager_model_mismatch": (
        "The model the Manager uses ({model}) failed several times in a row to work "
        "out how to handle requests, so nothing was started. Pick another model for "
        "the Manager in Settings → Advanced settings, restart, and try again.",
        "现在给经理用的模型（{model}）连续几次都没弄懂该怎么处理请求，所以这次没有开始任何工作。"
        "请在“设置 → 高级设置”里给经理换一个模型，重启后再试。",
    ),
    "proxy_undecided": (
        "I couldn't decide “{title}” for you, so it waits for your answer.",
        "“{title}”我没法替你决定，等你回来再定。",
    ),
    # Replies in the conversation with the operator.
    "working_out_reply": ("Working out a reply…", "正在想怎么回复…"),
    "understanding_request": ("Understanding your request…", "正在理解你的请求…"),
    "steer_unclear": (
        "It sounds like you want to change direction, but I couldn't tell how, "
        "so nothing has changed yet. Could you say a bit more?",
        "听起来你想调整当前任务的方向，但我没听出具体要怎么改，所以先没动。能再说具体一点吗？",
    ),
    "steer_taken": ("Got it, carrying on in that direction.", "好，接下来按你说的方向做。"),
    "steer_taken_ongoing": (
        "Got it, carrying on in that direction. This is now ongoing work; "
        "after this round I'll plan the next piece.",
        "好，接下来按你说的方向做。这件事改成长期做下去，这一轮做完会接着安排下一项。",
    ),
    "steer_taken_not_ongoing": (
        "Got it, carrying on in that direction. The direction is noted, but I "
        "couldn't make this ongoing work; the current setup stays as it is.",
        "好，接下来按你说的方向做。方向记下了，但没能改成长期任务，原来的安排不变。",
    ),
    "paused": (
        "Paused. Everything so far is saved; I'll pick it up when you say continue.",
        "已暂停。做到的部分都保存了，你说继续我再接着做。",
    ),
    "paused_stopping": (
        "Paused; stopping what was running. Everything so far is saved; "
        "I'll pick it up when you say continue.",
        "已暂停，正在停下手上的事。做到的部分都保存了，你说继续我再接着做。",
    ),
    "gathering_options": ("Putting together options for this task…", "正在结合你的任务整理几个选项…"),
    "options_failed": (
        "I couldn't put the options together just now. Your task and answer are "
        "kept; please try again in a moment.",
        "这次没能整理出选项，你的任务和回答都保留着，稍后再试一次吧。",
    ),
    "retry_no_action": ("Nothing was actually done yet; trying once more…", "还没真正动手，再试一次…"),
    "retry_resumed": (
        "The model stopped responding; picking the conversation up again…",
        "模型这次没有回应，接着刚才的对话再试一次…",
    ),
    "retry_stalled": ("The model stopped responding; trying once more…", "模型这次没有回应，再试一次…"),
    "retry_dropped": (
        "The connection to the model service dropped; trying once more…",
        "连接模型服务时断了，再试一次…",
    ),
    "reply_empty": (
        "No reply came back for this request. No new task was added, and the "
        "current task was not changed.",
        "这次没有收到回复。没有新增任务，正在进行的任务也没有改变。",
    ),
    "reply_cut_off": ("The model didn't finish its answer.", "模型这次没把话说完。"),
    "reply_stopped_midway": (
        "This stopped before it finished: {said}\n\nThe progress messages above "
        "don't mean it's done, and some files may be half-written. No new task was added.",
        "这次没做完就停下了：{said}\n\n"
        "上面的进度消息不代表已经做完，可能留下了做到一半的文件；没有新增任务。",
    ),
    "reply_stopped_before_reply": (
        "This stopped before I could reply: {said}\n\n"
        "No new task was added, and the current work is unchanged.",
        "这次还没回复就停下了：{said}\n\n没有新增任务，正在做的事也没变。",
    ),
    "reply_incomplete": ("No completed reply was received.", "没有收到完整回复。"),
    "reply_failed": (
        "This request did not finish. No new task was added.\n\nReason: {said}",
        "这次没能处理完你的请求，没有新增任务。\n\n原因：{said}",
    ),
    # The first reply to a task the operator hands over.
    "ack_plan_awaits_start": (
        "The plan is ready, but nothing has started. Check what I understood and "
        "the plan in the card above; work starts when you press Start.",
        "计划排好了，还没开始。请在上面的卡片里看看我的理解和计划，点“开始执行”后才会开始。",
    ),
    "ack_needs_your_call": ("Before I start, I need your call:\n{question}", "开始之前需要你定一下：\n{question}"),
    "ack_waits_for_slot": (
        "Got it. Everything that can run at once is busy; this starts as soon "
        "as a slot frees up, no need to resend.",
        "记下了。现在同时在做的事已经满了，空出来就开始，不用重发。",
    ),
    "ack_checking_start": (
        "Got it. Checking whether it can start; I'll let you know.",
        "记下了，正在确认能不能开始，稍后告诉你。",
    ),
    "ack_start_failed": (
        "Saved, but the work couldn't start. Your request is kept; see the "
        "startup details for why, or try again later.",
        "记下了，但没能开始工作。你的要求已经保存，可以点“启动详情”看原因，或者稍后再试。",
    ),
    "ack_already_asked": (
        "You already asked for this ({state}); I didn't add it again.",
        "这件事之前已经交代过了（{state}），没有再加一遍。",
    ),
    "asked_pending": ("not started yet", "还没开始"),
    "asked_running": ("in progress", "正在做"),
    "asked_done": ("done", "已经做完"),
    "asked_parked": ("paused", "暂停着"),
    "asked_failed": ("it didn't succeed last time", "上次没做成"),
    "asked_saved": ("saved", "已经记下"),
    "ack_after_current": ("Got it. This starts once the current work is done.", "收到，等手头这项做完就开始。"),
    "ack_picked_up": ("Got it, it's been picked up.", "收到，已经接手了。"),
    "ack_goal_updated": ("Goal updated; working out the next step.", "目标已更新，正在安排下一步。"),
    "ack_starting": ("Got it, starting now.", "收到，马上开始。"),
    "ack_not_recorded": (
        "The task is saved; only this confirmation couldn't be recorded.",
        "任务已经记下了，只是这条确认没能保存。",
    ),
    # Replies to the operator's answer to a question.
    "answer_taken": ("Got it, carrying on with your choice.", "收到，按你定的继续做。"),
    "answer_already_taken": ("I already have this decision.", "这个决定之前已经收到了。"),
    "answer_not_applied": (
        "That answer couldn't be applied: the question may already be answered or gone.",
        "这个回答没能用上：问题可能已经答过，或者已经不在了。",
    ),
    "answered_for_operator": (
        "You weren't available, so I chose for you: {choice}",
        "这个问题你暂时没空回答，我先替你选了：{choice}",
    ),
    "answer_stopped": ("Stopped. The work done so far is kept.", "已停下，做到的部分都保留着。"),
    "answer_dropped": (
        "Dropped this task; the rest of the work carries on.",
        "已放下这项任务，其余工作照常。",
    ),
    "answer_dropped_while_stopped": (
        "Dropped this task. Everything else stays stopped until you resume it.",
        "已放下这项任务。在你恢复之前，其余工作先停着。",
    ),
    "campaign_stays_stopped": (
        "Everything else stays stopped until you resume it.",
        "在你恢复之前，其余工作先停着。",
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


def uses_cjk(*texts: Any) -> bool:
    """Whether any of *texts* is written in a CJK script: the operator writes Chinese."""
    return any(_CJK_RE.search(str(text or "")) for text in texts)


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
