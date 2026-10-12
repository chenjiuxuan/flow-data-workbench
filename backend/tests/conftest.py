"""测试环境：必须在任何 app 模块导入前设置独立的测试库路径。"""
import os
import tempfile
import atexit

_TEST_DIR = tempfile.TemporaryDirectory(prefix="dataflow-tests-", ignore_cleanup_errors=True)
_TEST_DB = os.path.join(_TEST_DIR.name, "dataflow_test.db")
os.environ["DATAFLOW_DB_PATH"] = _TEST_DB

# TestClient 不进上下文管理器时不会触发 lifespan，这里手动建表并灌演示数据
from app.db import engine, init_db  # noqa: E402
from app.main import seed_if_empty  # noqa: E402

init_db()
seed_if_empty()


def _cleanup():
    engine.dispose()
    _TEST_DIR.cleanup()


atexit.register(_cleanup)
