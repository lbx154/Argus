import { act, create } from "react-test-renderer";
import { describe, expect, it } from "vitest";
import { CardNow, LiveLine } from "../map/LiveLine";
import { renderToStaticMarkup } from 'react-dom/server';
import { latestManagerMessage, managerDoing, roleActionLabel } from '../lib/taskLanguage';

const text = (root: ReturnType<typeof create>) =>
  root.root.findAllByType("span").map((s) => s.children.join("")).join(" ");

describe("the living status line", () => {
  it('keeps closing verdicts out of ended task summaries and preserves request answers and running progress', () => {
    const task = { id: 't1', title: 'Compute results', role: 'reviewer', status: 'done',
      latest_action: { role: 'reviewer', text: 'Accepted, ready for the operator.', ts: 1 } };
    expect(renderToStaticMarkup(<CardNow task={task} zh />)).toBe('');
    expect(renderToStaticMarkup(<CardNow task={{ ...task, status: 'in_progress' }} zh />)).toContain(task.latest_action.text);
    expect(renderToStaticMarkup(<CardNow task={{ ...task, turn_kind: 'qa' }} zh />)).toContain(task.latest_action.text);
    expect(task.latest_action.text).toBe('Accepted, ready for the operator.');
  });
  it("names the role at work and how long it has been at it", () => {
    const since = Date.now() / 1000 - 12 * 60 - 3;
    let root!: ReturnType<typeof create>;
    act(() => { root = create(<LiveLine role="engineer" since={since} zh={false} />); });
    expect(text(root)).toBe("Working on the current task · 12 min this run");
    act(() => root.unmount());
  });
  it("speaks Chinese when the map does, and stays quiet about time it does not know", () => {
    let root!: ReturnType<typeof create>;
    act(() => { root = create(<LiveLine role="reviewer" since={null} zh />); });
    expect(text(root)).toBe("正在检查结果是否正确");
    act(() => root.unmount());
  });
  it('keeps global Manager narration out of the card’s live line', () => {
    // The TS rows stream's projection of call.start and step/message records.
    const records = [
      { type: 'provider.request.started', agent_layer: 'manager', call_id: 'c1' },
      { type: 'engineer.progress', agent_layer: 'manager', kind: 'agent_message', text: '我正在核对你刚才提出的地图问题。' },
      { type: 'engineer.progress', agent_layer: 'manager', kind: 'agent_message', text: '已找到长路径的显示入口，正在检查手机详情。' },
      { type: 'engineer.progress', agent_layer: 'engineer', kind: 'agent_message', text: '工程师的另一项任务。' },
    ];
    const message = latestManagerMessage(records);
    let root!: ReturnType<typeof create>;
    expect(message).toBe('已找到长路径的显示入口，正在检查手机详情。');
    act(() => { root = create(<LiveLine role="manager" zh />); });
    expect(text(root)).toBe('正在回复你的消息');
    expect(latestManagerMessage([...records, { type: 'provider.request.started', agent_layer: 'manager', call_id: 'c2' }])).toBe('');
    expect(roleActionLabel('manager', false)).toBe('Replying to your message');
    act(() => root.unmount());
  });
});

it('says what the Manager is at since it last spoke: its latest thought\'s heading, or the command it runs', () => {
  const step = (kind: string, text: string) => ({ type: 'engineer.progress', agent_layer: 'manager', kind, text });
  const started = { type: 'provider.request.started', agent_layer: 'manager' };
  const said = step('agent_message', '我先看看。');
  const thought = step('reasoning', '**Inspecting the logs**\n\nThe loss is flat.');
  const ran = step('command_execution', "/usr/bin/bash -lc 'tail -n 5 train.log'");
  expect(managerDoing([started, said, thought])).toBe('Inspecting the logs');
  expect(managerDoing([started, thought, ran])).toBe('tail -n 5 train.log');
  expect(managerDoing([started, thought, said])).toBe('');
  expect(managerDoing([thought, started])).toBe('');
});
