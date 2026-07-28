import Link from "next/link";

export const metadata = { title: "用户协议 - CareerCopilot" };

export default function TermsPage() {
  return (
    <main className="page">
      <p className="notice">
        草案，上线前需专业复核。本文为占位条款，不构成正式法律文件。
      </p>
      <h1>CareerCopilot 用户协议（草案）</h1>
      <p className="muted">版本：draft-2026-07 ・ 占位内容</p>

      <h2>一、服务说明</h2>
      <p style={{ lineHeight: 1.8 }}>
        CareerCopilot（以下简称「本服务」）是一款 AI
        求职助手，为用户提供简历解析、岗位推荐与简历定制等功能。本服务目前处于邀请制
        Beta 阶段，功能与条款可能调整。
      </p>

      <h2>二、使用资格</h2>
      <p style={{ lineHeight: 1.8 }}>
        本服务仅面向年满 18
        周岁、具有完全民事行为能力的自然人。注册即表示你确认满足前述条件。
      </p>

      <h2>三、用户义务</h2>
      <p style={{ lineHeight: 1.8 }}>
        你应保证上传的简历及其他资料真实、合法，且不侵犯任何第三方权利；不得利用本服务从事任何违法活动，不得干扰服务的正常运行。
      </p>

      <h2>四、服务内容与免责</h2>
      <p style={{ lineHeight: 1.8 }}>
        岗位推荐与匹配分析仅供参考，不构成任何录用承诺或职业建议。本服务不保证岗位信息的完整性与时效性，请以招聘方官方信息为准。
      </p>

      <h2>五、个人信息保护</h2>
      <p style={{ lineHeight: 1.8 }}>
        个人信息的处理规则见
        <Link href="/legal/privacy" className="link">
          《隐私政策》
        </Link>
        。向第三方模型服务商传输简历内容需要你的单独授权，且可随时撤回。
      </p>

      <h2>六、协议变更与终止</h2>
      <p style={{ lineHeight: 1.8 }}>
        条款如有重大变更将提前通知。你可随时申请注销账号；注销进入 7
        天恢复期，恢复期结束后数据将被删除。
      </p>

      <p className="muted" style={{ marginTop: 24 }}>
        （以上为占位草案，正式内容待专业法律复核后发布。）
      </p>
    </main>
  );
}
