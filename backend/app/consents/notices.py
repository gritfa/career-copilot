"""版本化告知文案（占位）。

【重要】以下全部为工程占位文案，上线前必须由具备资质的专业人员复核
（个人信息保护法、供应商最新隐私政策与合同条款）。
"""

REVIEW_DISCLAIMER = "【占位文案：上线前需由具备资质的专业人员复核，不构成法律意见】"

TERMS_VERSION = "terms-2026-07-28.1"
PRIVACY_VERSION = "privacy-2026-07-28.1"

# 各 provider 告知版本（授权时必须携带匹配版本；文案或范围变化需提升版本并重新授权）
PROVIDER_NOTICE_VERSIONS: dict[str, str] = {
    "deepseek": "deepseek-2026-07-28.1",
    "qwen": "qwen-2026-07-28.1",
    "analytics": "analytics-2026-07-28.1",
    "support": "support-2026-07-28.1",
}

_SCOPE_DESCRIPTIONS = {
    "full_resume": "完整简历内容（含个人经历原文）",
    "deidentified": "脱敏后的简历内容（移除联系方式、姓名等直接标识）",
    "profile_fields": "结构化画像字段（技能、年限等，经用户确认）",
}

PROVIDER_NOTICES: dict[str, dict] = {
    "deepseek": {
        "provider": "deepseek",
        "notice_version": PROVIDER_NOTICE_VERSIONS["deepseek"],
        "receiver": "杭州深度求索人工智能基础技术研究有限公司（DeepSeek）",
        "policy_url": "https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html",
        "scopes": _SCOPE_DESCRIPTIONS,
        "purposes": ["简历解析", "岗位匹配分析", "简历改写建议"],
        "risks": "数据将传输至第三方模型服务商处理，供应商可能按其政策留存日志。",
        "revoke_path": "可随时在“隐私与授权”页面撤回；撤回后新调用立即阻断，不追溯已发生调用。",
        "fallback": "拒绝授权时可使用规则 + 向量基础匹配的降级路径。",
        "default_checked": False,
        "disclaimer": REVIEW_DISCLAIMER,
    },
    "qwen": {
        "provider": "qwen",
        "notice_version": PROVIDER_NOTICE_VERSIONS["qwen"],
        "receiver": "阿里云计算有限公司（通义千问 / 百炼平台）",
        "policy_url": "https://help.aliyun.com/zh/model-studio/privacy-notice",
        "scopes": _SCOPE_DESCRIPTIONS,
        "purposes": ["备用模型切换", "简历解析", "岗位匹配分析"],
        "risks": "作为备用服务商时同样需要单独授权；备用切换不是授权例外。",
        "revoke_path": "可随时在“隐私与授权”页面撤回；撤回后新调用立即阻断。",
        "fallback": "拒绝授权时该服务商不可用，不影响 DeepSeek 的独立授权。",
        "default_checked": False,
        "disclaimer": REVIEW_DISCLAIMER,
    },
    "analytics": {
        "provider": "analytics",
        "notice_version": PROVIDER_NOTICE_VERSIONS["analytics"],
        "receiver": "CareerCopilot 匿名产品分析",
        "policy_url": None,
        "scopes": {"profile_fields": "匿名化产品事件（不含正文与联系方式）"},
        "purposes": ["产品改进", "推荐质量评估"],
        "risks": "事件使用不可逆或轮换标识，聚合后不可合理回溯到个人。",
        "revoke_path": "可随时撤回；撤回后停止收集可关联事件。",
        "fallback": "拒绝不影响核心功能。",
        "default_checked": False,
        "disclaimer": REVIEW_DISCLAIMER,
    },
    "support": {
        "provider": "support",
        "notice_version": PROVIDER_NOTICE_VERSIONS["support"],
        "receiver": "CareerCopilot 支持人员（限时、单份资源）",
        "policy_url": None,
        "scopes": {"full_resume": "用户主动发起支持请求时指定的单份简历"},
        "purposes": ["问题排查支持"],
        "risks": "仅限用户主动发起且限时；管理员默认无法查看简历正文。",
        "revoke_path": "可随时撤回，过期自动失效。",
        "fallback": "拒绝时支持人员仅能基于元数据协助。",
        "default_checked": False,
        "disclaimer": REVIEW_DISCLAIMER,
    },
}


def notices_payload() -> dict:
    """GET /consents/notices 响应体。"""
    return {
        "terms_version": TERMS_VERSION,
        "privacy_version": PRIVACY_VERSION,
        "disclaimer": REVIEW_DISCLAIMER,
        "providers": PROVIDER_NOTICES,
    }
