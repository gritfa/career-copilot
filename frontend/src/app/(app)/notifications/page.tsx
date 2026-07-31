import EmptyState from "@/components/ui/EmptyState";
import PageHeader from "@/components/ui/PageHeader";
import { IconBell } from "@/components/ui/icons";

export default function NotificationsPage() {
  return (
    <main className="page">
      <PageHeader
        title="站内通知"
        description="系统与任务相关的站内通知列表。"
      />
      <EmptyState
        icon={<IconBell className="h-6 w-6" />}
        title="暂无通知"
        description="站内通知功能待实现，简历解析、分析完成等事件的提醒将出现在这里。（占位页面，功能待实现）"
      />
    </main>
  );
}
