import type { ReactNode } from "react";
import { IconInbox } from "./icons";

/** 空态：图标 + 标题 + 描述 + 可选 CTA */
export default function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={[
        "flex flex-col items-center rounded-xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400">
        {icon ?? <IconInbox className="h-6 w-6" />}
      </div>
      <p className="text-base font-semibold text-slate-900">{title}</p>
      {description ? (
        <div className="mt-1.5 max-w-md text-sm text-slate-600">{description}</div>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}
