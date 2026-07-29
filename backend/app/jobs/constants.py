"""岗位领域受控词表：六类方向、六城市行政区码、办公方式（docs/01 3.1 节）。"""

# 六类岗位方向（docs/01）：无法可靠分类时用 unknown，不得强行分配
ROLE_FAMILIES: tuple[str, ...] = (
    "ai_application",  # AI 应用开发 / 大模型应用工程师
    "backend_python",  # Python 后端开发
    "backend_java",  # Java 后端开发
    "frontend",  # 前端开发
    "data",  # 数据分析 / 数据工程
    "qa",  # 软件测试 / 测试开发
)
ROLE_FAMILY_UNKNOWN = "unknown"

ROLE_FAMILY_LABELS: dict[str, str] = {
    "ai_application": "AI 应用开发",
    "backend_python": "Python 后端开发",
    "backend_java": "Java 后端开发",
    "frontend": "前端开发",
    "data": "数据分析/数据工程",
    "qa": "软件测试/测试开发",
}

# 六城市：市级行政区划代码（GB/T 2260）
CITY_CODE_BY_NAME: dict[str, str] = {
    "北京": "110100",
    "上海": "310100",
    "深圳": "440300",
    "杭州": "330100",
    "广州": "440100",
    "成都": "510100",
}
CITY_NAME_BY_CODE: dict[str, str] = {v: k for k, v in CITY_CODE_BY_NAME.items()}
SUPPORTED_CITY_CODES: tuple[str, ...] = tuple(CITY_CODE_BY_NAME.values())

WORK_MODES: tuple[str, ...] = ("onsite", "hybrid", "remote")

# 来源 key（与 /health/capabilities 中 job_source:* 对应）
SOURCE_KEY_FIXTURE_A = "fixture_a"
SOURCE_KEY_FIXTURE_B = "fixture_b"
SOURCE_KEY_BOSS = "boss"
SOURCE_KEY_USER_IMPORT = "user_import"
