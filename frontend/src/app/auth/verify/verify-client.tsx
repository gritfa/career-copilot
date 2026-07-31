"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import type { VerifyResponse } from "@/lib/types";
import { buttonClasses } from "@/components/ui/Button";

type State = "verifying" | "success" | "failed";

export default function VerifyClient() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get("token");

  // 没有 token 直接进入失败态（惰性初始化，避免在 effect 里同步 setState）
  const [state, setState] = useState<State>(() =>
    token ? "verifying" : "failed",
  );
  // magic link token 单次有效；防止 React 严格模式下 effect 双跑重复消费
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current || !token) return;
    startedRef.current = true;

    let cancelled = false;
    (async () => {
      try {
        const res = await api.post<VerifyResponse>("/auth/magic-links/verify", {
          token,
        });
        if (cancelled) return;
        setState("success");
        const onboarded =
          res?.onboarding_completed ?? res?.user?.onboarding_completed ?? false;
        router.replace(onboarded ? "/dashboard" : "/onboarding");
      } catch {
        if (!cancelled) setState("failed");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [token, router]);

  if (state === "verifying") {
    return <p className="text-sm text-slate-600">正在验证登录链接…</p>;
  }

  if (state === "success") {
    return <p className="text-sm text-slate-600">验证成功，正在跳转…</p>;
  }

  return (
    <div>
      <h1 className="text-2xl font-bold tracking-tight text-slate-900">
        链接已失效，请重新获取
      </h1>
      <p className="mt-3 mb-6 text-sm leading-relaxed text-slate-600">
        登录链接短时有效且只能使用一次。请返回登录页重新发送。
      </p>
      <Link href="/login" className={buttonClasses("primary", "md", "w-full")}>
        返回登录页
      </Link>
    </div>
  );
}
