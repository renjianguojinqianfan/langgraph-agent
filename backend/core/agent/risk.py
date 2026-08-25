"""Planning-phase risk scanning (P1 item 1 — EHRB layer 1).

The planner produces a plan; the ``risk_scan`` graph node inspects every step
before the executor runs anything. This module provides:

* :data:`DANGER_KEYWORDS` — five built-in danger keyword categories
  (delete / database / financial / privacy / system);
* :func:`scan_keywords` — deterministic, case-insensitive keyword matching;
* :func:`scan_semantic` — optional LLM semantic judgement (needs an aux or the
  main model; ``None`` skips it so offline mode never pays an extra LLM call);
* :func:`scan_tool_output` — EHRB layer-3 skeleton (P1: warning only);
* :class:`RiskScanner` — facade combining keyword + optional semantic scan.

Risk handling contract (see the architecture document):

* ``risk_policy=confirm`` (default): a high-risk round marks ``_risk_blocked``
  and the executor raises ``need_confirm`` for every tool call of that round —
  reusing the existing P0 ``human_confirm`` mechanism. **No new confirmation
  state machine is introduced** and the ``_needs_confirm`` recomputation in
  ``human_confirm_node`` is left untouched.
* ``risk_policy=pause``: the risk node performs a single plan-level blocking
  confirmation (confirm key ``risk_plan``) before the executor runs.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, List, Optional

from ...config import Settings
from ...utils.logging import get_logger

logger = get_logger("agent.risk")


class RiskLevel(str, Enum):
    """Risk level of a single plan step."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskAction(str, Enum):
    """Disposition suggested for a plan step."""

    ALLOW = "allow"
    CONFIRM = "confirm"
    BLOCK = "block"


# Built-in danger keyword table (five categories). Matching is
# case-insensitive; keep entries short and specific to limit false positives.
DANGER_KEYWORDS: Dict[str, List[str]] = {
    "delete": [
        "rm -rf",
        "rm -r",
        "del ",
        "删除",
        "删掉",
        "格式化",
        "清空",
    ],
    "database": [
        "drop table",
        "drop database",
        "delete from",
        "truncate table",
        "truncate",
    ],
    "financial": [
        "转账",
        "汇款",
        "支付",
        "付款",
        "资金",
        "transfer",
    ],
    "privacy": [
        "发邮件",
        "发送邮件",
        "发短信",
        "发布",
        "泄露",
        "隐私",
    ],
    "system": [
        "关机",
        "重启",
        "提权",
        "停服",
        "shutdown",
        "reboot",
        "sudo",
    ],
}


def _norm(text: str) -> str:
    return (text or "").lower()


def _parse_extra_keywords(raw: str) -> List[str]:
    """Parse the optional ``risk_danger_keywords`` JSON array override."""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        logger.warning("risk_danger_keywords is not valid JSON; ignoring override.")
        return []
    if not isinstance(data, list):
        logger.warning("risk_danger_keywords must be a JSON array; ignoring override.")
        return []
    return [str(k) for k in data if str(k).strip()]


def _suggestion(level: RiskLevel, matched: List[str]) -> str:
    if level == RiskLevel.HIGH:
        return (
            "检测到高危关键词（"
            + "、".join(matched[:5])
            + "），建议人工确认后再执行相关操作"
        )
    if level == RiskLevel.MEDIUM:
        return "检测到风险特征，建议谨慎执行"
    return ""


