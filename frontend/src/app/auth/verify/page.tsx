import { Suspense } from "react";
import VerifyClient from "./verify-client";

export default function VerifyPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-gradient-to-b from-indigo-50 via-slate-50 to-slate-50 px-4 py-12">
      <div className="w-full max-w-md overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
        <div className="h-1.5 bg-gradient-to-r from-indigo-500 via-primary to-violet-500" />
        <div className="p-8">
          <Suspense fallback={<p className="text-sm text-slate-600">正在验证登录链接…</p>}>
            <VerifyClient />
          </Suspense>
        </div>
      </div>
    </main>
  );
}
