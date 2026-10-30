import { Markdown } from "../../lib/markdown";

/**
 * Flat answer body (dsh shape, #265): the turn's process disclosure above the
 * answer carries the step/tool counts; the answer itself is plain prose.
 */
export function AnswerSheet({ content }: { content: string }) {
  if (!content.trim()) {
    return <p className="text-body text-tertiary">（空回答）</p>;
  }
  return <Markdown>{content}</Markdown>;
}
