"""`backend/app` 内部的依赖方向必须是单向的。

阶段 0 之前这里有三个循环依赖，两边都靠函数体内的延迟导入绕开——那种写法能跑，但它
把「谁依赖谁」从 import 区挪进了函数体，读代码的人看不出层次，静态分析工具也帮不上忙。
12 处延迟导入里只有 1 处（`index_versions.py` 的 TYPE_CHECKING）有正当理由。

这两条检查是那次清理的守卫：环一旦被重新引入，顶层导入会立刻抛 ImportError，而新增的
延迟导入会被 `test_no_delayed_local_imports` 抓住。没有它们，下一个「先加个函数内 import
让它跑起来」的改动不会被任何人发现。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

APP = Path("backend/app")

# 允许延迟导入的位置。放宽这份名单前先确认：真的存在环吗？还是提到顶层就能解决？
ALLOWED: frozenset[tuple[str, str]] = frozenset()


def _modules() -> list[Path]:
    return sorted(path for path in APP.glob("*.py") if path.name != "__init__.py")


def _delayed_local_imports(path: Path) -> list[tuple[str, int]]:
    """函数体内的相对导入。模块级 TYPE_CHECKING 块不在函数内，因此不会被算进来。"""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {
        id(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    found: set[tuple[str, int]] = set()
    # 自顶向下带着「当前是否在函数内」走，而不是对每个函数各 walk 一遍——后者会让
    # 嵌套函数里的同一行被外层函数重复计入。
    def visit(node: ast.AST, inside: bool) -> None:
        for child in ast.iter_child_nodes(node):
            within = inside or id(child) in functions
            if within and isinstance(child, ast.ImportFrom) and child.level > 0:
                found.add((child.module or "", child.lineno))
            visit(child, within)

    visit(tree, False)
    return sorted(found, key=lambda item: item[1])


@pytest.mark.parametrize("path", _modules(), ids=lambda path: path.name)
def test_no_delayed_local_imports(path: Path) -> None:
    offenders = [
        f"{path.name}:{lineno} 延迟导入 .{module}"
        for module, lineno in _delayed_local_imports(path)
        if (path.name, module) not in ALLOWED
    ]
    assert not offenders, (
        "函数体内的相对导入通常是在绕开循环依赖。先把依赖方向理顺，"
        "而不是把 import 藏进函数：\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("path", _modules(), ids=lambda path: path.name)
def test_module_imports_standalone(path: Path) -> None:
    """每个模块都能被单独导入。存在环时，先导入环上任一模块就会 ImportError。"""

    importlib.import_module(f"backend.app.{path.stem}")


def test_frontend_pipeline_stages_cover_what_the_backend_writes() -> None:
    """前端流水线格子必须覆盖后端真实写入的每一个 stage。

    匹配不上的 stage 不会显示成「未开始」——`PipelineStepper` 找不到对应格子时会退回
    按 `progressPercent` 猜位置（见该文件 currentIndex 的 inferredIndex 分支）。于是进度条
    看起来精确，实际是拿一个硬编码百分比在猜。这类不一致没有任何报错。

    V25 之前 sync_run 有五个格子（parse/chunk/enrich/validate/activate）后端从来不写，
    而后端写的 fetch_or_normalize / size_limit / retry_unavailable 前端一个都接不住。
    """

    import re

    stepper = Path("frontend/src/components/ui/PipelineStepper.tsx").read_text(encoding="utf-8")
    covered = set(re.findall(r'"([a-z_]+)"', stepper.split("const TERMINAL_SUCCESS")[0]))

    # 后端真实写入 operations.current_stage 与 sync_resource_runs.current_stage 的取值。
    # 新增一个阶段而不在前端加别名，这条会红。
    written = set()
    for path in Path("backend/app").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        written |= set(re.findall(r"(?<!failure_)current_stage\s*=\s*'([a-z_]+)'", text))
        # upsert_sync_resource(..., stage="...") 的关键字实参。前面的 (?<!failure_)
        # 排除 bad case 分类用的 failure_stage，那与流水线无关。
        written |= set(re.findall(r"(?<!failure_)(?<!_)stage\s*=\s*\"([a-z_]+)\"", text))
    # mark_stage 的六个阶段名以字典键的形式出现，单独列出。
    written |= {"parsing", "chunking", "vector", "keyword", "metadata", "validating"}
    # 这些是**状态**不是线性阶段，由 PipelineStepper 的 TERMINAL_FAILURE / RETRY /
    # CANCELLED 集合单独处理，不需要也不该有对应格子。
    written -= {"failed", "cancelled", "retry", "aborted", "queued"}

    missing = sorted(stage for stage in written if stage not in covered)
    assert not missing, (
        "后端会写入这些 stage，但前端没有任何格子的 key 或 aliases 接得住，"
        f"它们会退化成按进度百分比猜位置：{missing}"
    )
