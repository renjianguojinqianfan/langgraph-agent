import { useId } from "react";

interface FormFieldProps {
  label: string;
  name: string;
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
  autoFocus?: boolean;
}

/** Labelled text input for forms (design §6: every control has a name). */
export function FormField({
  label,
  name,
  value,
  placeholder,
  onChange,
  autoFocus = false,
}: FormFieldProps) {
  const id = useId();
  return (
    <div className="flex items-center gap-3">
      <label
        htmlFor={id}
        className="w-24 shrink-0 text-meta text-secondary"
      >
        {label}
      </label>
      <input
        id={id}
        name={name}
        type="text"
        autoComplete="off"
        autoFocus={autoFocus}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 min-w-0 flex-1 rounded-control border border-rule bg-surface-well px-2 text-body text-primary placeholder:text-tertiary"
      />
    </div>
  );
}
