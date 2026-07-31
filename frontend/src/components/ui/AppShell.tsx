"use client";

import { useState, type ComponentType, type SVGProps } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";
import {
  IconBell,
  IconDatabase,
  IconFileText,
  IconHome,
  IconImport,
  IconLogo,
  IconLogOut,
  IconMenu,
  IconShield,
  IconSparkles,
  IconTarget,
  IconUser,
  IconWrench,
  IconX,
} from "./icons";

/**
 * 登录后统一 App Shell：
 * - 桌面：左侧固定侧边栏（w-60：logo + 分组导航 + 底部登出），右侧内容区
 * - 移动：顶栏 + 抽屉式侧边栏（遮罩点击关闭）
 * 只是布局壳，不做鉴权拦截（各页面 401 仍由 handleApiError 统一跳登录）。
 */

type NavIcon = ComponentType<SVGProps<SVGSVGElement>>;

interface NavItem {
  href: string;
  label: string;
  icon: NavIcon;
  /** 激活判断是否包含子路径（如 /plans/xxx 高亮「求职方案」） */
  matchPrefix?: boolean;
}

const MAIN_NAV: NavItem[] = [
  { href: "/dashboard", label: "首页概览", icon: IconHome },
  { href: "/recommendations", label: "每日推荐", icon: IconSparkles, matchPrefix: true },
  { href: "/plans", label: "求职方案", icon: IconTarget, matchPrefix: true },
  { href: "/resumes", label: "简历工作台", icon: IconFileText, matchPrefix: true },
  { href: "/profile/facts", label: "事实库", icon: IconDatabase },
  { href: "/jobs/import", label: "导入岗位", icon: IconImport },
  { href: "/notifications", label: "站内通知", icon: IconBell },
];

const SETTINGS_NAV: NavItem[] = [
  { href: "/settings/account", label: "账号设置", icon: IconUser },
  { href: "/settings/privacy", label: "隐私设置", icon: IconShield },
  { href: "/admin", label: "管理后台", icon: IconWrench },
];

function isActive(pathname: string, item: NavItem): boolean {
  if (pathname === item.href) return true;
  if (item.matchPrefix && pathname.startsWith(`${item.href}/`)) return true;
  // 简历版本/事实确认属于「简历工作台」域
  if (item.href === "/resumes" && pathname.startsWith("/resume-versions")) return true;
  return false;
}

function NavLink({
  item,
  pathname,
  onNavigate,
}: {
  item: NavItem;
  pathname: string;
  onNavigate?: () => void;
}) {
  const active = isActive(pathname, item);
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={[
        "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
        active
          ? "bg-primary-soft text-primary"
          : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
      ].join(" ")}
    >
      <Icon className={`h-4 w-4 shrink-0 ${active ? "text-primary" : "text-slate-400"}`} />
      {item.label}
    </Link>
  );
}

function Brand() {
  return (
    <Link href="/dashboard" className="flex items-center gap-2.5 px-3">
      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-white">
        <IconLogo className="h-5 w-5" />
      </span>
      <span className="text-base font-bold tracking-tight text-slate-900">
        CareerCopilot
      </span>
    </Link>
  );
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname() ?? "";
  const [loggingOut, setLoggingOut] = useState(false);

  async function logout() {
    setLoggingOut(true);
    try {
      await api.delete("/auth/session");
    } catch {
      // 会话可能已失效，忽略错误直接回登录页
    }
    window.location.assign("/login");
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center border-b border-slate-200 px-3">
        <Brand />
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-4">
        <div className="space-y-1">
          {MAIN_NAV.map((item) => (
            <NavLink key={item.href} item={item} pathname={pathname} onNavigate={onNavigate} />
          ))}
        </div>
        <p className="mt-6 mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-slate-400">
          设置
        </p>
        <div className="space-y-1">
          {SETTINGS_NAV.map((item) => (
            <NavLink key={item.href} item={item} pathname={pathname} onNavigate={onNavigate} />
          ))}
        </div>
      </nav>

      <div className="border-t border-slate-200 p-3">
        <button
          type="button"
          onClick={logout}
          disabled={loggingOut}
          className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 disabled:opacity-50"
        >
          <IconLogOut className="h-4 w-4 shrink-0 text-slate-400" />
          {loggingOut ? "退出中…" : "退出登录"}
        </button>
      </div>
    </div>
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <div className="flex min-h-screen w-full">
      {/* 桌面固定侧边栏 */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 border-r border-slate-200 bg-white lg:block">
        <SidebarContent />
      </aside>

      {/* 移动端抽屉 */}
      {drawerOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 bg-slate-900/50"
            onClick={() => setDrawerOpen(false)}
            aria-hidden="true"
          />
          <div className="absolute inset-y-0 left-0 w-60 bg-white shadow-xl">
            <button
              type="button"
              className="absolute right-3 top-4 rounded-lg p-1.5 text-slate-500 hover:bg-slate-100"
              onClick={() => setDrawerOpen(false)}
              aria-label="关闭导航"
            >
              <IconX className="h-5 w-5" />
            </button>
            <SidebarContent onNavigate={() => setDrawerOpen(false)} />
          </div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col lg:pl-60">
        {/* 移动端顶栏 */}
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-slate-200 bg-white/90 px-4 backdrop-blur lg:hidden">
          <button
            type="button"
            className="rounded-lg p-1.5 text-slate-600 hover:bg-slate-100"
            onClick={() => setDrawerOpen(true)}
            aria-label="打开导航"
          >
            <IconMenu className="h-5 w-5" />
          </button>
          <Brand />
        </header>

        <div className="flex-1">{children}</div>
      </div>
    </div>
  );
}
