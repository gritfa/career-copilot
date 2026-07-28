import Link from "next/link";

export const metadata = { title: "隐私政策 - CareerCopilot" };

export default function PrivacyPolicyPage() {
  return (
    <main className="page">
      <p className="notice">
        草案，上线前需专业复核。本文为占位条款，不构成正式法律文件。
      </p>
      <h1>CareerCopilot 隐私政策（草案）</h1>
      <p className="muted">版本：draft-2026-07 ・ 占位内容</p>

      <h2>一、我们收集哪些信息</h2>
      <p style={{ lineHeight: 1.8 }}>
        账号信息（邮箱）、你主动上传的简历文件及其结构化内容、求职偏好、对推荐结果的反馈，以及保障服务安全所必需的日志信息。
      </p>

      <h2>二、我们如何使用信息</h2>
      <p style={{ lineHeight: 1.8 }}>
        仅用于简历解析、岗位匹配与推荐、简历定制建议、账号与安全管理等本服务的直接目的。我们遵循合法、正当、必要与最小范围原则。
      </p>

      <h2>三、第三方模型服务商</h2>
      <p style={{ lineHeight: 1.8 }}>
        在获得你的<strong>单独授权</strong>后，简历内容才会发送至 DeepSeek
        或通义千问（阿里云百炼）等模型服务商用于解析与匹配。授权默认关闭、逐个开启，可随时在「设置
        → 隐私」撤回；撤回后新的调用立即停止。拒绝授权仅影响相关 AI
        功能，你仍可使用脱敏/本地规则的受限功能。
      </p>

      <h2>四、存储与安全</h2>
      <p style={{ lineHeight: 1.8 }}>
        简历文件加密存储于私有对象存储，访问采用短时签名链接；日志中不记录简历正文、联系方式或凭据信息。
      </p>

      <h2>五、你的权利</h2>
      <p style={{ lineHeight: 1.8 }}>
        你有权访问、更正、删除个人信息，撤回授权，导出数据副本，以及注销账号。注销进入
        7 天恢复期，到期后数据被物理清理。相关入口见「设置 →
        账号」与「设置 → 隐私」。
      </p>

      <h2>六、联系我们</h2>
      <p style={{ lineHeight: 1.8 }}>
        如对本政策有疑问或需行使上述权利，可通过站内反馈或服务邮箱联系我们（占位）。
      </p>

      <p className="muted" style={{ marginTop: 24 }}>
        另见
        <Link href="/legal/terms" className="link">
          《用户协议》
        </Link>
        。（以上为占位草案，正式内容待专业法律复核后发布。）
      </p>
    </main>
  );
}
