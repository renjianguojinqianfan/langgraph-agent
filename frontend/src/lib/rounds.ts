import type { ChatTurn, StepRecord } from "../types";

export interface Round {
  /** 1-based conversation round, matching the backend's user-turn count. Used
   * to attribute steps (#265); never rendered. */
  index: number;
  user: ChatTurn;
  answers: ChatTurn[];
}

/**
 * Split the flat conversation into rounds at each user turn. The backend's
 * current contract opens every ledger with the user's first turn (ADR 0013
 * decision 1), so rounds are exactly the user turns and their following
 * assistant answers.
 */
export function splitRounds(conversation: ChatTurn[]): Round[] {
  const rounds: Round[] = [];
  for (const turn of conversation) {
    if (turn.role === "user") {
      rounds.push({ index: rounds.length + 1, user: turn, answers: [] });
    } else {
      rounds[rounds.length - 1]?.answers.push(turn);
    }
  }
  return rounds;
}

/** Steps attributed to one 1-based conversation round (#265). */
export function stepsForRound(steps: StepRecord[], round: number): StepRecord[] {
  return steps.filter((step) => step.round === round);
}

export type TurnVerdict = "failed" | "running" | "done";

/**
 * A round's verdict comes from its last step (#287 review F2): a round resumed
 * after a hard kill carries the dead attempt as a failed step, while the
 * answer that followed is what the round delivered. `live` keeps 执行中 while
 * the round is still running even if its last persisted step already closed.
 * A live round never leaves an earlier step running (startup settlement closes
 * stale steps), so the last step is the only verdict source.
 */
export function turnVerdict(steps: StepRecord[], live: boolean): TurnVerdict {
  const last = steps[steps.length - 1];
  if (last?.status === "failed") return "failed";
  if (live || last?.status === "running") return "running";
  return "done";
}

/**
 * Plan updates are plumbing, not an answer: render them as a muted line.
 *
 * The note is a protocol line, not a prefix: the backend's only producer is
 * `plan_file.plan_note_text` (re-exported through `_plan_note_text` in
 * `backend/core/agent/nodes.py`), which always emits ``计划已更新到第 N 版。``.
 * Matching the whole sentence keeps a model answer that merely starts with the
 * same words on the AnswerSheet (#231).
 */
const PLAN_NOTE_RE = /^计划已更新到第 \d+ 版。$/;

export function isPlanNotice(content: string): boolean {
  return PLAN_NOTE_RE.test(content.trim());
}
