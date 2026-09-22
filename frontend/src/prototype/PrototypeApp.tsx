// PROTOTYPE ONLY (branch prototype/frontend-redesign). Hosts the three design
// world variants behind ?prototype=1&variant=D|E|F with a floating switcher bar.
// The question: which visual world should the workbench redesign fold into?
import { useCallback, useEffect, useState } from "react";
import { VariantD, variantName as nameD } from "./VariantD";
import { VariantE, variantName as nameE } from "./VariantE";
import { VariantF, variantName as nameF } from "./VariantF";

const VARIANTS = [
  { key: "D", name: nameD, el: VariantD },
  { key: "E", name: nameE, el: VariantE },
  { key: "F", name: nameF, el: VariantF },
] as const;

function readVariant(): number {
  const v = new URLSearchParams(window.location.search).get("variant") ?? "D";
  const idx = VARIANTS.findIndex((x) => x.key === v.toUpperCase());
  return idx >= 0 ? idx : 0;
}

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
}

export function PrototypeApp() {
  const [idx, setIdx] = useState(readVariant);

  const go = useCallback((next: number) => {
    const wrapped = (next + VARIANTS.length) % VARIANTS.length;
    setIdx(wrapped);
    const params = new URLSearchParams(window.location.search);
    params.set("prototype", "1");
    params.set("variant", VARIANTS[wrapped].key);
    window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target)) return;
      if (e.key === "ArrowLeft") go(idx - 1);
      if (e.key === "ArrowRight") go(idx + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [idx, go]);

  const Active = VARIANTS[idx].el;
  return (
    <div className="h-full">
      <Active />
      {/* floating switcher — visibly NOT part of the design under evaluation */}
      <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[80] flex items-center gap-1 rounded-full border border-white/20 bg-black/85 text-white shadow-2xl px-2 py-1.5 backdrop-blur">
        <button
          onClick={() => go(idx - 1)}
          aria-label="上一个变体"
          className="w-8 h-8 rounded-full hover:bg-white/15 text-lg leading-none"
        >
          ←
        </button>
        <div className="px-3 text-sm font-medium whitespace-nowrap">
          <span className="font-mono text-amber-300">{VARIANTS[idx].key}</span>
          <span className="mx-1.5 opacity-40">·</span>
          {VARIANTS[idx].name}
          <span className="ml-2 text-[11px] opacity-50">?variant={VARIANTS[idx].key}</span>
        </div>
        <button
          onClick={() => go(idx + 1)}
          aria-label="下一个变体"
          className="w-8 h-8 rounded-full hover:bg-white/15 text-lg leading-none"
        >
          →
        </button>
      </div>
    </div>
  );
}
