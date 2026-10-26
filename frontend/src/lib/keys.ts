/**
 * Enter sends; Shift+Enter inserts a newline.
 *
 * An Enter that commits an IME composition belongs to the input method, not
 * the composer — sending it would submit a half-typed Chinese/Japanese
 * sentence. `isComposing` is true for that keydown in Chrome / Safari / Edge.
 */
export function shouldSendOnEnter(
  key: string,
  shiftKey: boolean,
  isComposing: boolean
): boolean {
  return key === "Enter" && !shiftKey && !isComposing;
}
