/** The one place that spells the long-content wrapping contract (D-28 /
 * #258 AC1). Every face that renders arbitrary model/user text composes this
 * class: `min-w-0` lets the flex/grid child shrink, `overflow-wrap:anywhere`
 * breaks a 400-char token or a long URL at any character. Deliberately NO
 * global `overflow-x:hidden` — that would hide the regression instead of
 * fixing it. Code blocks / tables keep their own `overflow-auto` / `overflow-x`
 * so they scroll instead of breaking layout. */
export const LONG_TEXT = "min-w-0 [overflow-wrap:anywhere]";