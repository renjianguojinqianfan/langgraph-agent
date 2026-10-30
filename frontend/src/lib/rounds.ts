import type { ChatTurn, StepRecord } from "../types";

export interface Round {
  /** 1-based conversation round, matching the backend's user-turn count. Used
   * to attribute steps (#265); never rendered. 0 = leading assistant-only block. */
  index: number;
  user: ChatTurn | null;
  answers: ChatTurn[];
}

/**
 * Split the flat conversation into rounds at each user turn. Assistant turns
 * before the first user turn stay in a leading round without a user bubble;
 * that block is index 0 so it does not shift the backend's round numbers.
 */
export function splitRounds(conversation: ChatTurn[]): Round[] {
  const rounds: Round[] = [];
  let userRounds = 0;
  for (const turn of conversation) {
    const current = rounds[rounds.length - 1];
    if (turn.role === "user" || !current) {
      if (turn.role === "user") userRounds += 1;
      rounds.push({
        index: turn.role === "user" ? userRounds : 0,
        user: turn.role === "user" ? turn : null,
        answers: turn.role === "user" ? [] : [turn],
      });
    } else {
      current.answers.push(turn);
    }
  }
  return rounds;
}

/** Steps attributed to one 1-based conversation round (#265). */
export function stepsForRound(steps: StepRecord[], round: number): StepRecord[] {
  return steps.filter((step) => step.round === round);
}

/**
 * Steps persisted before #265 (no round). They are rendered once as a legacy
 * block instead of being guessed into the last round — that guess caused the
 * cross-round leak this field exists to remove.
 */
export function legacySteps(steps: StepRecord[]): StepRecord[] {
  return steps.filter((step) => step.round == null || step.round === 0);
}

/**
 * Plan updates are plumbing, not an answer: render them as a muted line.
 *
 * The note is a protocol line, not a prefix: the backend's only producer is
 * `_plan_note_text(version)` (backend/core/agent/nodes.py), which always emits
 * ``计划已更新到第 N 版。``. Matching the whole sentence keeps a model answer that
 * merely starts with the same words on the AnswerSheet (#231).
 */
const PLAN_NOTE_RE = /^计划已更新到第 \d+ 版。$/;

export function isPlanNotice(content: string): boolean {
  return PLAN_NOTE_RE.test(content.trim());
}
