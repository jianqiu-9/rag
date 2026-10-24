"""PyCharm 便捷入口：复用后端隔离环境，避免误用旧的项目 Python 解释器。"""

import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    python = root / "backend/.venv/bin/python"
    if not python.exists():
        raise SystemExit("请先运行：uv sync --project backend --python 3.12 --extra dev --extra models")
    # 不使用 shell 拼接命令；服务默认仅在本机监听。
    raise SystemExit(
        subprocess.call(
            [str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"], cwd=root
        )
    )


if __name__ == "__main__":
    main()
