import { useEffect, useState, type RefObject } from "react";

/** Live rendered width of an element (ResizeObserver). 0 until measured; the
 * caller decides the pre-measure fallback. Used by #346 to pick the navigator
 * layout from the actual right-column row, not the viewport guess. */
export function useElementWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setWidth(el.getBoundingClientRect().width);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);

  return width;
}
