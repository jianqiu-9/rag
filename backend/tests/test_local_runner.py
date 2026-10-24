"""验证本机启动入口的 TCP 参数，避免把数据库意外监听到局域网。"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/run_local.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("run_local", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configured_database_does_not_restart(tmp_path, monkeypatch):
    pytest.importorskip("pgserver")
    runner = load_runner()
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = ("127.0.0.1", "5433")
    monkeypatch.setattr(runner.psycopg, "connect", lambda *a, **k: connection)
    control = MagicMock()
    monkeypatch.setattr(runner, "pg_ctl", control)
    server = SimpleNamespace(
        pgdata=tmp_path, get_uri=lambda: "unused", get_postmaster_info=lambda: SimpleNamespace(port=5433)
    )
    runner.ensure_local_tcp(server, 5433)
    control.assert_not_called()


def test_socket_only_database_restarts_with_loopback_and_refreshes_uri(tmp_path, monkeypatch):
    pytest.importorskip("pgserver")
    runner = load_runner()
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = ("", "5432")
    monkeypatch.setattr(runner.psycopg, "connect", lambda *a, **k: connection)
    control = MagicMock()
    monkeypatch.setattr(runner, "pg_ctl", control)
    refresh = MagicMock()
    monkeypatch.setattr(runner, "ensure_port_available", MagicMock())
    server = SimpleNamespace(
        pgdata=tmp_path,
        get_uri=lambda: "unused",
        ensure_postgres_running=refresh,
        get_postmaster_info=lambda: SimpleNamespace(port=5432),
    )
    runner.ensure_local_tcp(server, 5433)
    arguments = control.call_args.args[0]
    options = arguments[arguments.index("-o") + 1]
    assert "-h 127.0.0.1" in options
    assert "-p 5433" in options
    assert "-k" in options
    assert "restart" in arguments
    refresh.assert_called_once()


def test_existing_nondefault_socket_port_is_not_lost_on_restart(tmp_path, monkeypatch):
    pytest.importorskip("pgserver")
    runner = load_runner()
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = ("127.0.0.1", "5433")
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(runner.psycopg, "connect", connect)
    server = SimpleNamespace(
        pgdata=tmp_path,
        get_uri=lambda: "postgresql://postgres@/postgres?host=/tmp/testpg",
        get_postmaster_info=lambda: SimpleNamespace(port=5433),
    )
    runner.ensure_local_tcp(server, 5433)
    assert connect.call_args.kwargs["port"] == 5433
