"""岗位供给 Spike 工具（ADR-002 硬门槛验证）。

与生产连接器（app/jobs/adapters）完全隔离：不写业务数据库，不复用 Adapter，
所有结果落 supply-spike/reports/ 为不可变 JSON。仅复用 app.jobs.normalize /
app.jobs.constants 的纯函数与受控词表（非连接器代码）。
"""
