export function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-sheet bg-surface-hover px-3.5 py-2 text-body text-primary">
        {text}
      </div>
    </div>
  );
}
