// Three GPU replies from review-r22-inputs/two-replies-events.jsonl (2026-10-31).
// Retain dialogue and call boundaries; omit commands/usage and sanitize opaque identities.
import type { Recorded } from '../src/serve/views.js';

export const replyReplay: Recorded[] = [
  {
    "ts": "2026-10-31T05:21:02.410Z",
    "type": "message",
    "from": "operator",
    "text": "你有多少显卡？机器上",
    "request_id": "gpu-request-1"
  },
  {
    "ts": "2026-10-31T05:21:02.411Z",
    "type": "call.start",
    "role": "manager",
    "answers": "gpu-request-1"
  },
  {
    "ts": "2026-10-31T05:21:05.735Z",
    "type": "step",
    "role": "manager",
    "kind": "message",
    "text": "我查一下这台机器上的显卡数量。"
  },
  {
    "ts": "2026-10-31T05:21:09.095Z",
    "type": "call.end",
    "role": "manager",
    "callId": "gpu-call-3",
    "threadId": null,
    "completed": true,
    "error": null,
    "model": "gpt-6.1-sol",
    "usage": null,
    "ms": 6672
  },
  {
    "ts": "2026-10-31T05:21:09.095Z",
    "type": "handoff",
    "role": "manager",
    "to": "operator",
    "letter": "机器上有 **4 张 NVIDIA RTX A6000**，每张显存约 **45 GiB**，合计约 **180 GiB**。",
    "answers": "gpu-request-1"
  },
  {
    "ts": "2026-10-31T05:21:50.372Z",
    "type": "message",
    "from": "operator",
    "text": "有人在用吗",
    "request_id": "gpu-request-2"
  },
  {
    "ts": "2026-10-31T05:21:50.373Z",
    "type": "call.start",
    "role": "manager",
    "answers": "gpu-request-2"
  },
  {
    "ts": "2026-10-31T05:21:53.473Z",
    "type": "step",
    "role": "manager",
    "kind": "message",
    "text": "我查一下当前的显卡使用情况和运行进程。"
  },
  {
    "ts": "2026-10-31T05:21:59.297Z",
    "type": "call.end",
    "role": "manager",
    "callId": "gpu-call-8",
    "threadId": null,
    "completed": true,
    "error": null,
    "model": "gpt-6.1-sol",
    "usage": null,
    "ms": 8912
  },
  {
    "ts": "2026-10-31T05:21:59.298Z",
    "type": "handoff",
    "role": "manager",
    "to": "operator",
    "letter": "有，当前 **2、3 号卡正在使用**：\n\n- **2 号卡**：利用率 63%，占用约 15 GiB 显存。\n- **3 号卡**：利用率 100%，占用约 25 GiB 显存。\n- **1 号卡**：空闲，利用率 0%。\n- **0 号卡**：状态异常，多项指标显示 ERR!。\n\n进程列表没有显示使用者，所以目前无法判断是谁在用。",
    "answers": "gpu-request-2"
  },
  {
    "ts": "2026-10-31T05:22:20.915Z",
    "type": "message",
    "from": "operator",
    "text": "1号卡你看看是好的还是坏的",
    "request_id": "gpu-request-3"
  },
  {
    "ts": "2026-10-31T05:22:20.916Z",
    "type": "call.start",
    "role": "manager",
    "answers": "gpu-request-3"
  },
  {
    "ts": "2026-10-31T05:22:23.747Z",
    "type": "step",
    "role": "manager",
    "kind": "message",
    "text": "我检查一下 1 号卡的硬件状态，再尝试实际运行 CUDA 计算，确认它能不能正常使用。"
  },
  {
    "ts": "2026-10-31T05:22:42.225Z",
    "type": "call.end",
    "role": "manager",
    "callId": "gpu-call-13",
    "threadId": null,
    "completed": true,
    "error": null,
    "model": "gpt-6.1-sol",
    "usage": null,
    "ms": 21297
  },
  {
    "ts": "2026-10-31T05:22:42.225Z",
    "type": "handoff",
    "role": "manager",
    "to": "operator",
    "letter": "**1 号卡目前可以正常使用，基本检查通过。**\n\n- CUDA 初始化、创建上下文均成功。\n- 实测 64 MiB 显存写入和读回，数据校验通过。\n- 温度约 31°C，ECC 错误计数为 0。\n\n不过 PCI 部分查询显示 `Unknown Error`，所以还不能断言完全健康；这次验证了基本功能，尚未做长时间计算压力测试。",
    "answers": "gpu-request-3"
  }
];
