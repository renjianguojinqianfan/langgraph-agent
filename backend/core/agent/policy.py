"""Danger-action policy: 设置里的规则表 + 一个钩子（S2 #136）。

[ADR 0008](../../../docs/adr/0008-single-loop-plan-build-modes.md) 第 6 条把「危险动作」
收成两件事：一张**设置里的规则表**（``Settings.policy_rules``），和一个接在
``AgentRuntime.executor`` 里、决定「这次调用执行不执行」的钩子。命中即**拒**：
不执行，把 ``{block, reason}`` 当这次调用的结果回给模型，模型自己换路。

与 pi 的 ``tool_call`` 钩子的三条有意分歧（实证见
``docs/research-pi-agent-construction.md`` §5）：

* **不改参数**——pi 的钩子能原地改 ``event.input`` 且**改完不复核**；「校验过的参数被
  钩子改掉又不复核」是后门，本仓库只回 ``{block, reason}``；
* **不 terminate**——终止由人按停，不由策略层杀；
* **不弹框、不进图重入**——拒绝是一次**工具结果**，不是一次人工确认（闸门那套是旧形态，
  由 S4 拆除）。

判定单位是**一次调用**（工具名 + 入参），不是计划里的一句话：规划期那句「这一步看起来
危险」是 :mod:`backend.core.agent.risk` 的活，两者不同源、也不互相替代。
``pattern`` 打在**入参里的字符串值**上（不是 JSON 文本，见 :func:`_string_leaves`）。

坏配置一律 warning + 忽略，绝不因为规则表写错把运行炸掉（与 ``risk_danger_keywords``
同口径）；整表都编不出规则时另发一条「什么都没拦住」——**软档**：出声但照跑
（裁定见 ``docs/research-danger-policy-across-agents.md`` §4.1）。**空表 = 不拦**，
与改造前逐字节一致。
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional, Sequence, Tuple

from ...config import Settings
from ...utils.logging import get_logger

logger = get_logger("agent.policy")


def _string_leaves(value: Any) -> Iterator[str]:
    """入参里的所有**字符串值**（dict 只走值、list/tuple 递归；键名不算）。

    判据是「命令模式打在真实参数文本上」。以前搜 ``json.dumps(args)`` 的整段文本，JSON
    转义会把真实字符变形——真 tab 变成 ``\\t``、引号变成 ``\\"``、Windows 路径变成双反斜杠
    ——照着真实命令写的模式反而漏拦（#136 独立评审 R2 实测：``rm\\t-rf /`` 与
    ``rm -rf "/"`` 都不命中）。顺带把「键名也被匹配」这一类假阳性一并去掉。
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_leaves(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _string_leaves(item)


@dataclass(frozen=True)
class BlockRule:
    """一条「命中即拒」的规则。

    ``tool`` 是工具名，支持 ``fnmatch`` 通配（如 ``mcp__*__write``）；
    ``pattern`` 可选，是对**入参里每个字符串值**的不区分大小写正则搜索——即「命令模式」，
    省略则**该工具的任何一次调用**都拦。``reason`` 为空时由 :meth:`DangerPolicy.check`
    用**实际工具名**补一句（规则里的通配名对模型没有意义）。
    """

    tool: str
    pattern: Optional["re.Pattern[str]"]
    reason: Optional[str]

    def matches(self, tool_name: str, args: Mapping[str, Any]) -> bool:
        if not fnmatch.fnmatchcase(tool_name, self.tool):
            return False
        if self.pattern is None:
            return True
        return any(self.pattern.search(leaf) for leaf in _string_leaves(args))


def _compile(rule: Any) -> Optional[BlockRule]:
    """一条规则 -> :class:`BlockRule`；写坏的那条 warning 后作废。"""
    if not isinstance(rule, dict):
        # 独立评审 R1：非对象条目以前在配置层就被静默滤掉，写坏的表因此无声无息。
        logger.warning("policy_rules entry is not an object; ignored: %r", rule)
        return None
    tool = str(rule.get("tool") or "").strip()
    if not tool:
        logger.warning("policy_rules entry without a 'tool'; ignored: %r", rule)
        return None
    pattern: Optional[re.Pattern[str]] = None
    raw_pattern = rule.get("pattern")
    if raw_pattern:
        try:
            pattern = re.compile(str(raw_pattern), re.IGNORECASE)
        except (re.error, OverflowError):
            # OverflowError：``a{99999999999}`` 这类重复次数过大（独立评审 R3 实测）。
            # 两样都按「这条规则写坏」处理，绝不让配置把运行炸掉。
            logger.warning("policy_rules pattern is not a valid regex; rule ignored: %r", rule)
            return None
    return BlockRule(tool=tool, pattern=pattern, reason=str(rule.get("reason") or "").strip() or None)


def _table_is_explicitly_empty(raw: str) -> bool:
    """``""`` / ``[]`` / ``null``（含空白）—— 运维就是没写表，不算写坏。

    整表写坏与「我就是要空表」在**结果**上一样（都是不拦），所以只有读一眼原文才分得开：
    解析不了、或解析出来不是数组，都按写坏论（下面那条 warning 就是给这一格用的）——
    参照物里够证据的三家（dsh / pi / codex）在这一格全是 fail-closed，本仓选的是软档：
    **出声但照跑**，见 ``docs/research-danger-policy-across-agents.md`` §4.1。
    """
    text = (raw or "").strip()
    if not text:
        return True
    try:
        return json.loads(text) in (None, [])
    except Exception:
        return False


class DangerPolicy:
    """编译好的规则表；对每一次调用判定一次。"""

    def __init__(self, rules: Sequence[BlockRule] = ()) -> None:
        self.rules: Tuple[BlockRule, ...] = tuple(rules)

    @classmethod
    def from_settings(cls, settings: Settings) -> "DangerPolicy":
        """Build from ``Settings.policy_rules_list`` (the parsed rule table)."""
        rules = [
            r for r in (_compile(item) for item in settings.policy_rules_list) if r is not None
        ]
        if not rules and not _table_is_explicitly_empty(settings.policy_rules):
            # #136 §4.1：整表写坏 = 一条规则都没编出来，而运维多半以为拦着。
            # ``_parse_json_list`` 对坏 JSON 静默回退空表（MCP 两处同口径，本片不动它），
            # 所以这里补一句「什么都没拦住」，别让坏表静悄悄变成裸奔。
            logger.warning(
                "policy_rules is set but yielded no rule (not a usable JSON array?); "
                "NOTHING is blocked: %r",
                settings.policy_rules,
            )
        return cls(rules)

    def check(self, tool_name: str, args: Mapping[str, Any]) -> Optional[str]:
        """Return the reason to block this call on, or ``None`` to let it through.

        只读判定：不碰入参、不留状态、不弹框（pi 的钩子能原地改参数，这里不能）。
        规则按表序判定，**第一条命中的说了算**。规则没写 reason 时用**实际工具名**兜一句
        ——「``mcp__*__write`` is blocked by policy」对模型没有可操作性（独立评审 R4）。
        """
        for rule in self.rules:
            if rule.matches(tool_name, args):
                return rule.reason or f"{tool_name} is blocked by policy"
        return None
