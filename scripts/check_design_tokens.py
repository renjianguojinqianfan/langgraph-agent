#!/usr/bin/env python3
"""#261: DESIGN.md 的 frontmatter 与 index.css 的 ``@theme`` 必须逐项同值。

背景：``DESIGN.md`` 是 ``docs/frontend-design.md``（视觉 / 交互权威）的机器可读镜像，
``frontend/src/index.css`` 的 ``@theme`` 是实现。镜像漂了不会报错——界面照常工作，
只是镜像开始说谎，下一个人照着 DESIGN.md 改界面就会描错颜色 / 圆角 / 字号。此前
「镜像一致」只靠人自觉，本脚本把它变成一条机械断言（与 ``check_doc_links.py`` /
``check_no_machine_paths.py`` / ``check:sse`` 同一先例）。

比对面（任一不成立 exit 1）：

* **颜色 15 个**：DESIGN frontmatter ``colors.*`` ↔ ``--color-*``；
* **圆角 5 个**：``rounded.*`` ↔ ``--radius-*``；
* **排版 6 组**：``typography.*`` ↔ ``--text-*``（字号 / 行高 / 字重 / 字距 / 首个字体族，
  字重缺省按 CSS 默认 400 计）。DESIGN 的 ``label`` 角色在 CSS 里叫 ``meta``——
  impeccable 的 schema 角色名与 Tailwind 的 token 名不同，所以名字映射是显式表。

**为什么名字用显式映射表而不是「值集合相等」**：值集合比较放过同值互换（页底与槽底
两个近黑互换 = 语义全错但集合相同）；映射表让「换名 / 换值 / 互换」都必须由人改这里。

**刻意不比的**：字体 fallback 全串（DESIGN frontmatter 是短串、index.css 是长栈，
两边本就不同，见 docs/frontend-design.md §2）、``fontFeature: tabular-nums``
（用法在 utility 类上，不是 @theme token）、``components`` 段的 ``{colors.x}`` 引用解析、
**权威表 ``docs/frontend-design.md`` §1/§2 本身**（本守卫只保「镜像 ↔ 实现」；改了权威表
必须人工把镜像与实现一起对齐，再跑本守卫）。

解析面（防止「看起来守住了」的假绿）：聚合**全部** ``@theme`` 块（``@theme inline``
也收），最后一条声明不带分号也算；值支持 3/4/6/8 位 hex 与 ``rgb()`` / ``rgba()`` 的
逗号 / 空格两种写法；长度以 ``px`` 为准（换 rem 等会红，属预期——两边单位必须一致）。

跑法 / 接线：``python scripts/check_design_tokens.py``；pre-commit 钩子
``design-token-mirror``（改 DESIGN.md 或 index.css 时触发）+ CI ``backend-test`` job
（index.css 变更会触发本 workflow）。**已知边界**：纯 DESIGN.md 变更不触发 CI
（根级 ``*.md`` 在 paths 白名单外，见 #103 / #278 同款记录），那一侧靠 pre-commit
（未装钩子 / ``--no-verify`` / ``SKIP=`` 可绕过，与 #278 记录同类）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parent.parent
DESIGN_MD = "DESIGN.md"
INDEX_CSS = "frontend/src/index.css"

#: DESIGN.md frontmatter 颜色键 -> index.css @theme 变量。名字不同是常态（impeccable
#: 的语义名 vs Tailwind 的 utility 名），改名 / 换色必须改这张表，不能被值相等蒙混。
COLOR_TOKENS: dict[str, str] = {
    "page-ink": "--color-surface-page",
    "column-ink": "--color-surface-column",
    "sheet-ink": "--color-surface-sheet",
    "well-ink": "--color-surface-well",
    "hover-ink": "--color-surface-hover",
    "text-primary": "--color-primary",
    "text-secondary": "--color-secondary",
    "text-tertiary": "--color-tertiary",
    "rule": "--color-rule",
    "rule-strong": "--color-rule-strong",
    "signal-now": "--color-signal-now",
    "text-on-signal": "--color-on-signal",
    "state-ok": "--color-state-ok",
    "state-warn": "--color-state-warn",
    "state-danger": "--color-state-danger",
}

#: 圆角：DESIGN 与 CSS 同名（``rounded.control`` ↔ ``--radius-control``）。
RADIUS_TOKENS: dict[str, str] = {
    name: f"--radius-{name}" for name in ("control", "well", "sheet", "composer", "dialog")
}

#: 排版：DESIGN 角色 -> CSS 角色。``label`` 在 @theme 里叫 ``meta``（见文件头）。
TYPOGRAPHY_TOKENS: dict[str, str] = {
    "title": "title",
    "body": "body",
    "label": "meta",
    "machine": "machine",
    "code": "code",
    "invite": "invite",
}

#: 首个字体族 -> 字体栈 token；只比第一族：DESIGN frontmatter 用短串、CSS 用长栈，
#: 全串比对会红在「设计上刻意不同」的 fallback 列表上（docs/frontend-design.md §2）。
FONT_TOKENS: dict[str, str] = {
    "Inter Variable": "--font-sans",
    "JetBrains Mono": "--font-mono",
}

_HEX = re.compile(r"^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_LEGACY_RGB = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*"
    r"(?:,\s*(0|1|0?\.\d+)\s*)?\)$",
    re.IGNORECASE,
)
_MODERN_RGB = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s*"
    r"(?:/\s*(0|1|0?\.\d+)\s*)?\)$",
    re.IGNORECASE,
)
_PX = re.compile(r"^(-?\d+(?:\.\d+)?)px$")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
#: 一条 @theme 声明；末尾分号在 CSS 里可省（最后一条合法），所以 ``;`` 或块尾都收。
_THEME_ENTRY = re.compile(r"(--[A-Za-z0-9-]+)\s*:\s*([^;{}]+?)(?:;|\Z)")
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_THEME_START = re.compile(r"@theme\b[^{]*\{")


def parse_design(text: str) -> dict[str, Any]:
    """DESIGN.md 头部 ``---`` 包住的 YAML 映射（解析失败即 raise，失败即红）。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("文件不以 `---` frontmatter 开头")
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise ValueError("frontmatter 没有闭合的 `---`")
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter YAML 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("frontmatter 不是 YAML 映射")
    return data


