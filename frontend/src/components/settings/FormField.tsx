import { useId } from "react";

interface FormFieldProps {
  label: string;
  name: string;
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
  autoFocus?: boolean;
  /** text (default) or password for write-only secrets. */
  type?: "text" | "password";
  disabled?: boolean;
  /** Field-level validation copy (readable reason + next step, §6). */
  error?: string;
}

/** Labelled text input for forms (design §6: every control has a name). */
export function FormField({
  label,
  name,
  value,
  placeholder,
  onChange,
  autoFocus = false,
  type = "text",
  disabled = false,
  error,
}: FormFieldProps) {
  const id = useId();
  const errorId = useId();
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-3">
        <label htmlFor={id} className="w-24 shrink-0 text-meta text-secondary">
          {label}
        </label>
        <input
          id={id}
          name={name}
          type={type}
          autoComplete="off"
          autoFocus={autoFocus}
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 min-w-0 flex-1 rounded-control border border-rule bg-surface-well px-2 text-body text-primary placeholder:text-tertiary disabled:opacity-50"
        />
      </div>
      {error && (
        <span
          id={errorId}
          role="alert"
          className="pl-[6.75rem] text-meta text-state-danger"
        >
          {error}
        </span>
      )}
    </div>
  );
}
