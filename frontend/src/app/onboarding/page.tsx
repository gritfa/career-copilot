"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { handleApiError } from "@/lib/api-error";
import type { SessionInfo } from "@/lib/types";

/**
 * 首次建档（docs/05-ui-ux.md 第 4 节）。
 * 本页实现：①年龄与协议 ②模型服务商授权 ③完成。
 * 上传简历与事实确认在「简历工作台」（/resumes）完成；创建方案待后续开放。
 */

interface ProviderCard {
  key: "deepseek" | "qwen";
  name: string;
  receiver: string;
  noticeUrl: string;
  dataCategories: string;
  purpose: string;
  retention: string;
  withdraw: string;
}

const PROVIDERS: ProviderCard[] = [
  {
    key: "deepseek",
    name: "DeepSeek（深度求索）",
    receiver: "杭州深度求索人工智能基础技术研究有限公司（DeepSeek 开放平台 API）",
    noticeUrl:
      "https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html",
    dataCategories:
      "完整简历内容（个人概况、技能、工作与项目经历、教育背景）",
    purpose: "简历解析、岗位匹配分析、简历改写建议",
    retention:
      "数据将通过 API 发送至 DeepSeek 服务器处理，供应商可能按其隐私政策留存必要日志，存在第三方处理的固有风险",
    withdraw:
      "可随时在「设置 → 隐私」撤回。撤回后新的调用立即停止（不追溯已合法发生的调用），你仍可使用脱敏/本地规则的受限功能",
  },
  {
    key: "qwen",
    name: "通义千问（阿里云百炼）",
    receiver: "阿里云计算有限公司（百炼模型服务平台）",
    noticeUrl: "https://help.aliyun.com/zh/model-studio/privacy-notice",
    dataCategories:
      "完整简历内容（个人概况、技能、工作与项目经历、教育背景）",
    purpose:
      "简历解析、岗位匹配分析、简历改写建议，以及在 DeepSeek 不可用时的备用切换（备用切换同样以本授权为前提）",
    retention:
      "数据将通过 API 发送至阿里云百炼平台处理，供应商可能按其隐私说明留存必要日志，存在第三方处理的固有风险",
    withdraw:
      "可随时在「设置 → 隐私」撤回。撤回后新的调用立即停止（不追溯已合法发生的调用），你仍可使用脱敏/本地规则的受限功能",
  },
];

type ConsentState = "idle" | "loading" | "granted";