def _theme_blocks(css: str) -> list[str]:
    """所有 ``@theme { ... }`` / ``@theme inline { ... }`` 块的内容（注释先剥掉）。

    聚合**全部**块而不是第一个：Tailwind v4 允许多个 @theme 块合并（``@theme inline``
    也是合法写法），只看第一块会让后面块里的漂移静默逃逸。
    """
    blocks: list[str] = []
    position = 0
    while True:
        match = _THEME_START.search(css, position)
        if match is None:
            break
        depth = 1
        for index in range(match.end(), len(css)):
            char = css[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(css[match.end() : index])
                    position = index + 1
                    break
        else:
            raise ValueError("@theme 块没有闭合")
    if not blocks:
        raise ValueError("index.css 里找不到 @theme 块")
    return blocks


def parse_theme(css: str) -> dict[str, str]:
    """index.css 文本 -> {CSS 变量名: 值}（只取 @theme 块；跨行值折叠成单空格）。

    多个 @theme 块按文档顺序合并；同名变量后者胜（与 CSS 层叠一致）。
    """
    tokens: dict[str, str] = {}
    for block in _theme_blocks(_CSS_COMMENT.sub("", css)):
        for match in _THEME_ENTRY.finditer(block):
            tokens[match.group(1)] = " ".join(match.group(2).split())
    if not tokens:
        raise ValueError("@theme 块里解析不到任何 token")
    return tokens


def _color(value: Any) -> tuple[float, float, float, float] | None:
    """hex / rgb(a) -> (r, g, b, a)；不是颜色返回 None（由调用方报漂移）。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    hex_match = _HEX.match(text)
    if hex_match:
        digits = hex_match.group(1).lower()
        if len(digits) in (3, 4):
            digits = "".join(char * 2 for char in digits)
        red, green, blue = (float(int(digits[index : index + 2], 16)) for index in (0, 2, 4))
        alpha = float(int(digits[6:8], 16)) / 255.0 if len(digits) == 8 else 1.0
        return (red, green, blue, alpha)
    rgb_match = _LEGACY_RGB.match(text) or _MODERN_RGB.match(text)
    if rgb_match:
        return (
            float(rgb_match.group(1)),
            float(rgb_match.group(2)),
            float(rgb_match.group(3)),
            float(rgb_match.group(4) or 1.0),
        )
    return None


def _px(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _PX.match(value.strip())
        if match is not None:
            return float(match.group(1))
    return None


def _radius(value: Any) -> tuple[float, ...] | None:
    number = _px(value)
    return (number,) if number is not None else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and _NUMBER.match(value.strip()):
        return float(value.strip())
    return None


def _first_family(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    first = value.split(",", 1)[0].strip().strip("\"'")
    return first or None


def _normalize_text(value: Any) -> str | None:
    """小文本值（如字距 ``-0.01em``）的归一化；None 表示两边都没有。"""
    if value is None:
        return None
    return " ".join(str(value).split())


def _section(design: dict[str, Any], key: str, drifts: list[str]) -> dict[str, Any]:
    value = design.get(key)
    if not isinstance(value, dict):
        drifts.append(f"DESIGN.md frontmatter 缺 `{key}:` 映射（或形状不是映射）")
        return {}
    return value


def _compare_scalar_tokens(
    design: dict[str, Any],
    theme: dict[str, str],
    drifts: list[str],
    *,
    section: str,
    theme_prefix: str,
    mapping: dict[str, str],
    noun: str,
    normalize: Callable[[Any], tuple[float, ...] | None],
    expected: str,
) -> None:
    """「同名语义、单值」的两组 token（颜色 / 圆角）的通用比对。"""
    tokens = _section(design, section, drifts)
    actual = {name: value for name, value in theme.items() if name.startswith(theme_prefix)}
    for design_name, css_name in mapping.items():
        design_value = tokens.get(design_name)
        css_value = actual.get(css_name)
        if design_value is None:
            drifts.append(f"{section}.{design_name}: DESIGN.md 缺这个{noun}（index.css 有 {css_name}）")
        elif css_value is None:
            drifts.append(f"{section}.{design_name}: index.css @theme 缺 {css_name}")
        else:
            expected_value, actual_value = normalize(design_value), normalize(css_value)
            if expected_value is None:
                drifts.append(f"{section}.{design_name}: DESIGN.md 值 {design_value!r} 不是 {expected}")
            elif actual_value is None:
                drifts.append(f"{css_name}: index.css 值 {css_value!r} 不是 {expected}")
            elif expected_value != actual_value:
                drifts.append(f"{section}.{design_name}: DESIGN.md {design_value!r} != index.css {css_name} {css_value!r}")
    for extra in sorted(set(tokens) - set(mapping)):
        drifts.append(f"{section}.{extra}: DESIGN.md 多了未映射的{noun}（映射表在 scripts/check_design_tokens.py）")
    for extra in sorted(set(actual) - set(mapping.values())):
        drifts.append(f"{extra}: index.css @theme 多了 DESIGN.md 未声明的{noun}")


def _compare_typography(design: dict[str, Any], theme: dict[str, str], drifts: list[str]) -> None:
    typography = _section(design, "typography", drifts)
    for design_name, css_name in TYPOGRAPHY_TOKENS.items():
        spec = typography.get(design_name)
        if not isinstance(spec, dict):
            drifts.append(f"typography.{design_name}: DESIGN.md 缺这一组排版（或形状不是映射）")
            continue
        prefix = f"--text-{css_name}"

        size = _px(spec.get("fontSize"))
        css_size = _px(theme.get(prefix))
        if size is None:
            drifts.append(f"typography.{design_name}.fontSize: DESIGN.md 缺值或不是 px")
        elif css_size is None:
            drifts.append(f"typography.{design_name}.fontSize: index.css @theme 缺 {prefix}")
        elif size != css_size:
            drifts.append(
                f"typography.{design_name}.fontSize: DESIGN.md {spec['fontSize']!r} "
                f"!= index.css {prefix} {theme[prefix]!r}"
            )

        line_height = _number(spec.get("lineHeight"))
        css_line_height = _number(theme.get(f"{prefix}--line-height"))
        if line_height is None:
            drifts.append(f"typography.{design_name}.lineHeight: DESIGN.md 缺值或不是数字")
        elif css_line_height is None:
            drifts.append(f"typography.{design_name}.lineHeight: index.css @theme 缺 {prefix}--line-height")
        elif line_height != css_line_height:
            drifts.append(
                f"typography.{design_name}.lineHeight: DESIGN.md {spec['lineHeight']!r} "
                f"!= index.css {theme[prefix + '--line-height']!r}（{prefix}--line-height）"
            )

        weight = _number(spec.get("fontWeight"))
        # 比的是 token 的缺省字重：@theme 不写 --font-weight 时 CSS 默认 400。
        # 组件里显式的 font-semibold 等 utility 是用法、不是 token 漂移。
        css_weight = _number(theme.get(f"{prefix}--font-weight"))
        if css_weight is None:
            css_weight = 400.0
        if weight is None:
            drifts.append(f"typography.{design_name}.fontWeight: DESIGN.md 缺值或不是数字")
        elif weight != css_weight:
            drifts.append(
                f"typography.{design_name}.fontWeight: DESIGN.md {spec['fontWeight']!r} "
                f"!= index.css 默认值 {css_weight:g}（{prefix}--font-weight）"
            )

        letter_spacing = _normalize_text(spec.get("letterSpacing"))
        css_letter_spacing = _normalize_text(theme.get(f"{prefix}--letter-spacing"))
        if letter_spacing != css_letter_spacing:
            drifts.append(
                f"typography.{design_name}.letterSpacing: DESIGN.md {letter_spacing!r} "
                f"!= index.css {css_letter_spacing!r}（{prefix}--letter-spacing）"
            )

        family = _first_family(spec.get("fontFamily"))
        if family is None:
            drifts.append(f"typography.{design_name}.fontFamily: DESIGN.md 缺字体")
        else:
            css_font = FONT_TOKENS.get(family)
            if css_font is None:
                drifts.append(
                    f"typography.{design_name}.fontFamily: DESIGN.md 字体 {family!r} "
                    "不在 FONT_TOKENS 里（新增字体要先补映射）"
                )
            else:
                css_family = _first_family(theme.get(css_font))
                if css_family is None:
                    drifts.append(f"typography.{design_name}.fontFamily: index.css @theme 缺 {css_font}")
                elif css_family != family:
                    drifts.append(
                        f"typography.{design_name}.fontFamily: DESIGN.md {family!r} != index.css {css_font} {css_family!r}"
                    )

    for extra in sorted(set(typography) - set(TYPOGRAPHY_TOKENS)):
        drifts.append(f"typography.{extra}: DESIGN.md 多了未映射的排版组（映射表在 scripts/check_design_tokens.py）")
    # 基础字号 token 形如 --text-body；--text-body--line-height / --text-body-sm 都不算。
    css_roles = {
        name[len("--text-") :]
        for name in theme
        if re.fullmatch(r"--text-[a-z0-9]+(?:-[a-z0-9]+)*", name) is not None
    }
    for extra in sorted(css_roles - set(TYPOGRAPHY_TOKENS.values())):
        drifts.append(f"--text-{extra}: index.css @theme 多了 DESIGN.md 未声明的排版组")
    for extra in sorted({name for name in theme if name.startswith("--font-")} - set(FONT_TOKENS.values())):
        drifts.append(f"{extra}: index.css @theme 多了 DESIGN.md 未声明的字体栈")


def compare(design: dict[str, Any], theme: dict[str, str]) -> list[str]:
    """两组 token 的全量差异（空列表 == 一致）。纯函数，便于测试里造漂移。"""
    drifts: list[str] = []
    _compare_scalar_tokens(
        design,
        theme,
        drifts,
        section="colors",
        theme_prefix="--color-",
        mapping=COLOR_TOKENS,
        noun="颜色",
        normalize=_color,
        expected="hex / rgb(a) 颜色",
    )
    _compare_scalar_tokens(
        design,
        theme,
        drifts,
        section="rounded",
        theme_prefix="--radius-",
        mapping=RADIUS_TOKENS,
        noun="圆角",
        normalize=_radius,
        expected="px 长度",
    )
    _compare_typography(design, theme, drifts)
    return drifts


def check(root: Path = ROOT) -> list[str]:
    """读 ``root`` 下的两个文件并比对；解析失败按漂移返回（失败即红，不静默放过）。"""
    drifts: list[str] = []
    design: dict[str, Any] | None = None
    theme: dict[str, str] | None = None
    try:
        design = parse_design((root / DESIGN_MD).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        drifts.append(f"{DESIGN_MD} 解析失败: {exc}")
    try:
        theme = parse_theme((root / INDEX_CSS).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        drifts.append(f"{INDEX_CSS} 解析失败: {exc}")
    if design is not None and theme is not None:
        drifts.extend(compare(design, theme))
    return drifts


def main() -> int:
    drifts = check()
    if not drifts:
        print(
            "设计 token 镜像一致：DESIGN.md <-> index.css @theme"
            f"（{len(COLOR_TOKENS)} 色 / {len(RADIUS_TOKENS)} 圆角 / {len(TYPOGRAPHY_TOKENS)} 组排版）"
        )
        return 0
    print(
        "设计 token 镜像漂移（DESIGN.md frontmatter <-> frontend/src/index.css @theme）：",
        file=sys.stderr,
    )
    for drift in drifts:
        print(f"  - {drift}", file=sys.stderr)
    print(
        f"共 {len(drifts)} 处；改任一侧必须同步另一侧（锚点：docs/frontend-design.md §1/§2）。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
