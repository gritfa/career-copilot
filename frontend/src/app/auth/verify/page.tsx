import { Suspense } from "react";
import VerifyClient from "./verify-client";

export default function VerifyPage() {
  return (
    <main className="page">
      <Suspense fallback={<p>正在验证登录链接…</p>}>
        <VerifyClient />
      </Suspense>
    </main>
  );
}
