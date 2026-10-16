"""S2 #136 —— 危险动作拦截：设置里的规则表 + 一个钩子，命中即拒。

ADR 0008 第 6 条：危险动作在执行前被**设置里的规则表**当场拒掉，命中即不执行，
把 ``{block, reason}`` 当这次调用的结果回给模型（模型自己换路）。**不弹框、不进
图重入、不改参数、不 terminate**——这四条是与 pi 的 ``tool_call`` 钩子的有意分歧
（pi 可原地改参数且改完不复核、且 ``block`` 当场产出错误结果；见
``docs/research-pi-agent-construction.md`` §5）。

本片是**纯增量**：这里只钉住新钩子的三条判据——命中即拒 / 不命中照旧 / 规则表为空
零回归。判定单位是**一次调用**（工具名 + 入参）。人工确认闸门与规划期风险扫描已随
S4（#138）整链删除，危险动作的防护现在就只剩这一条钩子。

零回归口径：记录体、事件载荷、对话面消息**都不多一个键**——被拒的调用就落在「没执行」
那两格里（``status:"skipped"`` + 理由在 ``error``），没有新字段。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

import pytest

from backend.api.schemas import StepRecord, ToolCallRecord
from backend.config import Settings
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.policy import DangerPolicy
from backend.core.agent.state import AgentState
from backend.core.llm.client import MockLLMClient
from backend.core.tools.base import BaseTool, ToolResult
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_settings

TASK_ID = "t_policy_block"

#: 一次调用在 ``executor`` 出口的键集合 —— 空表时一个键都不许增减（#136 AC1）。
#: （``circuit_open`` / ``retries`` 是 tool_node 真派发之后才写上的 P0 标记，不在这一层。
#: 闸门自己的 ``need_confirm`` / ``confirmed`` 两列已随 #138 拆除，故不在集合里。）
PRE_S2_RECORD_KEYS = frozenset(
    {"id", "tool_name", "input", "output", "status", "error"}
)


def _settings(tmp_path, **rules_kw: Any) -> Settings:
    """一份隔离的 Settings；规则表按 JSON 字符串写（生产路径就是这条）。"""
    return make_settings(tmp_path, **rules_kw)


def _rules(*entries: Dict[str, Any]) -> str:
    return json.dumps(list(entries), ensure_ascii=False)


class _Probe(BaseTool):
    """一个会记账的探针：``calls`` 非空就等于「这一次真的执行了」。"""

    name = "probe"
    description = "counting probe (test only)"
    args_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {"cmd": {"type": "string"}},
    }

    def __init__(self, settings: Settings | None = None):
        super().__init__(settings)
        self.calls: list[Dict[str, Any]] = []

    def run(self, **kwargs: Any) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(success=True, data={"ran": True})


class _StubTM:
    """TaskManager 的最小替身：只需要事件总线与停机探针。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.event_bus = EventBus()

    def is_stop_flagged(self, task_id: str) -> bool:
        return False

    def add_artifact(self, *args: Any, **kwargs: Any) -> None:
        return None


def _state() -> AgentState:
    return {
        "task_id": TASK_ID,
        "step_index": 1,
        "steps": [{"index": 1, "thought": "", "tool_calls": [], "status": "running"}],
        "messages": [{"role": "user", "content": "hi"}],
        "plan": [], "artifacts": [], "status": "RUNNING", "stop_requested": False,
        "final_answer": "", "error": "",
        "_last_action": "", "_current_tool_calls": [],
        "subtasks": [], "_is_subtask": False,
    }


def _executor(
    settings: Settings,
    *,
    args: Dict[str, Any] | None = None,
) -> tuple[AgentRuntime, AgentState, _Probe, _StubTM]:
    """Just the executor pass: one scripted probe call, decisions made, nothing dispatched."""
    tool = _Probe(settings)
    tm = _StubTM(settings)
    rt = AgentRuntime(
        TASK_ID,
        tm,
        llm=MockLLMClient(tool_calls=[{"id": "c1", "name": tool.name, "arguments": args or {}}]),
        tools=[tool],
        tool_schemas=[tool.to_openai_schema()],
    )
    state = _state()
    rt.executor(state)
    return rt, state, tool, tm


