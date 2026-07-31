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
# 演示种子来源（阶段 11 P1）：全部内容为合成数据，绝不含真实公司在招岗位正文
SOURCE_KEY_SYNTHETIC_SEED = "synthetic_seed"

# canonical_jobs.data_origin 受控词表（阶段 11 P1）：
# - connector：连接器采集（当前仅合成 fixture 连接器）
# - user_import：用户手动导入（正文/URL）
# - synthetic_seed：演示种子数据——前端必须在列表/详情展示「合成示例」徽标
DATA_ORIGIN_CONNECTOR = "connector"
DATA_ORIGIN_USER_IMPORT = "user_import"
DATA_ORIGIN_SYNTHETIC_SEED = "synthetic_seed"
DATA_ORIGINS: tuple[str, ...] = (
    DATA_ORIGIN_CONNECTOR,
    DATA_ORIGIN_USER_IMPORT,
    DATA_ORIGIN_SYNTHETIC_SEED,
)
