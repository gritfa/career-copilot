import type { HTMLAttributes, ReactNode } from "react";

/**
 * 统一徽标。tone 对应语义色：
 * neutral（默认灰）/ success（emerald）/ warning（amber）/ danger（red）/ info（indigo）
 */

export type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info";

const TONES: Record<BadgeTone, string> = {
  neutral: "border-slate-200 bg-slate-100 text-slate-700",
  success: "border-emerald-200 bg-success-soft text-emerald-700",
  warning: "border-amber-200 bg-warning-soft text-amber-700",
  danger: "border-red-200 bg-danger-soft text-red-700",
  info: "border-indigo-200 bg-primary-soft text-primary",
};

export default function Badge({
  tone = "neutral",
  className,
  children,
  ...rest
}: HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone; children: ReactNode }) {
  return (
    <span
      className={[
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium leading-normal",
        TONES[tone],
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    >
      {children}
    </span>
  );
}

/**
 * 「合成示例」徽标（阶段 11 P1 合规硬要求，列表卡片与详情页复用）。
 * 用醒目的 warning 色 + 虚线边框与普通标签区分。
 */
export function SyntheticBadge({ className }: { className?: string }) {
  return (
    <Badge
      tone="warning"
      className={["border-dashed border-amber-400", className].filter(Boolean).join(" ")}
      title="该岗位为合成种子数据，仅用于产品演示，非真实在招岗位"
    >
      合成示例
    </Badge>
  );
}