def _run(
    settings: Settings,
    **kwargs: Any,
) -> tuple[AgentRuntime, AgentState, _Probe, _StubTM, EventBus]:
    """One full executor -> tool pass with a scripted single probe call."""
    rt, state, tool, tm = _executor(settings, **kwargs)
    rt.tool_node(state)
    return rt, state, tool, tm, tm.event_bus


def _rec(state: AgentState) -> Dict[str, Any]:
    return state["_current_tool_calls"][0]


def _events(tm: _StubTM) -> list[Dict[str, Any]]:
    return tm.event_bus.replay(TASK_ID)


def _last_tool_message(state: AgentState, call_id: str = "c1") -> Dict[str, Any]:
    for msg in reversed(state.get("messages") or []):
        if msg.get("role") == "tool" and msg.get("tool_call_id") == call_id:
            return msg
    raise AssertionError("no tool message was fed back to the model")


# ── 命中即拒 ─────────────────────────────────────────────────────────────────
def test_hit_denies_the_call_without_executing_it(tmp_path):
    """命中 → 不执行 + ``{block, reason}`` 回模型。"""
    reason = "probe is not allowed here"
    settings = _settings(tmp_path, policy_rules=_rules({"tool": "probe", "reason": reason}))
    _rt, state, tool, tm, _bus = _run(settings)

    assert tool.calls == [], "被规则拦下的调用还是执行了"
    rec = _rec(state)
    assert rec["status"] == "skipped"
    assert rec["error"] == reason
    assert set(rec) == PRE_S2_RECORD_KEYS, "记录体多了一个改造前没有的键"

    content = json.loads(_last_tool_message(state)["content"])
    assert content["block"] is True
    assert content["reason"] == reason

    # 事件面：这次调用照样在时间线上，但**没有任何**人工确认事件；
    # 结果体就是这条记录（状态 / 理由与状态面同一份）。
    types = [e["type"] for e in _events(tm)]
    assert types == ["tool_call", "tool_result"]
    result = [e["data"] for e in _events(tm) if e["type"] == "tool_result"][0]
    assert result["status"] == "skipped"
    assert result["error"] == reason


def test_a_command_pattern_is_matched_against_the_arguments(tmp_path):
    """「命令模式」= 对入参里的字符串值做正则；同一个工具的另一次调用不受累。"""
    settings = _settings(
        tmp_path,
        policy_rules=_rules(
            {"tool": "probe", "pattern": r"rm\s+-rf", "reason": "destructive shell"}
        ),
    )
    _hit_rt, hit_state, hit_tool, _tm, _bus = _run(settings, args={"cmd": "sudo rm -rf /"})
    _miss_rt, miss_state, miss_tool, _tm2, _bus2 = _run(settings, args={"cmd": "ls -la"})

    assert hit_tool.calls == []
    assert _rec(hit_state)["status"] == "skipped"
    assert miss_tool.calls == [{"cmd": "ls -la"}], "不命中的调用被连坐拦了"
    assert _rec(miss_state)["status"] == "success"


def test_block_does_not_mutate_the_arguments(tmp_path):
    """钩子不改参数：记录体里的入参与模型给的一模一样。"""
    args = {"cmd": "rm -rf /"}
    settings = _settings(tmp_path, policy_rules=_rules({"tool": "probe", "reason": "no"}))
    _rt, state, _tool, _tm, _bus = _run(settings, args=args)

    assert _rec(state)["input"] == {"cmd": "rm -rf /"}
    assert args == {"cmd": "rm -rf /"}


def test_block_does_not_terminate_the_run(tmp_path):
    """不 terminate：这一轮照常收口，球回给 planner（终止由人按停）。"""
    settings = _settings(tmp_path, policy_rules=_rules({"tool": "probe", "reason": "no"}))
    _rt, state, _tool, _tm, _bus = _run(settings)

    assert state["_last_action"] == "tool_done"
    assert state.get("stop_requested") is not True
    assert state["error"] == ""


