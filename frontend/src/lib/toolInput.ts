/** Tool input longer than this many Unicode code points gets an explicit
 * "展开全部 / 收起" control in the expanded ToolRow (#258 AC2). */
export const TOOL_INPUT_PREVIEW_LIMIT = 800;

/** Whether the expanded tool input needs the show-all affordance.
 *
 * Counts code points (`[...text].length`) so CJK and emoji are measured the
 * same way as ASCII — a 2000-char token is 2000 units, a surrogate pair is 1.
 * The threshold is inclusive: exactly `limit` does not need expanding. */
export function inputNeedsExpand(
  text: string,
  limit: number = TOOL_INPUT_PREVIEW_LIMIT
): boolean {
  return [...text].length > limit;
}