"""BOSS 直聘降级策略（docs/06 第 10 节）：link_out / import_only。

没有经确认允许的自动访问方式 → 不实现后台爬虫、不登录、不绕过任何限制。
只提供：按用户方案生成原平台搜索入口 URL 的纯函数 + 用户粘贴导入路径。
管理后台/能力矩阵中该来源必须显示 import_only，不得显示绿色“采集正常”。
"""

from urllib.parse import urlencode

from app.jobs.constants import ROLE_FAMILY_LABELS, SOURCE_KEY_BOSS

BOSS_BASE_URL = "https://www.zhipin.com"

# BOSS 站内城市编码（公开搜索页 URL 参数，仅用于生成跳转链接）
_BOSS_CITY_PARAM_BY_CODE: dict[str, str] = {
    "110100": "101010100",  # 北京
    "310100": "101020100",  # 上海
    "440300": "101280600",  # 深圳
    "330100": "101210100",  # 杭州
    "440100": "101280100",  # 广州
    "510100": "101270100",  # 成都
}

# 搜索关键词：用中文岗位方向名，贴近平台真实搜索习惯
_QUERY_BY_ROLE_FAMILY: dict[str, str] = {
    "ai_application": "大模型应用工程师",
    "backend_python": "Python后端开发",
    "backend_java": "Java后端开发",
    "frontend": "前端开发",
    "data": "数据开发",
    "qa": "测试开发",
}


def build_boss_search_url(role_family: str, city_code: str) -> str:
    """纯函数：按方向 + 城市生成 BOSS 公开搜索入口 URL（link-out，不抓取）。"""
    query = _QUERY_BY_ROLE_FAMILY.get(role_family, ROLE_FAMILY_LABELS.get(role_family, ""))
    params: dict[str, str] = {"query": query}
    city_param = _BOSS_CITY_PARAM_BY_CODE.get(city_code)
    if city_param:
        params["city"] = city_param
    return f"{BOSS_BASE_URL}/web/geek/job?{urlencode(params)}"


def build_plan_search_urls(role_family: str, city_codes: list[str]) -> list[dict[str, str]]:
    """为一个求职方案的每个城市生成搜索入口（推荐为空时的降级路径）。"""
    return [
        {
            "source_key": SOURCE_KEY_BOSS,
            "city_code": code,
            "url": build_boss_search_url(role_family, code),
        }
        for code in city_codes
    ]