# ── 不命中照旧 / 空表零回归 ──────────────────────────────────────────────────
def test_no_rules_is_byte_identical_to_before(tmp_path):
    """规则表为空 → 与改造前逐字节一致：executor 出口的记录体连键集合都不变。"""
    settings = _settings(tmp_path)
    _rt, state, tool, tm = _executor(settings, args={"cmd": "ls"})

    # executor 出口：与改造前逐字节同形（还没派发，所以 status 仍是 pending）。
    rec = _rec(state)
    assert set(rec) == PRE_S2_RECORD_KEYS, "未配置时记录体的键集合变了"
    assert rec["status"] == "pending"
    assert rec["error"] == ""

    _rt.tool_node(state)
    assert tool.calls == [{"cmd": "ls"}]
    assert _rec(state)["status"] == "success"
    # 一次真派发之后仍只有这两个额外的 P0 标记（tool_node 写的）。
    assert set(_rec(state)) == PRE_S2_RECORD_KEYS | {"circuit_open", "retries"}
    assert [e["type"] for e in _events(tm)] == ["tool_call", "tool_result"]
    assert json.loads(_last_tool_message(state)["content"]) == {"ran": True}


def test_a_rule_for_another_tool_does_not_match(tmp_path):
    """规则表非空但这条不匹配 → 照旧执行（判定按调用算，不是一票否决）。"""
    settings = _settings(tmp_path, policy_rules=_rules({"tool": "other_tool", "reason": "no"}))
    _rt, state, tool, _tm, _bus = _run(settings, args={"cmd": "ls"})

    assert tool.calls == [{"cmd": "ls"}]
    assert _rec(state)["status"] == "success"


# ── 规则表本身：解析与匹配 ──────────────────────────────────────────────────
class _LogCollector(logging.Handler):
    """``get_logger()`` 把 propagate 关掉了，pytest 的 caplog 收不到这些记录，
    因此直接把 handler 挂在目标 logger 上（同 ``test_checkpointer._LogCollector``）。"""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self, min_level: int = logging.WARNING) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno >= min_level]


@pytest.fixture
def policy_logs():
    """收集 ``agent.policy`` 的日志（模块名 agent.policy → logger ``agent.agent.policy``）。"""
    logger = logging.getLogger("agent.agent.policy")
    collector = _LogCollector()
    logger.addHandler(collector)
    try:
        yield collector
    finally:
        logger.removeHandler(collector)


def _policy(*rules: Dict[str, Any]) -> DangerPolicy:
    return DangerPolicy.from_settings(Settings(policy_rules=json.dumps(list(rules))))


def test_rows_without_a_tool_are_not_rules():
    assert _policy().rules == ()
    assert _policy({"reason": "no tool"}).rules == ()
    assert _policy({"tool": "  "}).rules == ()


def test_a_rule_without_a_reason_still_says_something():
    reason = _policy({"tool": "probe"}).check("probe", {})

    assert reason, "命中却没有理由可回给模型"
    assert "probe" in reason


def test_the_default_reason_names_the_actual_tool_not_the_glob():
    """R4：规则写的是通配 ``mcp__*__write``，回给模型的理由得是**真实工具名**。

    规则里的通配名对模型没有可操作性——它刷新一条一模一样的调用时，需要知道自己被哪个
    工具拒了（照纯读就好：拒绝理由是人读的话，不该拿规则文本喂模型）。
    """
    reason = _policy({"tool": "mcp__*__write"}).check("mcp__fs__write", {})

    assert "mcp__fs__write" in reason
    assert "mcp__*__write" not in reason
    # 规则自己写了 reason 就照写，不被覆盖。
    assert _policy({"tool": "mcp__*__write", "reason": "no writes"}).check("mcp__fs__write", {}) == "no writes"


def test_exact_name_and_glob_and_pattern():
    policy = _policy(
        {"tool": "probe", "reason": "exact"},
        {"tool": "mcp__*__write", "reason": "mcp writer"},
        {"tool": "code_exec", "pattern": "DROP TABLE", "reason": "ddl"},
    )

    assert policy.check("probe", {}) == "exact"
    assert policy.check("mcp__fs__write", {}) == "mcp writer"
    assert policy.check("mcp__fs__read", {}) is None
    # 命令模式不区分大小写
    assert policy.check("code_exec", {"cmd": "drop table users"}) == "ddl"
    assert policy.check("code_exec", {"cmd": "select 1"}) is None
    assert policy.check("untouched", {"cmd": "drop table users"}) is None


