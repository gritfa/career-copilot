import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CareerCopilot",
  description: "AI 求职助手：方案、推荐与简历工作台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
