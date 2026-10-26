import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cx } from "../../lib/cx";
import { Icon, type IconName } from "./Icon";

export type ButtonVariant = "quiet" | "outline" | "danger";
export type ButtonSize = "sm" | "md";

const BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-control active:scale-[.96] transition-[transform,background-color,color] duration-150 disabled:opacity-50 disabled:cursor-not-allowed";

const VARIANTS: Record<ButtonVariant, string> = {
  quiet: "text-secondary hover:bg-surface-hover hover:text-primary",
  outline: "border border-rule-strong text-primary hover:bg-surface-hover",
  danger:
    "border border-state-danger/40 text-state-danger hover:bg-state-danger/10",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-7 px-2 text-meta",
  md: "h-8 px-3 text-meta",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children: ReactNode;
}

export function Button({
  variant = "quiet",
  size = "md",
  className,
  type = "button",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(BASE, VARIANTS[variant], SIZES[size], className)}
      {...rest}
    >
      {children}
    </button>
  );
}

type IconButtonProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "aria-label" | "children"
> & {
  icon: IconName;
  "aria-label": string;
  variant?: ButtonVariant;
  size?: ButtonSize;
};

const ICON_SIZES: Record<ButtonSize, string> = {
  sm: "h-7 w-7",
  md: "h-8 w-8",
};

export function IconButton({
  icon,
  variant = "quiet",
  size = "md",
  className,
  type = "button",
  ...rest
}: IconButtonProps) {
  return (
    <button
      type={type}
      className={cx(BASE, VARIANTS[variant], ICON_SIZES[size], className)}
      {...rest}
    >
      <Icon name={icon} />
    </button>
  );
}