def scan_keywords(
    plan: List[Any],
    extra_keywords: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Scan plan steps for danger keywords.

    ``plan`` is the list of step dicts produced by the planner (``description``
    field) or plain strings. Returns a list of :class:`RiskItem`-compatible
    dicts (``step_index`` / ``level`` / ``matched_keywords`` / ``suggestion`` /
    ``action``), one per plan step.
    """
    categories: Dict[str, List[str]] = {}
    for cat, words in DANGER_KEYWORDS.items():
        categories[cat] = list(words)
    if extra_keywords:
        categories.setdefault("custom", []).extend(extra_keywords)

    items: List[Dict[str, Any]] = []
    for i, step in enumerate(plan, 1):
        if isinstance(step, dict):
            desc = str(step.get("description") or step.get("step") or "")
        else:
            desc = str(step)
        norm = _norm(desc)
        matched: List[str] = []
        for words in categories.values():
            for w in words:
                if w and _norm(w) in norm:
                    matched.append(w)
        # De-duplicate while keeping order.
        seen: set = set()
        unique: List[str] = []
        for w in matched:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        if unique:
            level = RiskLevel.HIGH
            action = RiskAction.CONFIRM
        else:
            level = RiskLevel.NONE
            action = RiskAction.ALLOW
        items.append(
            {
                "step_index": i,
                "level": level.value,
                "matched_keywords": unique,
                "suggestion": _suggestion(level, unique),
                "action": action.value,
            }
        )
    return items


def scan_semantic(
    llm: Any,
    step: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Optional LLM semantic risk judgement for one plan step.

    ``llm`` may be the aux model or the main model; ``None`` (no aux
    configured) skips the analysis entirely and returns ``None``. The prompt
    asks for a one-line verdict containing ``high`` / ``medium`` / ``low`` so
    the offline :class:`MockAuxLLMClient` can script a deterministic answer.
    """
    if llm is None:
        return None
    desc = str(step.get("description") or "")
    prompt = (
        "请对以下 Agent 计划步骤做风险语义分析，仅输出一行结论，格式为 "
        "'high: 理由' / 'medium: 理由' / 'low: 理由'。"
        "判断该操作是否涉及删除、破坏、资金转移、隐私外发或系统级变更等风险。\n"
        f"步骤描述：{desc}"
    )
    try:
        resp = llm.complete([{"role": "user", "content": prompt}])
        content = (resp.content or "").strip().lower()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("scan_semantic failed: %s", exc)
        return None
    if not content:
        return None
    if "high" in content:
        level = RiskLevel.HIGH
    elif "medium" in content:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW
    return {
        "level": level.value,
        "matched_keywords": [],
        "suggestion": content[:120] if content else _suggestion(level, []),
        "semantic": True,
    }


def scan_tool_output(output: Any) -> List[Dict[str, Any]]:
    """EHRB layer-3 skeleton (P1): tool-output re-check.

    Full implementation is deferred to P2; the skeleton never blocks the main
    flow — it only warns and returns an empty report.
    """
    logger.warning("scan_tool_output is a P1 skeleton; tool output re-check not enabled.")
    return []


class RiskScanner:
    """Facade combining keyword scanning with optional semantic analysis."""

    def __init__(
        self,
        settings: Settings,
        aux_llm: Any = None,
        semantic_enabled: bool = False,
    ) -> None:
        self.settings = settings
        self.aux_llm = aux_llm
        self.semantic_enabled = bool(semantic_enabled)
        self._extra = _parse_extra_keywords(settings.risk_danger_keywords)

    def scan(self, plan: List[Any]) -> List[Dict[str, Any]]:
        """Run the full scan pipeline for ``plan``.

        Steps already flagged ``high`` by the keyword scan are kept as-is;
        semantic analysis only runs for steps that did **not** match a keyword
        (avoids wasting aux calls on already-blocked steps).
        """
        items = scan_keywords(plan, self._extra)
        if not self.semantic_enabled or self.aux_llm is None:
            return items
        for i, step in enumerate(plan, 1):
            item = items[i - 1] if i - 1 < len(items) else None
            if item is None:
                continue
            if item["level"] == RiskLevel.HIGH.value:
                continue  # already flagged; no semantic call needed
            semantic = scan_semantic(self.aux_llm, step if isinstance(step, dict) else {"description": str(step)})
            if semantic is None:
                continue
            if semantic["level"] in (RiskLevel.HIGH.value, RiskLevel.MEDIUM.value):
                items[i - 1] = {
                    "step_index": i,
                    "level": semantic["level"],
                    "matched_keywords": [],
                    "suggestion": semantic["suggestion"],
                    "action": RiskAction.CONFIRM.value,
                }
        return items
