import type { ButtonHTMLAttributes, ReactNode } from "react";

/**
 * 统一按钮（primary / secondary / ghost / danger + loading 态）。
 * 服务端/客户端组件均可使用；需要 <Link> 外观时用 buttonClasses()。
 */

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

const BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium " +
  "transition-colors cursor-pointer select-none " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 " +
  "disabled:opacity-50 disabled:cursor-not-allowed";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-primary text-white border border-primary " +
    "hover:bg-primary-hover hover:border-primary-hover",
  secondary:
    "bg-white text-slate-700 border border-slate-200 shadow-xs " +
    "hover:bg-slate-50 hover:border-slate-300",
  ghost:
    "bg-transparent text-slate-600 border border-transparent " +
    "hover:bg-slate-100 hover:text-slate-900",
  danger:
    "bg-white text-danger border border-red-200 " +
    "hover:bg-danger-soft hover:border-red-300",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "px-3 py-1.5 text-sm",
  md: "px-4 py-2 text-sm",
};

/** 组合按钮外观 class（供 <Link>、<a> 复用同一套样式） */
export function buttonClasses(
  variant: ButtonVariant = "secondary",
  size: ButtonSize = "md",
  extra?: string,
): string {
  return [BASE, VARIANTS[variant], SIZES[size], extra].filter(Boolean).join(" ");
}

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z"
      />
    </svg>
  );
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** loading 时自动禁用并显示转圈 */
  loading?: boolean;
  children: ReactNode;
}

export default function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  disabled,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={buttonClasses(variant, size, className)}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}
