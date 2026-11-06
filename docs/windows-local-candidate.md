# 本地 Windows 兼容候选 0.1.9

这是当前 `feat/ts-core` 的同一 TypeScript core 本地兼容构建，不是公共 1.0.0 的回移、正式签名 release 或跨代数据迁移。未改调度架构；Windows 原生边界修复在 Windows 分支执行，store 保持原有语义。公开更新 endpoint 在 2026-11-06 返回 1.0.0，迁移与 Node 能力不同，不可据此宣称此分支内置 Node。

## 构建

安装 Node≥22.12、Rust/MSVC、Windows SDK，使用干净源码快照和独有 Cargo 输出目录：

```powershell
npm ci
npm --prefix frontend/web ci
npm --prefix desktop-tauri ci
npm run check
npm --prefix frontend/web run typecheck
npm --prefix frontend/web test
npm --prefix desktop-tauri run build:local-windows
```

根 workspace 各包、frontend、desktop/Cargo/Tauri 版本一致为 0.1.9；锁文件仅同步元数据，不升级依赖。
`build:local-windows` 使用已有 unsigned 构建路线，加编译 feature `local-windows`：禁用公共更新检查和安装，包括已有缓存候选，避免跨入未经验证的 1.x 布局。普通 release 和 POSIX 构建默认行为不变。此本地候选后续只能由负责人核对源码/迁移要求后手动整包升级，不能使用公共自动更新。

NSIS 在独有 `CARGO_TARGET_DIR/release/bundle/nsis/`；完整便携载荷为同次构建的 `Argus.exe`、`WebView2Loader.dll`、`argus-core/` 及许可证。未提供发布私钥或 Authenticode 证书，不能宣称已签名或无 SmartScreen 提示，不生成替代信任密钥、不上传 release。

## 安装与接续边界

团队只在隔离 scratch 验证，不覆盖/重启生产 D:/argus，不推 GitHub或切共享分支。安装器仍写 HKCU，不能仅改 APPDATA 就在团队账户运行生产标识 NSIS；须独立用户/VM，或使用独立标识的测试安装包并核对完整载荷。

生产后续由独立执行器在全部任务静止后操作：先停止本地后端并退出，受控备份完整安装树、桌面设置与 ARGUS_HOME（含凭据，仅本机保管），再整包安装/升级，保留项目/认证/用户技能。禁止只换 ESM 或仅复制 EXE；同一真实状态根只能一个 owner。NSIS 拒绝降级，无事务回滚；回退需一致的安装树、登记及适用数据备份，不能覆盖升级后新数据。详见 [安装保留测试](windows-install-preservation.md)。

实际 SHA、载荷/安装器散列、测试 fail/skip/未跑、生产是否激活由交付根的 README/PDF 和 evidence/integration 收据列明；此源码说明不是测试通过声明。
