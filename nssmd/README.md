# nssmd — 修改方法论

这个目录记录我（`nssmd`）在 argus-skill 仓库做修改时遵循的工作流程。每次修改前先读这里的 skill，确保流程一致。

## 核心原则

1. **永远先 pull 再改**。上游可能在你思考时已经动过。
2. **测试驱动加新模块**：先写测试，再写实现，至少一个端到端测试证明集成路径走通。
3. **找现有钩子，不要重构**：90% 的"集成"问题是找对接入点。`grep` 找已有的同类模式，挂上去；不要新建并行架构。
4. **改完立刻 push**：本地不留长时间未推的状态，避免和上游分叉。
5. **不带 Claude / HAPI 字样**：commit 用 `Co-Authored-By: nssmd <nssmd@noreply.local>`。

## Skill 索引

按调用顺序：

- [`skills/00-pull-first.md`](skills/00-pull-first.md) — 任何修改前的 pull / stash / 冲突处理
- [`skills/01-test-driven-additions.md`](skills/01-test-driven-additions.md) — 新模块的 TDD 流程
- [`skills/02-integration-via-existing-hooks.md`](skills/02-integration-via-existing-hooks.md) — 集成到已有架构的查找模式
- [`skills/03-push-after.md`](skills/03-push-after.md) — commit message + force-push 注意事项

## 例外

- **只读探索**（grep / read / 测试运行）不需要 pull-first，但有写操作时必须。
- **紧急 hotfix**（生产坏了）允许跳过测试驱动，但必须在 PR 描述里写明。
