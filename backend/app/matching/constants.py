"""匹配领域常量：版本号、权重、阈值、词表（docs/07 第 2、4 节）。"""

# 版本链（docs/07 第 13 节）：任何算法调整必须递增版本，禁止无版本回归比较
SCORING_VERSION = "score_v1"
HARD_RULE_VERSION = "hard_v1"

# 综合评分 v1 初始权重（docs/07 第 4 节；60 组人工标注后校准）
COMPONENT_WEIGHTS: dict[str, int] = {
    "core_skills": 35,
    "experience": 20,
    "project_evidence": 20,
    "role_semantic": 10,
    "industry": 5,
    "preference": 10,
}
assert sum(COMPONENT_WEIGHTS.values()) == 100

# 等级阈值（docs/07 第 4 节）
GRADE_HIGH_MIN = 80
GRADE_POTENTIAL_MIN = 65


def grade_for(score_total: int) -> str:
    if score_total >= GRADE_HIGH_MIN:
        return "high"
    if score_total >= GRADE_POTENTIAL_MIN:
        return "potential"
    return "low"


# 外包信号判 failed 的置信度阈值：标题命中（0.9）→ failed；
# 仅正文命中（0.7）→ unknown 展示，不淘汰（docs/07 第 2 节：低置信不定性）
OUTSOURCING_FAIL_CONFIDENCE = 0.8

# 学历等级（与 app/jobs/normalize.py 的 level 编码一致）
EDUCATION_RANK: dict[str, int] = {
    "associate": 1,
    "bachelor": 2,
    "master": 3,
    "phd": 4,
}
# 简历侧学历文案 → level 编码
DEGREE_TEXT_TO_LEVEL: dict[str, str] = {
    "博士": "phd",
    "硕士": "master",
    "研究生": "master",
    "本科": "bachelor",
    "学士": "bachelor",
    "大专": "associate",
    "专科": "associate",
}

# 经验差 ≤1 年允许降分进入（docs/07 第 2 节）
EXPERIENCE_PENALTY_MAX_GAP_YEARS = 1.0

# 技术词表：核心技能/项目证据分项做确定性词面匹配用。
# 只用于定位证据 span，不做任何“常识补足”。
TECH_TERMS: tuple[str, ...] = (
    "python",
    "java",
    "go",
    "typescript",
    "javascript",
    "fastapi",
    "django",
    "flask",
    "celery",
    "asyncio",
    "pydantic",
    "spring",
    "spring boot",
    "spring cloud",
    "mybatis",
    "mysql",
    "postgresql",
    "pgvector",
    "redis",
    "kafka",
    "rabbitmq",
    "elasticsearch",
    "hive",
    "spark",
    "flink",
    "etl",
    "数仓",
    "数据仓库",
    "数据分析",
    "ab 实验",
    "归因",
    "docker",
    "kubernetes",
    "k8s",
    "ci",
    "gitlab",
    "react",
    "vue",
    "vue3",
    "next.js",
    "vite",
    "webpack",
    "小程序",
    "组件库",
    "llm",
    "langchain",
    "langgraph",
    "rag",
    "agent",
    "prompt",
    "提示工程",
    "大模型",
    "多模态",
    "向量数据库",
    "milvus",
    "bm25",
    "rerank",
    "function calling",
    "工具调用",
    "微服务",
    "分库分表",
    "消息队列",
    "高并发",
    "性能优化",
    "pytest",
    "selenium",
    "appium",
    "jmeter",
    "requests",
    "自动化测试",
    "接口自动化",
    "ui 自动化",
    "质量门禁",
    "回归测试",
    "功能测试",
    "sql",
)

# 行业词表：仅当岗位明确出现“XX行业/XX业务”要求时行业分项才参与判定
INDUSTRY_TERMS: tuple[str, ...] = (
    "金融",
    "电商",
    "医疗",
    "教育",
    "游戏",
    "政务",
    "汽车",
    "物流",
    "营销",
    "内容平台",
    "交易",
)
