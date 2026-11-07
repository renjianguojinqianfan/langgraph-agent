import { useMediaQuery } from "../hooks/useMediaQuery";

/** Single source of the drawer breakpoints (D-25 / #257).
 *
 * Tailwind v4 compiles `max-[860px]:` to `@media not all and (min-width: 860px)`
 * — i.e. strictly `width < 860px`, so 860 itself is still the wide layout. The
 * JS queries below spell the same `<` semantics (not `max-width: 859.98px`),
 * so CSS and `matchMedia` agree at the boundary. */
export const LEFT_DRAWER_MAX = 860;
export const RIGHT_DRAWER_MAX = 1180;

export const LEFT_DRAWER_QUERY = `(width < ${LEFT_DRAWER_MAX}px)`;
export const RIGHT_DRAWER_QUERY = `(width < ${RIGHT_DRAWER_MAX}px)`;

/** True exactly when the left rail is shown as a narrow-screen drawer. */
export function useIsLeftDrawer(): boolean {
  return useMediaQuery(LEFT_DRAWER_QUERY);
}

/** True exactly when the right dash is shown as a narrow-screen drawer. */
export function useIsRightDrawer(): boolean {
  return useMediaQuery(RIGHT_DRAWER_QUERY);
}

/** The rail is static (not a drawer) whenever the left drawer query is false. */
export function useRailStatic(): boolean {
  return !useIsLeftDrawer();
}