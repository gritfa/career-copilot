"use client";

import { useState } from "react";
import {
  type SearchPlan,
  PLAN_CITIES,
  PLAN_DIRECTIONS,
  WORK_MODES,
} from "@/lib/types";

/**
 * 求职方案表单（/plans/new 与 /plans/{id} 共用）。
 * 契约见 docs/04-api.md 第 5 节、docs/03-data-model.md 第 5 节：
 * role_family、city_codes[]、work_modes[]、minimum/target_monthly_salary（CNY）、
 * salary_months_preference、minimum_match_score（默认 65）、allow_outsourcing（默认 false）。
 */

/** POST /search-plans 与 PATCH /search-plans/{id} 的请求体 */
export interface PlanFormPayload {
  name: string;
  role_family: string;
  city_codes: string[];
  work_modes: string[];
  minimum_monthly_salary: number;
  target_monthly_salary: number;
  currency: "CNY";
  salary_months_preference: number | null;
  minimum_match_score: number;
  allow_outsourcing: boolean;
}

interface PlanFormProps {
  initial?: SearchPlan | null;
  submitLabel: string;
  submitting: boolean;
  /** 提交失败的文案由父组件传入（保持请求逻辑在页面层） */
  submitError?: string | null;
  onSubmit: (payload: PlanFormPayload) => void;
}

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export default function PlanForm({
  initial,
  submitLabel,
  submitting,
  submitError,
  onSubmit,
}: PlanFormProps) {
  const [name, setName] = useState(initial?.name ?? "");
  const [direction, setDirection] = useState(
    initial?.role_family ?? initial?.direction ?? "",
  );
  const [cities, setCities] = useState<string[]>(
    initial?.city_codes ?? initial?.cities ?? [],
  );
  const [workModes, setWorkModes] = useState<string[]>(initial?.work_modes ?? []);
  const [minSalary, setMinSalary] = useState(
    initial?.minimum_monthly_salary != null ? String(initial.minimum_monthly_salary) : "",
  );
  const [targetSalary, setTargetSalary] = useState(
    initial?.target_monthly_salary != null ? String(initial.target_monthly_salary) : "",
  );
  const [salaryMonths, setSalaryMonths] = useState(
    initial?.salary_months_preference != null
      ? String(initial.salary_months_preference)
      : "",
  );
  const [minScore, setMinScore] = useState(
    initial?.minimum_match_score != null ? String(initial.minimum_match_score) : "65",
  );
  const [allowOutsourcing, setAllowOutsourcing] = useState(
    initial?.allow_outsourcing ?? false,
  );
  const [formError, setFormError] = useState<string | null>(null);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;

    if (!direction) {
      setFormError("请选择求职方向");
      return;
    }
    if (cities.length === 0) {
      setFormError("请至少选择一个城市");
      return;
    }
    if (workModes.length === 0) {
      setFormError("请至少选择一种办公方式");
      return;
    }
    const min = Number(minSalary);
    const target = Number(targetSalary);
    if (!minSalary || !Number.isFinite(min) || min <= 0) {
      setFormError("请填写有效的最低月薪（正数，单位元）");
      return;
    }
    if (!targetSalary || !Number.isFinite(target) || target <= 0) {
      setFormError("请填写有效的目标月薪（正数，单位元）");
      return;
    }
    if (min > target) {
      setFormError("最低月薪不能高于目标月薪");
      return;
    }
    let months: number | null = null;
    if (salaryMonths.trim()) {
      months = Number(salaryMonths);
      if (!Number.isInteger(months) || months < 12 || months > 24) {
        setFormError("薪数偏好请填写 12 ~ 24 之间的整数（如 13 表示 13 薪），或留空");
        return;
      }
    }
    const score = Number(minScore);
    if (!minScore || !Number.isInteger(score) || score < 0 || score > 100) {
      setFormError("最低匹配分请填写 0 ~ 100 之间的整数");
      return;
    }

    setFormError(null);
    onSubmit({
      name:
        name.trim() ||
        (PLAN_DIRECTIONS.find((d) => d.value === direction)?.label ?? "求职方案"),
      role_family: direction,
      city_codes: cities,
      work_modes: workModes,
      minimum_monthly_salary: min,
      target_monthly_salary: target,
      currency: "CNY",
      salary_months_preference: months,
      minimum_match_score: score,
      allow_outsourcing: allowOutsourcing,
    });
  }

  return (
    <form onSubmit={handleSubmit}>
      <label className="field">
        <span>方案名称（选填，默认使用方向名）</span>
        <input
          className="input"
          value={name}
          maxLength={50}
          onChange={(e) => setName(e.target.value)}
          placeholder="例如：杭州 Python 后端"
        />
      </label>

      <div className="field">
        <span>求职方向（6 选 1）*</span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px" }}>
          {PLAN_DIRECTIONS.map((d) => (
            <label key={d.value} style={{ fontSize: 14, cursor: "pointer" }}>
              <input
                type="radio"
                name="direction"
                checked={direction === d.value}
                onChange={() => setDirection(d.value)}
              />{" "}
              {d.label}
            </label>
          ))}
        </div>
      </div>

      <div className="field">
        <span>城市（可多选）*</span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px" }}>
          {PLAN_CITIES.map((c) => (
            <label key={c.value} style={{ fontSize: 14, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={cities.includes(c.value)}
                onChange={() => setCities((prev) => toggle(prev, c.value))}
              />{" "}
              {c.label}
            </label>
          ))}
        </div>
      </div>

      <div className="field">
        <span>办公方式（可多选）*</span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px" }}>
          {WORK_MODES.map((m) => (
            <label key={m.value} style={{ fontSize: 14, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={workModes.includes(m.value)}
                onChange={() => setWorkModes((prev) => toggle(prev, m.value))}
              />{" "}
              {m.label}
            </label>
          ))}
        </div>
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <label className="field" style={{ flex: "1 1 180px" }}>
          <span>最低月薪（元，CNY）*</span>
          <input
            className="input"
            type="number"
            min={1}
            inputMode="numeric"
            value={minSalary}
            onChange={(e) => setMinSalary(e.target.value)}
            placeholder="例如 15000"
          />
        </label>
        <label className="field" style={{ flex: "1 1 180px" }}>
          <span>目标月薪（元，CNY）*</span>
          <input
            className="input"
            type="number"
            min={1}
            inputMode="numeric"
            value={targetSalary}
            onChange={(e) => setTargetSalary(e.target.value)}
            placeholder="例如 22000"
          />
        </label>
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <label className="field" style={{ flex: "1 1 180px" }}>
          <span>薪数偏好（选填，如 13 表示 13 薪）</span>
          <input
            className="input"
            type="number"
            min={12}
            max={24}
            inputMode="numeric"
            value={salaryMonths}
            onChange={(e) => setSalaryMonths(e.target.value)}
            placeholder="例如 13"
          />
        </label>
        <label className="field" style={{ flex: "1 1 180px" }}>
          <span>最低匹配分（0 ~ 100，默认 65）</span>
          <input
            className="input"
            type="number"
            min={0}
            max={100}
            inputMode="numeric"
            value={minScore}
            onChange={(e) => setMinScore(e.target.value)}
          />
        </label>
      </div>

      <div className="field">
        <label style={{ fontSize: 14, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={allowOutsourcing}
            onChange={(e) => setAllowOutsourcing(e.target.checked)}
          />{" "}
          接受外包 / 派遣 / 驻场岗位
        </label>
        <p className="muted" style={{ marginTop: 4 }}>
          默认排除外包、派遣和驻场类岗位；开启后此类岗位才会进入推荐，并保留风险提示。
        </p>
      </div>

      {formError ? <p className="error-text">{formError}</p> : null}
      {submitError ? <p className="error-text">{submitError}</p> : null}

      <button type="submit" className="btn btn-primary" disabled={submitting}>
        {submitting ? "保存中…" : submitLabel}
      </button>
    </form>
  );
}
