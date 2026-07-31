import AppShell from "@/components/ui/AppShell";

/**
 * 登录后区域的统一布局（路由组，不改变 URL）：
 * 左侧固定侧边栏 + 右侧内容区，移动端折叠为顶栏 + 抽屉。
 */
export default function AppAreaLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <AppShell>{children}</AppShell>;
}
