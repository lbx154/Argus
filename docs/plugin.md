# Argus Plugin

## 一键安装

所有平台先安装 Node.js 22.12+。macOS/Linux 使用下面的 shell 命令，安装脚本会在
修改环境前验证 Node 和 Python。Windows 不创建虚拟环境，请在 PowerShell 下载并
运行 `install.ps1`：

```powershell
$Installer = Join-Path $env:TEMP "argus-plugin-install.ps1"
Invoke-WebRequest `
  https://raw.githubusercontent.com/lbx154/Argus/main/plugins/argus/install.ps1 `
  -OutFile $Installer
& $Installer all
Remove-Item $Installer
```

把 `all` 换成 `codex` 或 `claude` 可以只安装一个宿主。脚本使用 `py -m pip`
直接安装，不创建 Windows venv，也不会修改 PowerShell execution policy。

Codex：

```bash
curl -fsSL https://raw.githubusercontent.com/lbx154/Argus/main/plugins/argus/install.sh | sh -s -- codex
```

Claude Code：

```bash
curl -fsSL https://raw.githubusercontent.com/lbx154/Argus/main/plugins/argus/install.sh | sh -s -- claude
```

两个都安装：

```bash
curl -fsSL https://raw.githubusercontent.com/lbx154/Argus/main/plugins/argus/install.sh | sh -s -- all
```

## 使用

直接告诉 Codex 或 Claude Code：

- “用 Argus 执行这个项目。”
- “查看 Argus 项目状态。”
- “用 `target-disease-research` 研究 EGFR 与肺癌。”

医学能力是 `argus-verticals` 社区包提供的 `medical` vertical（需在 Argus 运行环境 `pip install argus-verticals`），不提供诊断或治疗建议。

编写 vertical 插件时，请在 `argus.verticals` entry-point 组注册；用重命名前的包名 `argus_skill` 拼写的旧组在一个发布周期内仍会被读取，但会打一条 warning。
