"""Issue #104 —— SSE 发布点识别从 grep 升级为 AST 枚举。

``frontend/scripts/check-sse-event-registry.mjs`` 用三条正则认后端发布点
（``self._publish("x", …)`` / ``….publish(task_id, "x", …)`` /
``sse_format({"type": "x", …})``）。现役调用点全被这三种形状覆盖，但正则的失效模式是
**静默**的：一种新写法（首实参含括号、跨行、带注释……）+ 同时漏写 §3.4 表 + 漏注册前端
= 四个集合齐缺该类型，四处断言全绿——注册表漂了不报错，只是前端静默收不到（#92 的马甲）。

本文件是第二条机械路径：解析 ``backend/`` 非测试码的**语法树**，把三种发布形状里的
字符串字面量类型实参各归各位（不受括号、空白、换行、注释影响），断言该集合
**等于** ``docs/architecture.md`` §3.4 表第一列。两条路径互为备份：.mjs 继续守前端 job
（无 pytest 依赖、跑得快）与前端两处注册表，pytest 这条守「发布点枚举」本身。

口径边界（与 .mjs 一致）：

* 权威源是 §3.4 表，本文不写计数；
* 只枚举字符串字面量实参——类型经变量名传入的调用仍看不见，那种事件必须同时补进
  §3.4 表（.mjs 文件头同一条告诫）；
* ``backend/services/trace.py`` 的 ``{"type": "trace_end"}`` 由 ``TraceRecorder.close()``
  直写 JSONL、不经 EventBus，属 trace 独有类型，**不进**发布点集合（第二测试钉住它）；
* ``backend/tests/`` 不参与统计：#56 退役的三个类型只在测试里被断言「不许出现」，
  只有非测试码真发的才算发布点。「不许有」由那些 ``isdisjoint`` 断言管，本文件只管
  「不能少」，互不替代。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
ARCH_DOC = REPO_ROOT / "docs" / "architecture.md"
TRACE_MODULE = BACKEND_DIR / "services" / "trace.py"

# 三种现役发布形状 → 类型实参在 AST 里的落点。
# `_publish` / `publish`：位置实参的下标（EventBus.publish 的首参是 task_id，
# 类型永远在第二位——锚在形参位置上，才不会把「第一个字符串」当成类型）。
_PUBLISH_ARG_POSITION = {"_publish": 0, "publish": 1}
# `sse_format`：首参是 dict 字面量，类型不在位置上、在 `"type"` 键上，走单独那条分支。
_DICT_TYPE_CALLEE = "sse_format"

_TABLE_ROW = re.compile(r"^\| `([a-z_]+)` \|", re.MULTILINE)


def _backend_source_files() -> list[Path]:
    """``backend/`` 的非测试 .py（跳过 ``tests/`` 与 ``__pycache__``，口径同 .mjs）。"""
    files: list[Path] = []
    for path in sorted(BACKEND_DIR.rglob("*.py")):
        parts = path.relative_to(BACKEND_DIR).parts
        if "tests" in parts or "__pycache__" in parts:
            continue
        files.append(path)
    return files


def _callee_name(call: ast.Call) -> str | None:
    """``self.tm.event_bus.publish(...)`` 认最后那截属性名，与 .mjs 的正则同判据。"""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _str_literal(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _type_from_dict(node: ast.Dict) -> str | None:
    """dict 字面量里 ``"type"`` 键的字符串字面量值（SSE 帧与 trace 记录共用这个形状）。"""
    for key, value in zip(node.keys, node.values):
        if _str_literal(key) == "type":
            return _str_literal(value)
    return None


def _event_type_of(call: ast.Call) -> str | None:
    """一个 publish 形状调用里的字符串字面量类型实参；取不到就返回 None（不计入）。"""
    name = _callee_name(call)
    if name is None:
        return None
    if name == _DICT_TYPE_CALLEE:  # sse_format({"type": "x", ...})
        for arg in call.args:
            if isinstance(arg, ast.Dict):
                return _type_from_dict(arg)
        return None
    position = _PUBLISH_ARG_POSITION.get(name)
    if position is None or len(call.args) <= position:
        return None
    return _str_literal(call.args[position])


def _publish_points() -> dict[str, list[str]]:
    """事件类型 → 发布点 ``file:line`` 列表（多发布点同类型是常态，取证时一眼可见）。"""
    points: dict[str, list[str]] = {}
    for path in _backend_source_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            event_type = _event_type_of(node)
            if event_type is None:
                continue
            points.setdefault(event_type, []).append(f"{rel}:{node.lineno}")
    return points


def _architecture_table_types() -> list[str]:
    """§3.4 事件表第一列（表区间：``### 3.4`` 到下一个 ``## 4.``，口径同 .mjs）。"""
    text = ARCH_DOC.read_text(encoding="utf-8")
    start = text.find("### 3.4")
    end = text.find("\n## 4.", start) if start >= 0 else -1
    if start < 0 or end < 0:
        raise AssertionError("docs/architecture.md 里找不到 §3.4 的事件表区间——表被改名或挪走时，解析器要跟着动")
    rows = _TABLE_ROW.findall(text[start:end])
    if not rows:
        raise AssertionError("docs/architecture.md §3.4 一行都没解析到——表格式变了同理要改解析器，不能让守卫空跑")
    return rows


def _trace_file_only_types() -> list[str]:
    """只写进 trace JSONL、从不进 EventBus 的类型（``trace.py`` 里的 dict 字面量）。"""
    tree = ast.parse(TRACE_MODULE.read_text(encoding="utf-8"), filename="backend/services/trace.py")
    types: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            literal = _type_from_dict(node)
            if literal is not None:
                types.append(literal)
    return types


def test_backend_publish_points_match_architecture_table() -> None:
    """AST 枚举的后端发布点集合 == §3.4 表第一列（一条都不许多、也不许少）。"""
    points = _publish_points()
    table = set(_architecture_table_types())

    unlisted = sorted(set(points) - table)  # 后端在发、表里没登记
    unfired = sorted(table - set(points))  # 表里列了、后端没有发布点

    assert not unlisted, (
        "后端有发布点但 §3.4 表没有这一行："
        + "；".join(f"{t}（{points[t][0]}）" for t in unlisted)
        + "——补表，并按 #92 同步前端两处注册表"
    )
    assert not unfired, (
        "§3.4 表列了但后端无发布点的类型：" + ", ".join(unfired) + "——删表行（表以真实发布点为准）"
    )


def test_trace_only_types_are_not_publish_points() -> None:
    """trace 独有类型（现役 ``trace_end``）不进发布点集合，口径同 .mjs 断言 3。

    两边都钉：``trace.py`` 里那条直写 JSONL 的 dict 字面量确实还在（哪天它改走
    EventBus，第二个断言就红，提醒补 §3.4 表与前端注册表）。
    """
    trace_only = set(_trace_file_only_types())
    assert trace_only == {"trace_end"}, f"trace.py 的直写类型面与本测试口径不符：{sorted(trace_only)}"
    assert not (trace_only & set(_publish_points())), (
        f"trace 独有类型出现在 EventBus 发布点里：{sorted(trace_only & set(_publish_points()))}"
        "——它已不再是只写 JSONL 的那条，须同步 §3.4 表"
    )
