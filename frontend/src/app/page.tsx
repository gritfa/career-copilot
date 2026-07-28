import Link from "next/link";

const routes = [
  { href: "/login", label: "登录" },
  { href: "/onboarding", label: "建档" },
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
    <main style={{ padding: 32 }}>
      <h1>CareerCopilot</h1>
      <p>AI 求职助手脚手架，以下为占位页面导航。</p>
      <ul>
        {routes.map((r) => (
          <li key={r.href}>
            <Link href={r.href}>{r.label}</Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
