"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import PlanForm, { type PlanFormPayload } from "@/components/PlanForm";
import type { SearchPlan } from "@/lib/types";

/** 新建求职方案：POST /search-plans（docs/04-api.md 第 5 节） */
export default function NewPlanPage() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  async function handleSubmit(payload: PlanFormPayload) {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await api.post<SearchPlan>("/search-plans", payload);
      // 创建成功后进入详情页继续配置公司偏好；拿不到 id 时回列表
      if (created?.id) {
        router.push(`/plans/${encodeURIComponent(created.id)}`);
      } else {
        router.push("/plans");
      }
    } catch (err) {
      setSubmitError(handleApiError(err));
      setSubmitting(false);
    }
  }

  return (
    <main className="page">
      <p>
        <Link href="/plans" className="link muted">
          ← 返回方案列表
        </Link>
      </p>
      <h1>新建求职方案</h1>
      <p className="muted">
        薪资单位为人民币（CNY）月薪；外包 / 派遣 / 驻场岗位默认排除。
        保存后可在方案详情里继续配置公司偏好并激活。
      </p>
      <div className="card">
        <PlanForm
          submitLabel="保存方案"
          submitting={submitting}
          submitError={submitError}
          onSubmit={handleSubmit}
        />
      </div>
    </main>
  );
}