export default function OnboardingPage() {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [agreementDone, setAgreementDone] = useState(false);
  const [sessionNote, setSessionNote] = useState<string | null>(null);

  const [consentState, setConsentState] = useState<
    Record<string, ConsentState>
  >({ deepseek: "idle", qwen: "idle" });
  const [consentError, setConsentError] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const session = await api.get<SessionInfo>("/auth/session");
        if (cancelled) return;
        // 登录流程已强制年龄与协议确认；除非后端明确返回 false，视为已完成
        setAgreementDone(session?.age_confirmed !== false);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          handleApiError(err); // 跳转 /login
          return;
        }
        setSessionNote(
          "暂时无法获取会话状态，以下按登录时已完成年龄与协议确认展示。",
        );
        setAgreementDone(true);
      } finally {
        if (!cancelled) setSessionChecked(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function grantConsent(provider: ProviderCard["key"]) {
    setConsentError((prev) => ({ ...prev, [provider]: "" }));
    setConsentState((prev) => ({ ...prev, [provider]: "loading" }));
    try {
      // 契约要求：单一 provider + scope，不做批量勾选
      await api.post("/consents", { provider, scope: "full_resume" });
      setConsentState((prev) => ({ ...prev, [provider]: "granted" }));
    } catch (err) {
      setConsentState((prev) => ({ ...prev, [provider]: "idle" }));
      setConsentError((prev) => ({
        ...prev,
        [provider]: handleApiError(err),
      }));
    }
  }

  const grantedCount = PROVIDERS.filter(
    (p) => consentState[p.key] === "granted",
  ).length;

  return (
    <main className="page">
      <h1>首次建档</h1>
      <p className="muted">
        完成基础建档后，前往简历工作台上传简历并确认事实。
      </p>

      <ol className="steps">
        <li className={step === 1 ? "active" : "done"}>1. 年龄与协议</li>
        <li className={step === 2 ? "active" : step > 2 ? "done" : ""}>
          2. 模型服务商授权
        </li>
        <li className={step === 3 ? "active" : ""}>3. 完成</li>
        <li>4. 上传简历（在简历工作台完成）</li>
        <li>5. 确认事实（在简历工作台完成）</li>
        <li>6. 创建求职方案（待开放）</li>
      </ol>

      {step === 1 && (
        <section>
          <h2>年龄与协议</h2>
          {!sessionChecked ? (
            <p className="muted">正在检查确认状态…</p>
          ) : (
            <>
              {sessionNote && <p className="notice">{sessionNote}</p>}
              {agreementDone ? (
                <div className="card">
                  <p style={{ lineHeight: 1.7 }}>
                    ✓ 已完成：你在登录时已确认年满 18 周岁，并同意
                    <Link href="/legal/terms" className="link" target="_blank">
                      《用户协议》
                    </Link>
                    与
                    <Link
                      href="/legal/privacy"
                      className="link"
                      target="_blank"
                    >
                      《隐私政策》
                    </Link>
                    。
                  </p>
                </div>
              ) : (
                <div className="card">
                  <p style={{ lineHeight: 1.7 }}>
                    本服务仅面向 18 周岁以上用户。请返回
                    <Link href="/login" className="link">
                      登录页
                    </Link>
                    完成年龄与协议确认。
                  </p>
                </div>
              )}
              <button
                type="button"
                className="btn btn-primary"
                disabled={!agreementDone}
                onClick={() => setStep(2)}
              >
                下一步
              </button>
            </>
          )}
        </section>
      )}

      {step === 2 && (
        <section>
          <h2>模型服务商授权</h2>
          <p style={{ fontSize: 14, lineHeight: 1.7, margin: "8px 0" }}>
            上传简历不等同于授权第三方模型处理。若你希望使用完整的 AI
            解析与匹配能力，需要对下列服务商<strong>分别、主动</strong>
            授权（默认均不授权）。你也可以选择暂不授权，使用受限功能。
          </p>

          {PROVIDERS.map((p) => {
            const state = consentState[p.key];
            return (
              <div className="card" key={p.key}>
                <h3 style={{ fontSize: 16, marginBottom: 8 }}>{p.name}</h3>
                <dl style={{ fontSize: 14, lineHeight: 1.7 }}>
                  <dt style={{ fontWeight: "bold" }}>接收方</dt>
                  <dd style={{ marginBottom: 6 }}>
                    {p.receiver}（
                    <a
                      href={p.noticeUrl}
                      className="link"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      查看其隐私政策
                    </a>
                    ）
                  </dd>
                  <dt style={{ fontWeight: "bold" }}>数据类别</dt>
                  <dd style={{ marginBottom: 6 }}>{p.dataCategories}</dd>
                  <dt style={{ fontWeight: "bold" }}>处理目的</dt>
                  <dd style={{ marginBottom: 6 }}>{p.purpose}</dd>
                  <dt style={{ fontWeight: "bold" }}>保存与风险</dt>
                  <dd style={{ marginBottom: 6 }}>{p.retention}</dd>
                  <dt style={{ fontWeight: "bold" }}>撤回方式</dt>
                  <dd style={{ marginBottom: 6 }}>{p.withdraw}</dd>
                </dl>

                {consentError[p.key] && (
                  <p className="error-text">{consentError[p.key]}</p>
                )}

                {state === "granted" ? (
                  <p style={{ color: "#16a34a", fontSize: 14 }}>
                    ✓ 已授权。可随时在「设置 → 隐私」撤回。
                  </p>
                ) : (
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={state === "loading"}
                    onClick={() => grantConsent(p.key)}
                  >
                    {state === "loading"
                      ? "提交中…"
                      : `同意授权 ${p.name.split("（")[0]}`}
                  </button>
                )}
              </div>
            );
          })}

          <div
            style={{ display: "flex", gap: 12, marginTop: 16, flexWrap: "wrap" }}
          >
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setStep(3)}
              disabled={grantedCount === 0}
            >
              下一步
            </button>
            <button type="button" className="btn" onClick={() => setStep(3)}>
              暂不授权，使用受限功能
            </button>
          </div>
          <p className="muted" style={{ marginTop: 8 }}>
            不授权也可以继续：系统将使用脱敏内容或本地规则提供受限的基础功能，之后随时可在「设置
            → 隐私」中补充授权。
          </p>
        </section>
      )}

      {step === 3 && (
        <section>
          <h2>完成</h2>
          <div className="card">
            <p style={{ lineHeight: 1.8 }}>
              建档的当前阶段已完成：
              <br />
              ・年龄与协议确认：已完成
              <br />
              ・模型服务商授权：
              {grantedCount > 0
                ? `已授权 ${grantedCount} 个服务商`
                : "暂未授权（使用受限功能）"}
            </p>
          </div>
          <div className="card">
            <p style={{ lineHeight: 1.7 }}>
              下一步：前往简历工作台上传简历（PDF/DOCX），解析完成后逐条确认候选事实，建立你的事实库。创建求职方案功能待后续开放。
            </p>
          </div>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <Link href="/resumes" className="btn btn-primary">
              上传简历
            </Link>
            <Link href="/dashboard" className="btn">
              进入首页
            </Link>
          </div>
        </section>
      )}
    </main>
  );
}