def test_a_broken_pattern_is_dropped_not_crashed():
    """正则写错不该把运行炸掉；该条规则作废。"""
    policy = _policy({"tool": "probe", "pattern": "(", "reason": "no"})

    assert policy.rules == ()
    assert policy.check("probe", {}) is None


def test_an_over_large_repetition_is_dropped_not_crashed():
    """R3：``a{99999999999}`` 抛的是 ``OverflowError``，不是 ``re.error``。

    独立评审实测：只捕获 ``re.error`` 时这一条会穿过去把运行炸掉——而「配置写坏不炸运行」
    是这张表自己的承诺。
    """
    policy = _policy({"tool": "probe", "pattern": "a{99999999999}", "reason": "no"})

    assert policy.rules == ()
    assert policy.check("probe", {"cmd": "aaaa"}) is None


def test_a_pattern_is_matched_against_the_real_argument_text():
    """R2：判据打在入参的**文本叶子**上，不打在 JSON 文本上。

    这里每一条都要求**旧实现会漏**（复核要求：用例必须真能区分，不能拿一个在两边都命中的
    模式来冒充）：

    * ``rm\\t-rf /``（真 tab）——模式 ``rm\\s+-rf``：JSON 文本里是 ``rm\\\\t-rf``，``\\s`` 不匹配 ``\\``；
    * ``rm -rf "/"``——模式**带引号** ``rm -rf "/"``：JSON 文本里引号被转义成 ``\\"``，照真实命令写的模式搜不到；
    * ``C:\\tmp\\x``——JSON 文本里反斜杠翻倍；
    * 嵌套 list 里的 ``rm -rf /``——JSON 文本里前面有 ``"``，``^`` 锚点落空。

    **限制（同族，不隐藏）**：模式是对**每个叶子各自** search 的，所以 argv 被拆成
    ``["rm", "-rf", "/"]`` 时 ``rm\\s+-rf`` 命不中（旧实现搜整段文本时也一样命不中，不是回归）。
    """
    shell = _policy({"tool": "probe", "pattern": r"rm\s+-rf", "reason": "destructive"})
    assert shell.check("probe", {"cmd": "rm\t-rf /"}) == "destructive"  # 真 tab

    quoted = _policy({"tool": "probe", "pattern": 'rm -rf "/"', "reason": "destructive"})
    assert quoted.check("probe", {"cmd": 'rm -rf "/"'}) == "destructive"  # 引号

    winpath = _policy({"tool": "write", "pattern": r"C:\\tmp", "reason": "outside sandbox"})
    assert winpath.check("write", {"path": r"C:\tmp\x"}) == "outside sandbox"

    # 嵌套结构（list / dict）里的字符串也一样看得到。
    nested = _policy({"tool": "probe", "pattern": "^rm -rf", "reason": "destructive"})
    assert nested.check("probe", {"argv": ["sh", "-c", "rm -rf /"]}) == "destructive"

    # argv 被拆开时命不中：限制就写在这里，别让人以为它兜得住。
    assert nested.check("probe", {"argv": ["rm", "-rf", "/"]}) is None


def test_numbers_and_bools_still_have_a_text_form_to_match():
    """数字 / 布尔仍进匹配面（``str()``）：不做「只看字符串」的静默收窄。

    数字：旧实现搜 JSON 文本，``22`` 同样命中 ``{"port": 22}``，所以这里钉的是「没收窄」。
    布尔：新实现的文本形式是 ``str(True) == "True"``；旧实现搜的是 JSON 的 ``true``，
    ``^True$`` 在旧实现上本来就不成立，所以布尔这一条只说明新实现的文本形式，不是「没收窄」的证据。
    """
    policy = _policy({"tool": "probe", "pattern": "22", "reason": "no"})

    assert policy.check("probe", {"port": 22}) == "no"
    assert policy.check("probe", {"port": 2200}) == "no"  # 子串照样命中，规则自己写准
    assert policy.check("probe", {"port": "22"}) == "no"
    # None 没有文本形态，不参与。
    assert policy.check("probe", {"note": None}) is None
    assert _policy({"tool": "probe", "pattern": "^True$", "reason": "no"}).check(
        "probe", {"flag": True}
    ) == "no"


