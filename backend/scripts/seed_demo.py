"""一键 demo 种子脚本（阶段 11 P1）。

用法（backend/ 目录下，基础服务已启动且已 alembic upgrade head）：

    uv run python scripts/seed_demo.py

幂等：重复执行不产生重复数据。逻辑在 app/demo/seed.py（可测试）。
"""

import asyncio
import os
import sys

# 必须在导入 app 之前设置：解析/推荐任务在 seed 进程内同步执行（真实任务代码）
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "1"

from app.demo.seed import SeedError, format_report, run_demo_seed  # noqa: E402


def main() -> int:
    mailpit_api = os.environ.get("DEMO_MAILPIT_API", "http://localhost:8025/api/v1")
    try:
        report = asyncio.run(run_demo_seed(mailpit_api=mailpit_api))
    except SeedError as exc:
        print(f"[seed_demo] 失败：{exc}", file=sys.stderr)
        return 1
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
