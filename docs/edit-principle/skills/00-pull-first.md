---
name: pull-first
description: Always pull origin/main before any write operation. Stash local work, pull fast-forward, re-apply or selectively restore.
when_to_invoke: Before editing any file in the repo for the first time in a session, or before any modification after >30 min idle.
---

# Pull-First

## 流程

```bash
# 1. 确认在 main 分支 + 看本地状态
git status --short
git branch -vv

# 2. 如果有本地未提交修改，先 stash（含 untracked）
git stash push -u -m "<short reason>"

# 3. Fetch + 看上游有多少新 commit
git fetch origin
git log --oneline HEAD..origin/main | head -20
git diff --stat HEAD..origin/main | tail -3

# 4. 如果上游有新 commit，先 fast-forward pull
git pull --ff-only origin main

# 5. 决定 stash 怎么处理：
#    (a) 全部 unstash 并解决冲突
#    (b) 只 checkout 部分新增文件（推荐用于 untracked）：
git ls-tree -r stash@{0}^3 --name-only | grep <pattern>
git checkout stash@{0}^3 -- <path>
#    (c) 直接 drop（如果上游已经做了相同的事）

# 6. drop stash 后验证 git status 干净（除了你主动留的新文件）
git stash drop stash@{0}
git status --short
```

## 容易踩的坑

- **`git stash` 不会 stash untracked 文件**，除非加 `-u`。用 `git stash push -u`。
- **`git stash show stash@{0}` 不显示 untracked 内容**。用 `git ls-tree -r stash@{0}^3 --name-only` 看 untracked 部分。
- **`git checkout stash@{0}^3 -- <path>`** 是 untracked 部分单独取出的标准做法。
- **如果上游已经做了同样的修复**（例如删了一个测试文件），别强行 unstash —— 看冲突信息后决定 drop。

## 决策树

```
本地 clean → 直接 pull
本地有改动 → stash → pull → 看上游做了什么
              ├─ 上游做了相同事 → drop stash
              ├─ 上游做了相关重构 → 评估冲突，可能选择性 checkout 部分文件
              └─ 上游和本地不冲突 → git stash pop
```

## 反模式

- ❌ `git pull` 不带 `--ff-only` —— 可能产生 merge commit 污染历史
- ❌ `git stash pop` 不看上游变化就直接复原 —— 大概率冲突
- ❌ 长时间不 pull 直接改 —— 改完一定冲突