def test_a_pattern_never_matches_a_key_name():
    """R2 的顺带收益：键名不再参与匹配（以前搜整段 JSON 文本时，「key 里带 path」会误命中）。"""
    policy = _policy({"tool": "probe", "pattern": "cmd", "reason": "no"})

    assert policy.check("probe", {"cmd": "ls"}) is None
    assert policy.check("probe", {"argv": "cmd"}) == "no"


def test_the_first_matching_rule_wins():
    policy = _policy({"tool": "probe", "reason": "first"}, {"tool": "probe", "reason": "second"})

    assert policy.check("probe", {}) == "first"


def test_check_never_mutates_the_arguments():
    args = {"cmd": "rm -rf /"}
    _policy({"tool": "probe", "pattern": "rm", "reason": "no"}).check("probe", args)

    assert args == {"cmd": "rm -rf /"}


def test_a_non_object_entry_is_loud(policy_logs):
    """R1：非对象条目以前在配置层被静默滤掉——写坏的表因此无声无息。

    处置权收在 ``policy.py``（``config.policy_rules_list`` 只解析、不筛），所以这里断言的
    是「条目被丢掉」与「出声」同时成立，而不是只丢不响。
    """
    policy = _policy(1, {"tool": "probe", "reason": "no"})

    assert [r.tool for r in policy.rules] == ["probe"], "好条目被坏条目带走了"
    assert any("not an object" in m for m in policy_logs.messages())


def test_a_whole_table_written_wrong_is_loud(policy_logs):
    """整表写坏 → **出声**（#136 §4.1 定的软档），不是静默裸奔。

    参照物里够证据的三家（dsh 加载即抛 / pi 无 UI 即 block / codex 解析失败即报错）都是
    fail-closed；本仓选了软档：warning + 照跑（整表作废 = 不拦，行为不变，但看得见）。
    """
    for raw in ("nope", '{"tool": "probe"}', '["probe"]', '[{"reason": "no tool"}]'):
        before = len(policy_logs.messages())
        assert DangerPolicy.from_settings(Settings(policy_rules=raw)).rules == ()
        assert len(policy_logs.messages()) > before, f"{raw!r} 整表失效却没有一声"


def test_an_explicitly_empty_table_stays_silent(policy_logs):
    """`""` / `[]` / `null` 是「我就是要空表」，不该被当成写坏（否则每台机器都刷 warning）。"""
    for raw in ("", "[]", "[ ]", "null"):
        DangerPolicy.from_settings(Settings(policy_rules=raw))
    assert policy_logs.messages() == []


def test_settings_expose_the_rule_table_as_a_list(tmp_path):
    """配置层只解析：JSON 字符串 -> 数组；坏 JSON / 非数组 -> 空表（不炸）。

    条目校验归 `policy.py`（见 `test_a_non_object_entry_is_loud`）——这一层筛掉，正是
    R1 那个「写坏无声」的来源。
    """
    s = _settings(tmp_path, policy_rules=_rules({"tool": "probe", "reason": "no"}))
    assert s.policy_rules_list == [{"tool": "probe", "reason": "no"}]
    for raw in ("", "nope", '{"tool": "probe"}', "null"):
        assert Settings(policy_rules=raw).policy_rules_list == [], f"{raw!r} 不该产出规则"
    # 非 dict 的条目原样透出（由消费者出声处置）
    assert Settings(policy_rules='[1, {"tool": "a"}]').policy_rules_list == [1, {"tool": "a"}]


# ── 持久化面 ────────────────────────────────────────────────────────────────
def test_a_blocked_call_survives_persistence(tmp_path):
    """记录体能回看：状态与理由都在。"""
    reason = "no"
    settings = _settings(tmp_path, policy_rules=_rules({"tool": "probe", "reason": reason}))
    _rt, state, _tool, _tm, _bus = _run(settings)

    record = StepRecord(**state["steps"][0]).tool_calls[0]
    assert record.status == "skipped"
    assert record.error == reason
    # 没人批过它，也没人拒过它：这不是一次闸门判决。
    assert ToolCallRecord(id="x", tool_name="y").status == "pending"
