/** Minimal step shape the summary needs (testable without a full record). */
export interface StepSummaryInput {
  thought: string;
  tool_calls: ReadonlyArray<{ tool_name: string }>;
}

/** Row-head copy for a step whose thought is the round's final answer. */
export const FINAL_ANSWER_SUMMARY = "最终回答";

/**
 * Row-head summary for one step.
 *
 * The backend stores the final answer in `step.thought` as well as publishing
 * it to the conversation; `AnswerSheet` renders the answer below the process
 * block, so echoing the same text in the step's row summary duplicated it in
 * the first row (#287). Any thought equal (after trim) to one of the round's
 * answers collapses to 「最终回答」; every other step keeps the old precedence
 * (thought → tool names → 「步骤」).
 */
export function stepSummary(
  step: StepSummaryInput,
  answers: readonly string[] = []
): string {
  const thought = step.thought.trim();
  if (thought && answers.some((answer) => answer.trim() === thought)) {
    return FINAL_ANSWER_SUMMARY;
  }
  return thought || step.tool_calls.map((tool) => tool.tool_name).join("、") || "步骤";
}
