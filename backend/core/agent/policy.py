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

坏配置一律 warning + 忽略，绝不因为规则表写错把运行炸掉（与 ``risk_danger_keywords``
同口径）；**空表 = 不拦**，与改造前逐字节一致。
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from ...config import Settings
from ...utils.logging import get_logger

logger = get_logger("agent.policy")


def _args_text(args: Any) -> str:
    """入参的判据文本：JSON 序列化（``default=str``），命令类参数会原样出现。"""
    try:
        return json.dumps(args, ensure_ascii=False, default=str)
    except Exception:  # pragma: no cover - defensive
        return str(args)


@dataclass(frozen=True)
class BlockRule:
    """一条「命中即拒」的规则。

    ``tool`` 是工具名，支持 ``fnmatch`` 通配（如 ``mcp__*__write``）；
    ``pattern`` 可选，是对入参 JSON 文本的不区分大小写正则搜索——即「命令模式」，
    省略则**该工具的任何一次调用**都拦。
    """

    tool: str
    pattern: Optional["re.Pattern[str]"]
    reason: str

    def matches(self, tool_name: str, args: Mapping[str, Any]) -> bool:
        if not fnmatch.fnmatchcase(tool_name, self.tool):
            return False
        if self.pattern is None:
            return True
        return self.pattern.search(_args_text(args)) is not None


def _compile(rule: Mapping[str, Any]) -> Optional[BlockRule]:
    """一条规则 dict -> :class:`BlockRule`；写坏的那条 warning 后作废。"""
    tool = str(rule.get("tool") or "").strip()
    if not tool:
        logger.warning("policy_rules entry without a 'tool'; ignored: %r", rule)
        return None
    pattern: Optional[re.Pattern[str]] = None
    raw_pattern = rule.get("pattern")
    if raw_pattern:
        try:
            pattern = re.compile(str(raw_pattern), re.IGNORECASE)
        except re.error:
            logger.warning("policy_rules pattern is not a valid regex; rule ignored: %r", rule)
            return None
    # 没写 reason 也必须有话可回：模型看到的是一句拒绝，不是一个空对象。
    reason = str(rule.get("reason") or "").strip() or f"{tool} is blocked by policy"
    return BlockRule(tool=tool, pattern=pattern, reason=reason)


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
        规则按表序判定，**第一条命中的说了算**。
        """
        for rule in self.rules:
            if rule.matches(tool_name, args):
                return rule.reason
        return None
