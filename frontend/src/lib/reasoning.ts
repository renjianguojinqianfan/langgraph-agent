import type { StepRecord } from "../types";

/**
 * #267 / D-40: only a non-empty reasoning renders the collapsible "思考" block.
 * A non-thinking model must not get an empty block; old records (field absent)
 * follow the same rule.
 */
export function stepHasReasoning(
  step: Pick<StepRecord, "reasoning">
): boolean {
  return typeof step.reasoning === "string" && step.reasoning.trim().length > 0;
}
