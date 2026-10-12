"""数据库连接与会话管理。库文件路径可用环境变量 DATAFLOW_DB_PATH 覆盖（测试用）。"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = os.environ.get("DATAFLOW_DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "dataflow.db"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import models  # noqa: F401 确保模型注册

    Base.metadata.create_all(engine)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
