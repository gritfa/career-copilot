"use client";

import type { ReactNode } from "react";
import Button from "@/components/ui/Button";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** 高风险操作的二次确认弹层（撤回授权、撤销会话、注销账号等） */
export default function ConfirmDialog({
  open,
  title,
  children,
  confirmText = "确认",
  cancelText = "取消",
  danger = false,
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-5"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-6 shadow-xl">
        <h3 className="mb-2 text-base font-semibold text-slate-900">{title}</h3>
        <div className="text-sm leading-relaxed text-slate-700">{children}</div>
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={onCancel} disabled={loading}>
            {cancelText}
          </Button>
          <Button
            variant={danger ? "danger" : "primary"}
            onClick={onConfirm}
            loading={loading}
          >
            {loading ? "处理中…" : confirmText}
          </Button>
        </div>
      </div>
    </div>
  );
}
