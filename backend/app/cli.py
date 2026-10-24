"""显式初始化命令：迁移和模型下载不隐式混入用户的查询请求。"""

import argparse
import json
from datetime import datetime
from uuid import UUID

from app.audit import AuditStore
from app.config import Settings
from app.db import Database
from app.errors import AppError


def download_model(settings: Settings, revision: str):
    from huggingface_hub import HfApi, snapshot_download
    from huggingface_hub.errors import HfHubHTTPError

    # main 仅用于首次解析；下载实际使用不可变 SHA，运行时绝不再次解析 main。
    try:
        info = HfApi().model_info(settings.embedding_model, revision=revision)
    except Exception as exc:
        raise AppError(
            "MODEL_DOWNLOAD_FAILED", "无法查询模型版本，请检查网络、模型 ID 和 Hugging Face 访问权限", 503
        ) from exc
    settings.model_cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(
            settings.embedding_model,
            revision=info.sha,
            cache_dir=str(settings.model_cache_dir),
            ignore_patterns=["*.onnx", "onnx/*", "*.gguf", "*.tflite"],
        )
    except HfHubHTTPError as exc:
        raise AppError(
            "MODEL_DOWNLOAD_FAILED", "模型下载失败，请在模型页面接受条款并配置有权限的 HF_TOKEN", 503
        ) from exc
    lock = {"model_id": settings.embedding_model, "revision": info.sha}
    (settings.model_cache_dir / "model-lock.json").write_text(
        json.dumps(lock, indent=2) + "\n", encoding="utf-8"
    )
    print(f"模型已下载并锁定：{settings.embedding_model}@{info.sha}")


def timestamp(value):
    """显式要求时区，避免本地时区差异导致删错时间范围。"""
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result
    except ValueError:
        raise argparse.ArgumentTypeError(
            "时间必须是含时区的 ISO 8601，例如 2026-10-24T00:00:00+08:00"
        ) from None


def main():
    parser = argparse.ArgumentParser(description="Agent 面经知识库初始化工具")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="执行数据库迁移")
    sub.add_parser("recover-records", help="恢复已失去会话租约的中断问答，不重新调用模型")
    model = sub.add_parser("download-model", help="显式下载 EmbeddingGemma 并保存 SHA 锁文件")
    model.add_argument("--revision", default="main")
    records = sub.add_parser("records", help="按请求 ID 查看完整问答及调用记录，仅本机管理")
    records.add_argument("--request-id", required=True, type=UUID)
    purge = sub.add_parser("purge-records", help="级联清理完整问答；默认仅清理到期终态记录")
    selector = purge.add_mutually_exclusive_group()
    selector.add_argument("--request-id", type=UUID)
    selector.add_argument("--document-id", type=UUID)
    selector.add_argument("--before", type=timestamp)
    purge.add_argument("--after", type=timestamp)
    purge.add_argument("--dry-run", action="store_true", help="仅统计，不删除")
    args = parser.parse_args()
    if args.command == "purge-records":
        if args.after and (args.request_id or args.document_id or not args.before):
            parser.error("--after 必须与 --before 配对，不能与 ID 过滤同时使用")
        if args.after and args.after >= args.before:
            parser.error("--after 必须早于 --before")
    settings = Settings()
    if args.command == "migrate":
        Database(settings).migrate()
        print("数据库迁移完成")
    elif args.command in {"records", "purge-records", "recover-records"}:
        store = AuditStore(Database(settings), settings)
        try:
            if args.command == "recover-records":
                print(f"已标记 {store.recover_interrupted()} 条中断问答，不会自动重发模型请求")
            elif args.command == "records":
                record = store.get_record(str(args.request_id))
                if record is None:
                    raise SystemExit("未找到该请求记录（可能已过期或已清理）")
                print(json.dumps(record, ensure_ascii=False, indent=2, default=str))
            else:
                count = store.purge(
                    request_id=str(args.request_id) if args.request_id else None,
                    document_id=str(args.document_id) if args.document_id else None,
                    before=args.before,
                    after=args.after,
                    dry_run=args.dry_run,
                )
                print(f"{'将清理' if args.dry_run else '已清理'} {count} 条问答及其全部调用记录")
        except AppError as exc:
            raise SystemExit(exc.message) from None
        except Exception:
            raise SystemExit("记录操作失败，请检查数据库及迁移状态") from None
    else:
        try:
            download_model(settings, settings.embedding_revision or args.revision)
        except AppError as exc:
            raise SystemExit(exc.message) from None


if __name__ == "__main__":
    main()
