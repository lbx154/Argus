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
  // The request is the operator's own words; the plan line does not say it back.
  let plan: string;
  if (route === 'self') {
    plan = zh ? '我直接来处理。' : "I'll handle this myself.";
  } else if (mode === 'staged') {
    plan = route === 'team'
      ? (zh ? '交给团队，分几个阶段做。' : 'Handing this to the team, in stages.')
      : (zh ? '分几个阶段做。' : 'Working on this in stages.');
  } else if (mode === 'direct') {
    plan = route === 'team'
      ? (zh ? '交给团队来做。' : 'The team will take this on.')
      : (zh ? '直接做。' : 'Doing this directly.');
  } else {
    plan = zh ? '处理方式定好了。' : 'The approach is set.';
  }

  let scope = '';
  if ((event.lifetime === 'standing' && event.open_ended !== false)
    || (!event.lifetime && event.open_ended === true)) {
    scope = zh ? '之后会持续跟进。' : "I'll keep following up after this.";
  } else if (event.lifetime === 'bounded_increment') {
    scope = zh ? '先做一部分，做完再看下一步。' : 'One piece first, then I decide what comes next.';
  } else if (event.lifetime === 'bounded') {
    scope = event.continuous === true && event.open_ended === false
      ? (zh ? '这件事做完就收尾。' : 'This wraps up once it is done.')
      : (zh ? '只做这次的目标。' : 'Only this goal is in scope.');
  }

  // Older producers used Division.headline() as the reason. It repeats internal
  // routing fields; the structured event retains those fields for diagnostics.
  const reason = text(event.reason, 320);
  const usefulReason = reason && !/^(?:\[manager\]|manager routed\b|\{)/i.test(reason)
    && reason !== (text(event.execution_task, 320) || text(event.objective, 320)) ? reason : '';
  return [plan, [scope, usefulReason].filter(Boolean).join(zh ? '' : ' ')].filter(Boolean).join('\n');
}
