import type { HTMLAttributes, ReactNode } from "react";

/** 统一卡片：白底 + slate-200 边框 + 极轻阴影 + rounded-xl */
export default function Card({
  className,
  children,
  hover = false,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { children: ReactNode; hover?: boolean }) {
  return (
    <div
      className={[
        "rounded-xl border border-slate-200 bg-white p-5 shadow-card",
        hover ? "transition-shadow hover:shadow-md hover:border-slate-300" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    >
      {children}
    </div>
  );
}
