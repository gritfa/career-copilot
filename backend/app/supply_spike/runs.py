"""运行结果持久化：supply-spike/reports/runs/ 下的不可变 JSON。"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.supply_spike.config import DEFAULT_SPIKE_DIR

RUN_SCHEMA_VERSION = "1"


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_run_id(command: str) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{command}-{uuid.uuid4().hex[:8]}"


def runs_dir(spike_dir: Path | None = None) -> Path:
    return (spike_dir or DEFAULT_SPIKE_DIR) / "reports" / "runs"


def raw_dir(run_id: str, spike_dir: Path | None = None) -> Path:
    return (spike_dir or DEFAULT_SPIKE_DIR) / "reports" / "raw" / run_id


def write_run(record: dict, spike_dir: Path | None = None) -> Path:
    """写入不可变运行记录；重名拒写，绝不覆盖历史结果。"""
    directory = runs_dir(spike_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record['run_id']}.json"
    if path.exists():
        raise FileExistsError(f"运行记录已存在，拒绝覆盖: {path}")
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_runs(spike_dir: Path | None = None, command: str | None = None) -> list[dict]:
    """按时间顺序读取历史运行记录（command 过滤可选）。"""
    directory = runs_dir(spike_dir)
    if not directory.exists():
        return []
    records: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if command is None or data.get("command") == command:
            records.append(data)
    return records
