import { act, create } from "react-test-renderer";
import { describe, expect, it } from "vitest";
import { LiveLine } from "../map/LiveLine";
import { latestManagerMessage, roleActionLabel } from '../lib/taskLanguage';

const text = (root: ReturnType<typeof create>) =>
  root.root.findAllByType("span").map((s) => s.children.join("")).join(" ");

describe("the living status line", () => {
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
  it('uses the current Manager message and clears it at the next call boundary', () => {
    // The TS rows stream's projection of call.start and step/message records.
    const records = [
      { type: 'provider.request.started', agent_layer: 'manager', call_id: 'c1' },
      { type: 'engineer.progress', agent_layer: 'manager', kind: 'agent_message', text: '我正在核对你刚才提出的地图问题。' },
      { type: 'engineer.progress', agent_layer: 'manager', kind: 'agent_message', text: '已找到长路径的显示入口，正在检查手机详情。' },
      { type: 'engineer.progress', agent_layer: 'engineer', kind: 'agent_message', text: '工程师的另一项任务。' },
    ];
    const message = latestManagerMessage(records);
    let root!: ReturnType<typeof create>;
    act(() => { root = create(<LiveLine role="manager" message={message} zh />); });
    expect(text(root)).toBe('已找到长路径的显示入口，正在检查手机详情。');
    expect(latestManagerMessage([...records, { type: 'provider.request.started', agent_layer: 'manager', call_id: 'c2' }])).toBe('');
    act(() => root.update(<LiveLine role="manager" message="" zh />));
    expect(text(root)).toBe('正在回复你的消息');
    expect(roleActionLabel('manager', false)).toBe('Replying to your message');
    act(() => root.unmount());
  });
});
