# Argus

[English](README.md) · **简体中文**

## 项目简介

Argus 是面向长周期任务的自主科研与工程运行时，由四个持续协作的 AI 角色组成：

- **Manager**：理解 operator 意图、选择工作流并控制阶段迁移。
- **Planner**：把目标拆解为可执行任务和证据要求。
- **Engineer**：实现代码、开展调研和实验并生成产物。
- **Reviewer**：独立检查正确性、证据、局限和完成状态。

项目状态、任务历史、检查点、Skill 与审查证据会跨 session 持久化。Argus 支持 GitHub Copilot CLI、OpenAI Codex CLI、Claude Code、OpenCode 与 Pi 后端。

[Technical Report PDF](technical_report/argus-technical-report.pdf)

## 安装方法

### 环境要求

- Python 3.11 或更高版本
- Node.js 22 或更高版本
- 至少安装并登录一个受支持的 agent CLI

### 源码安装

```bash
git clone https://github.com/lbx154/Argus.git
cd Argus

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

安装并登录一个后端。使用 GitHub Copilot：

```bash
npm install -g @github/copilot
copilot login
argus --setup --non-interactive \
  --backend copilot \
  --accept-house-rules
argus
```

使用 Pi：

```bash
npm install -g --ignore-scripts @earendil-works/pi-coding-agent
pi  # 执行 /login，完成鉴权后退出 Pi
argus --setup --non-interactive \
  --backend pi \
  --accept-house-rules
argus
```

其他受支持后端的安装命令：

```bash
npm install -g @openai/codex@latest
npm install -g @anthropic-ai/claude-code
curl -fsSL https://opencode.ai/install | bash
```

### npm beta 安装

```bash
npm install -g @github/copilot
copilot login
npm install -g @argusevolve/argus@beta
argus --setup --non-interactive \
  --backend copilot \
  --accept-house-rules
argus
```

### 更新源码安装

```bash
cd Argus
git pull --ff-only
. .venv/bin/activate
pip install -e .
argus
```
