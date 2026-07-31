/** 加载骨架屏（脉冲动画的灰色占位块） */
export default function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={["animate-pulse rounded-lg bg-slate-200", className ?? "h-4 w-full"]
        .filter(Boolean)
        .join(" ")}
    />
  );
}

/** 推荐卡片形状的骨架（列表/首页加载态复用） */
export function CardSkeleton() {
  return (
    <div className="my-3 rounded-xl border border-slate-200 bg-white p-5 shadow-card">
      <div className="flex items-start justify-between gap-4">
        <Skeleton className="h-5 w-2/5" />
        <Skeleton className="h-5 w-20 rounded-full" />
      </div>
      <Skeleton className="mt-3 h-4 w-3/5" />
      <Skeleton className="mt-2 h-4 w-2/5" />
      <div className="mt-4 flex gap-2">
        <Skeleton className="h-8 w-24" />
        <Skeleton className="h-8 w-24" />
      </div>
    </div>
  );
}
