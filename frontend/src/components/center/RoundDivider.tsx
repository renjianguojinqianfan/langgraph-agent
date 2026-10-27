export function RoundDivider({ n }: { n: number }) {
  return (
    <div className="flex items-center gap-3 text-meta text-tertiary">
      <span aria-hidden="true" className="h-px flex-1 bg-rule" />
      <span>第 {n} 轮</span>
      <span aria-hidden="true" className="h-px flex-1 bg-rule" />
    </div>
  );
}
