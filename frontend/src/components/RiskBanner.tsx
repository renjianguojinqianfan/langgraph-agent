import { RiskItem } from "../types";

const LEVEL_STYLES: Record<string, string> = {
  high: "bg-red-500/15 text-red-300 border-red-500/40",
  medium: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  low: "bg-slate-500/15 text-slate-300 border-slate-500/40",
  none: "bg-slate-800 text-slate-400 border-slate-700",
};

/** P1 item 1: risk-scan banner shown in the run flow after ``risk_report``. */
export function RiskBanner({ items }: { items: RiskItem[] }) {
  const visible = (items ?? []).filter((it) => it.level !== "none");
  if (visible.length === 0) return null;
  return (
    <div className="rounded border border-slate-700 bg-slate-900 p-3 mb-3">
      <div className="text-xs font-semibold text-amber-300 mb-2">🛡 风险扫描</div>
      <div className="space-y-1.5">
        {visible.map((it, i) => (
          <div
            key={i}
            className={`rounded border px-2 py-1 text-xs ${
              LEVEL_STYLES[it.level] ?? LEVEL_STYLES.none
            }`}
          >
            <span className="font-mono">Step {it.step_index}</span>{" "}
            <span className="font-semibold uppercase">{it.level}</span>
            {it.matched_keywords.length > 0 && (
              <span className="font-mono text-[11px] ml-1">
                {it.matched_keywords.join("、")}
              </span>
            )}
            {it.suggestion && (
              <div className="text-slate-300 mt-0.5">{it.suggestion}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
