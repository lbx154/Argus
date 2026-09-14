import type { EventMsg } from './types.js';

function text(value: unknown, limit: number): string {
  const valueText = typeof value === 'string' ? value.trim() : '';
  return valueText.length > limit ? `${valueText.slice(0, limit - 1).trimEnd()}…` : valueText;
}

/** Explain the selected plan. Routing alone proves neither startup nor execution. */
export function managerIntentSummary(event: EventMsg, locale: 'en' | 'zh-CN'): string {
  const zh = locale === 'zh-CN';
  const route = text(event.route, 40);
  const mode = text(event.workflow_mode, 40);
  // Historical objective fields can contain the model transport context.
  // Only repeat the normalized public task here; the task card owns its title.
  const objective = text(event.execution_task, 160);
  let plan: string;
  if (route === 'self') {
    plan = zh ? '计划由 Manager 直接处理' : 'Manager plans to handle this request';
  } else if (mode === 'staged') {
    plan = route === 'team'
      ? (zh ? '计划由团队分阶段推进' : 'The team plans to work in stages')
      : (zh ? '计划分阶段推进' : 'The plan is to work in stages');
  } else if (mode === 'direct') {
    plan = route === 'team'
      ? (zh ? '计划由团队直接处理' : 'The team plans to handle this task directly')
      : (zh ? '计划直接处理这项工作' : 'The plan is to handle this task directly');
  } else {
    plan = zh ? '处理方案已确定' : 'A plan is ready';
  }
  if (objective) plan += `${zh ? '：' : ': '}${objective}`;

  let scope = '';
  if ((event.lifetime === 'standing' && event.open_ended !== false)
    || (!event.lifetime && event.open_ended === true)) {
    scope = zh ? '持续跟进后续工作。' : 'Keep following up on subsequent work.';
  } else if (event.lifetime === 'bounded_increment') {
    scope = zh ? '本次只推进一个明确的增量。' : 'Limit this run to one defined increment.';
  } else if (event.lifetime === 'bounded') {
    scope = event.continuous === true && event.open_ended === false
      ? (zh ? '完成这次目标后结束。' : 'Finish once this objective is met.')
      : (zh ? '范围限于本次目标。' : 'Keep the work within this objective.');
  }

  // Older producers used Division.headline() as the reason. It repeats internal
  // routing fields; the structured event retains those fields for diagnostics.
  const reason = text(event.reason, 320);
  const usefulReason = reason && !/^(?:\[manager\]|manager routed\b|\{)/i.test(reason)
    && reason !== (text(event.execution_task, 320) || text(event.objective, 320)) ? reason : '';
  return [plan, [scope, usefulReason].filter(Boolean).join(zh ? '' : ' ')].filter(Boolean).join('\n');
}
