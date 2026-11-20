import { useEffect, useState } from "react";

/** Live viewport width for layout rules that need a number (not just a
 * matchMedia boolean). #346: viewport < 768 always fills, even when the row
 * itself is wide. */
export function useViewportWidth(): number {
  const [width, setWidth] = useState(() =>
    typeof window === "undefined" ? 0 : window.innerWidth
  );

  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return width;
}
