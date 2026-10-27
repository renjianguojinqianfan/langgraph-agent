import type { ChatTurn } from "../types";

export interface Round {
  /** 1-based; rendered as 「第 N 轮」. */
  index: number;
  user: ChatTurn | null;
  answers: ChatTurn[];
}

/**
 * Split the flat conversation into rounds at each user turn. Assistant turns
 * before the first user turn stay in a leading round without a user bubble.
 */
export function splitRounds(conversation: ChatTurn[]): Round[] {
  const rounds: Round[] = [];
  for (const turn of conversation) {
    const current = rounds[rounds.length - 1];
    if (turn.role === "user" || !current) {
      rounds.push({
        index: rounds.length + 1,
        user: turn.role === "user" ? turn : null,
        answers: turn.role === "user" ? [] : [turn],
      });
    } else {
      current.answers.push(turn);
    }
  }
  return rounds;
}

/** Steps belong to the last round with a user turn; they are not per-round. */
export function stepsRoundIndex(rounds: Round[]): number {
  for (let i = rounds.length - 1; i >= 0; i -= 1) {
    if (rounds[i].user) return i;
  }
  return rounds.length - 1;
}

/** Plan updates are plumbing, not an answer: render them as a muted line. */
export function isPlanNotice(content: string): boolean {
  return content.trimStart().startsWith("计划已更新到");
}
