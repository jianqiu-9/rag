"""本机开发启动入口：持久化 PostgreSQL + loopback TCP + FastAPI。

pgserver 在 macOS 上默认使用 -h "" 禁止 TCP，单改 postgresql.conf 会被启动参数覆盖。
本入口在确认实际配置后使用包内 pg_ctl 调整实例，再刷新 pgserver 的连接信息。
默认只监听 127.0.0.1:5433，现有原文件、表和向量均保持在原数据库中。
"""

import argparse
import shlex
import socket
from pathlib import Path

import pgserver
import psycopg
import uvicorn
from app.config import ROOT, Settings
from app.db import Database
from pgserver._commands import pg_ctl


def ensure_port_available(port: int):
    """调整数据库前先确认目标端口可绑定，避免先停库再发现端口冲突。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise RuntimeError(f"127.0.0.1:{port} 已占用，请先检查现有服务或指定其他端口") from exc


def ensure_local_tcp(server, port: int):
    """只在配置不一致时快速重启；重复执行不会无故中断已有数据库连接。"""
    # pgserver 0.1.4 的 socket URI 未带端口；已配置 5433 的实例再次启动时
    # libpq 会误用默认 5432。以 postmaster.pid 中的实际端口覆盖，仍兼容首次启动。
    actual_socket_port = server.get_postmaster_info().port
    with psycopg.connect(server.get_uri(), port=actual_socket_port) as conn:
        host, actual_port = conn.execute(
            "SELECT current_setting('listen_addresses'), current_setting('port')"
        ).fetchone()
    if host == "127.0.0.1" and actual_port == str(port):
        return
    ensure_port_available(port)
    options = shlex.join(["-h", "127.0.0.1", "-p", str(port), "-k", str(server.pgdata)])
    pg_ctl(
        ["-w", "-m", "fast", "-o", options, "-l", str(server.pgdata / "log"), "restart"],
        pgdata=server.pgdata,
        timeout=30,
    )
    # 重启后的 PID、端口已变化，不能继续使用先前缓存的 PostmasterInfo。
    server.ensure_postgres_running()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-port", type=int, default=5433, help="仅本机数据库端口，默认 5433")
    parser.add_argument("--port", type=int, default=8000, help="仅本机应用端口，默认 8000")
    parser.add_argument("--pgdata", type=Path, default=ROOT / "data/postgres")
    args = parser.parse_args()
    if not all(1 <= port <= 65535 for port in (args.db_port, args.port)):
        parser.error("端口必须在 1–65535 之间")
    try:
        ensure_port_available(args.port)
        server = pgserver.get_server(args.pgdata.resolve(), cleanup_mode="stop")
        try:
            ensure_local_tcp(server, args.db_port)
            settings = Settings(database_url=f"postgresql://postgres@127.0.0.1:{args.db_port}/postgres")
            Database(settings).migrate()
            from app.main import create_app

            print(f"PostgreSQL：127.0.0.1:{args.db_port}，数据库 postgres，用户 postgres", flush=True)
            print(f"项目页面：http://127.0.0.1:{args.port}/", flush=True)
            uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)
        finally:
            # stop 保留数据目录；不使用 delete，避免退出时误删知识库。
            server.cleanup()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
