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


def _guarded_calls(function: ast.FunctionDef, dotted_name: str) -> list[tuple[int, list[str]]]:
    """``function`` 里每个 ``dotted_name`` 调用点，连同它外层所有 ``if`` 判据。"""

    def dotted(node: ast.AST) -> str | None:
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
            return ".".join(reversed(parts))
        return None

    found: list[tuple[int, list[str]]] = []

    def visit(node: ast.AST, guards: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If):
                test = ast.unparse(child.test)
                for item in child.body:
                    visit(item, [*guards, test])
                for item in child.orelse:
                    visit(item, [*guards, f"not ({test})"])
                continue
            if isinstance(child, ast.Call) and dotted(child.func) == dotted_name:
                found.append((child.lineno, guards))
            visit(child, guards)

    visit(function, [])
    return sorted(found)


def _service_query() -> ast.FunctionDef:
    tree = ast.parse((APP / "service.py").read_text(encoding="utf-8"))
    service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RAGService"
    )
    return next(
        node
        for node in service.body
        if isinstance(node, ast.FunctionDef) and node.name == "query"
    )


def test_second_round_kb_supplement_stays_off_the_closed_loop() -> None:
    """闭环链只跑一轮 KB 检索，另三个 Profile 保留第二轮补检——一个 if，两条约束。

    这两条一起被破坏过：摘掉 fact_lookup 的补检调用点时整块删除，
    summarize / compare / procedure 的补检能力跟着消失，而设计稿第 51 行要求它们
    「继续路由到现有兼容行为」。全仓没有一条测试以 canary/full 跑过这三个 Profile，
    删掉不会有任何东西变红——所以这里只能从结构上守。
    """

    calls = _guarded_calls(_service_query(), "self.retrieve_candidates")
    guarded = [item for item in calls if any("not closed_loop" in g for g in item[1])]
    unguarded = [item for item in calls if item not in guarded]

    assert len(guarded) == 1, (
        "第二轮 KB 补检必须恰好有一个调用点，且被 `not closed_loop` 挡在闭环链之外"
        f"（当前带该判据的调用点在 {[line for line, _ in guarded]} 行）"
    )
    assert len(unguarded) == 2, (
        "闭环链的全局约束是单次请求最多一轮 KB 检索：原查询 + 扩展查询共两个调用点，"
        f"多出来的一个就是第二轮（当前无判据的调用点在 {[line for line, _ in unguarded]} 行）"
    )


def test_zero_candidate_rejection_waits_for_web_on_the_compatible_profiles() -> None:
    """判空位置两条链不同，两个调用点都必须显式说明自己属于哪一条。

    改造前 Web 结果先 extend 进 candidates 再判空，所以「KB 零候选 + Web 有结果」
    仍然作答。闭环链把判空提到了 Web 之前（Web 补充不了一条 KB 证据都没有的问题），
    那是闭环链**独有**的收敛；把它套到另三个 Profile 上会悄悄删掉一条可用路径。
    """

    calls = _guarded_calls(_service_query(), "self._raise_no_candidates")
    assert len(calls) == 2, f"判空只应有两个调用点，当前 {[line for line, _ in calls]}"
    closed_loop_first, compatible_after_web = calls
    assert any(
        "closed_loop and" in guard for guard in closed_loop_first[1]
    ), f"靠前的判空属于闭环链，判据里必须点名 closed_loop：{closed_loop_first}"
    assert any(
        "not closed_loop" in guard and "web_candidates" in guard
        for guard in compatible_after_web[1]
    ), (
        "靠后的判空属于未改造的三个 Profile，必须同时看 KB 与 Web 候选，"
        f"否则 Web 救回来的那条路径又没了：{compatible_after_web}"
    )


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
