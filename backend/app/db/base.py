"""SQLAlchemy 声明基类。业务表在后续阶段的各模块中定义并导入到此元数据。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """全局声明基类；Alembic autogenerate 以 ``Base.metadata`` 为目标。"""
