import { Icon } from "../ui/Icon";

interface RailSearchProps {
  value: string;
  onChange: (value: string) => void;
}

export function RailSearch({ value, onChange }: RailSearchProps) {
  return (
    <div role="search" className="relative">
      <Icon
        name="search"
        size={14}
        className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-tertiary"
      />
      <input
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-label="搜索任务"
        placeholder="搜索任务"
        autoComplete="off"
        spellCheck={false}
        className="h-8 w-full rounded-control bg-surface-well pl-7 pr-7 text-meta text-primary placeholder:text-tertiary"
      />
      {value && (
        <button
          type="button"
          aria-label="清除搜索"
          onClick={() => onChange("")}
          className="absolute right-1 top-1/2 inline-flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-control text-tertiary hover:bg-surface-hover hover:text-primary"
        >
          <Icon name="close" size={12} />
        </button>
      )}
    </div>
  );
}
