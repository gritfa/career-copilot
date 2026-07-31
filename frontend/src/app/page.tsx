import Link from "next/link";
import { buttonClasses } from "@/components/ui/Button";
import { IconArrowRight, IconLogo } from "@/components/ui/icons";

const routes = [
  { href: "/dashboard", label: "首页概览" },
  { href: "/plans", label: "求职方案" },
  { href: "/recommendations", label: "每日推荐" },
  { href: "/resumes", label: "简历工作台" },
  { href: "/notifications", label: "站内通知" },
  { href: "/settings/account", label: "账号设置" },
  { href: "/settings/privacy", label: "隐私设置" },
];

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-b from-indigo-50 via-slate-50 to-slate-50 px-4 py-16">
      <div className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 shadow-card">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-white">
            <IconLogo className="h-6 w-6" />
          </span>
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">
              CareerCopilot
            </h1>
            <p className="text-sm text-slate-600">
              AI 求职助手：方案、推荐与简历工作台
            </p>
          </div>
        </div>

        <div className="mt-6 flex gap-2">
          <Link href="/login" className={buttonClasses("primary", "md", "flex-1")}>
            登录
            <IconArrowRight className="h-4 w-4" />
          </Link>
          <Link href="/onboarding" className={buttonClasses("secondary", "md", "flex-1")}>
            建档
          </Link>
        </div>

        <p className="mt-6 mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
          功能入口
        </p>
        <ul className="divide-y divide-slate-100">
          {routes.map((r) => (
            <li key={r.href}>
              <Link
                href={r.href}
                className="flex items-center justify-between py-2 text-sm text-slate-700 transition-colors hover:text-primary"
              >
                {r.label}
                <IconArrowRight className="h-3.5 w-3.5 text-slate-300" />
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </main>
  );
}
