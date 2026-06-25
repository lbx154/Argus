---
name: test-driven-additions
description: New module pattern — start with tests, write module, then write integration test that exercises end-to-end through existing infrastructure.
when_to_invoke: When adding a new Python module to argus_skill/ (not a fix to existing code).
---

# Test-Driven Additions

## 流程

1. **决定模块位置**：
   - 纯 validator / scanner / parser → `argus_skill/skills/<name>.py`
   - 与 daemon / supervisor 相关的状态 → `argus_skill/life/<name>.py`
   - CLI 工具 → `argus_skill/tools/<name>.py`

2. **先写实现 + 同时写单元测试**：
   - dataclass 优先于 dict（schema 显式）
   - 纯函数优先于 mutating method（可测试性）
   - 给每个 CLI 入口 `main(argv: list[str] | None = None) -> int`
   - 用 `argparse`，不要手 parse `sys.argv`

3. **先写单元测试**：
   - `tests/<area>/test_<name>.py`
   - 覆盖：happy path、每个 issue code、边界（空输入、缺字段）
   - 用 `tmp_path` fixture，不要 mock 文件系统

4. **写端到端集成测试**：
   - subprocess 调真 CLI，断言 exit code + stdout 关键字
   - 验证错误传播到调用方（reviewer、supervisor、daemon）
   - 用 monkeypatch / env vars 控制变量

5. **跑全套**：
   - `python -m pytest tests/<area>/test_<name>.py -q` 先确认新文件绿
   - `python -m pytest -q` 确认零 regression

## 模板：新 validator 模块

```python
"""<F-id> · 一句话说明.

详细说明 + 在工厂状态机里的位置。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Issue:
    code: str
    detail: str


@dataclass
class Report:
    ok: bool
    issues: list[Issue] = field(default_factory=list)
    def to_dict(self) -> dict: ...


def validate(project_root: Path) -> Report:
    """Pure function. No side effects. No LLM calls."""
    ...


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = validate(args.project_root)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_text(report)
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

## 反模式

- ❌ 加一个 module 不加测试
- ❌ 测试只 mock 不真跑（脆，且不反映集成）
- ❌ 把业务逻辑写在 `main()` 里（无法单独 import 测试）
- ❌ 用 `print()` 当错误处理 —— issue code + structured return 才是
