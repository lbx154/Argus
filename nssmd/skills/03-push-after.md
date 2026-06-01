---
name: push-after
description: Commit with nssmd signature, force-push only with explicit warning, never to other branches.
when_to_invoke: After a logical chunk of work is done and tests are green.
---

# Push-After

## 流程

```bash
# 1. 跑全套测试，必须零 fail
python -m pytest --tb=no 2>&1 | tail -3

# 2. 看 git status，把无关的"测试副作用文件"撤回
git status --short
# 例如 paper/artifacts/*.json 的 repo_root 被测试改了 → git checkout 撤
git checkout -- <noise file>

# 3. 暂存所有想 commit 的
git add -A
git status --short  # 再次确认

# 4. commit，HEREDOC 风格，最后一行是 nssmd Co-Authored-By
git commit -m "$(cat <<'EOF'
<subject line, < 80 chars, present tense, 不带 emoji>

<body, 每段 < 5 行, 解释 WHY 而不是 WHAT>

Tests: +N (X unit + Y integration). Full suite N -> M passed, 0 failed.

Co-Authored-By: nssmd <nssmd@noreply.local>
EOF
)"

# 5. push
git push origin main

# 6. 输出 commit URL 给用户
git log -1 --format="%H" | head -c 7
# https://github.com/lbx154/argus-skill/commit/<sha>
```

## Force-push 规则

**默认禁止 force-push 到 main。** 唯一例外：用户明确要求改已 push 的 commit（例如改署名）。

```bash
# 改 commit message：
git commit --amend -m "$(cat <<'EOF'
<new message>
Co-Authored-By: nssmd <nssmd@noreply.local>
EOF
)"

# 用 --force-with-lease（不是 --force）：
git push --force-with-lease origin main

# 必须在 push 前给用户 warning：
# "force-push 会改写 origin/main 的历史，下游 clone 这个分支的人会看到 commit hash 变了"
```

## Commit message 格式

**禁用**：
- ❌ `Co-Authored-By: HAPI ...`
- ❌ `Co-Authored-By: Claude ...`
- ❌ `via [HAPI](https://hapi.run)`
- ❌ `🤖 Generated with [Claude Code]`

**只用**：
- ✅ `Co-Authored-By: nssmd <nssmd@noreply.local>`

## Subject line 模板

| 类型 | 模板 |
|---|---|
| 新增模块 | `Add <thing>: <one-liner>` |
| 修 bug | `Fix <symptom>: <root cause>` |
| 重构 | `Refactor <area>: <reason>` |
| 文档 | `Docs: <subject>` |
| 测试 | `Tests: <area> — <coverage>` |
| 集成 | `Integrate <A> into <B>: <effect>` |

## 反模式

- ❌ 一个 commit 同时改 5 个独立 feature
- ❌ commit message 只写 "update" / "fix"
- ❌ push 之前不跑测试
- ❌ 看到 `git status` 有意外文件直接 `git add -A` 不看
- ❌ 用 `git commit -m "single line"`，不带 body 解释 why
- ❌ Force-push 不 warning 用户
