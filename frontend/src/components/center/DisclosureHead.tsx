import type { ReactNode } from "react";
import { cx } from "../../lib/cx";
import { Icon } from "../ui/Icon";

interface DisclosureHeadProps {
  open: boolean;
  onToggle: () => void;
  /** Status dot class; the row shape is shared by step and turn disclosures. */
  dot: string;
  children: ReactNode;
}

export function DisclosureHead({ open, onToggle, dot, children }: DisclosureHeadProps) {
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={onToggle}
      className="flex w-full items-center gap-2.5 px-1 py-2 text-left"
    >
      <span aria-hidden="true" className={cx("h-1.5 w-1.5 shrink-0 rounded-full", dot)} />
      {children}
      <Icon
        name="chevron"
        size={14}
        className={cx(
          "ml-auto shrink-0 text-tertiary transition-transform duration-150",
          open && "rotate-180"
        )}
      />
    </button>
  );
}
