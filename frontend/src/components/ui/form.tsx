import type {
  InputHTMLAttributes,
  LabelHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

/** 统一表单控件：Label / Input / Select / Textarea / Field 组合 */

const CONTROL =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 " +
  "placeholder:text-slate-400 transition-colors " +
  "focus:outline-none focus:border-primary focus:ring-3 focus:ring-indigo-100 " +
  "disabled:opacity-50 disabled:cursor-not-allowed";

export function Label({
  className,
  children,
  ...rest
}: LabelHTMLAttributes<HTMLLabelElement> & { children: ReactNode }) {
  return (
    <label
      className={["block text-sm font-medium text-slate-700", className]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    >
      {children}
    </label>
  );
}

export function Input({
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input className={[CONTROL, className].filter(Boolean).join(" ")} {...rest} />
  );
}

export function Select({
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  return (
    <select className={[CONTROL, className].filter(Boolean).join(" ")} {...rest}>
      {children}
    </select>
  );
}

export function Textarea({
  className,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={[CONTROL, className].filter(Boolean).join(" ")}
      {...rest}
    />
  );
}

/** Label + 控件的纵向组合（label 文案 + 任意控件） */
export function Field({
  label,
  className,
  children,
}: {
  label: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label className={["block", className].filter(Boolean).join(" ")}>
      <span className="mb-1.5 block text-sm font-medium text-slate-700">
        {label}
      </span>
      {children}
    </label>
  );
}
