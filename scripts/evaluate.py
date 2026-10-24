"""固定样本校验、阈值标定与真实模型验收；运行结果不冒充人工语义审核。

先把 fixtures/acceptance/v1/*.md 导入专用验收数据库。标定模式只读取 calibration，
acceptance 模式才读取独立验收集；两者都不改写样本或自动修改环境配置。
"""

import argparse
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.config import ROOT, Settings
from app.db import Database
from app.embedding import Embedding
from app.errors import AppError
from app.llm import DeepSeek
from app.query import QueryService
from app.repository import Repository
from app.schemas import QueryRequest

FIXTURES = ROOT / "fixtures/acceptance/v1"


def load_cases():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in {**manifest["documents"], "queries.json": manifest["query_sha256"]}.items():
        if hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"验收样本 {name} 已改变，请审核并建立新版本，不要覆盖既有真值")
    return json.loads((FIXTURES / "queries.json").read_text(encoding="utf-8")), manifest


def matched(evidence, expected):
    return any(
        e.filename == source["filename"]
        and e.line_start <= source["line_end"]
        and e.line_end >= source["line_start"]
        for e in evidence
        for source in expected
    )


def calibrate(cases, settings, repo, embedding):
    cases = [c for c in cases if c["split"] == "calibration"]
    results = [
        (case, repo.search(embedding.query(case["query"]), 8, -1.0, embedding.profile)) for case in cases
    ]
    scores = []
    for step in range(1, 10):
        threshold = round(step / 10, 1)
        positive = [
            matched([e for e in hits if e.score >= threshold], case["expected_sources"])
            for case, hits in results
            if case["kind"] == "answerable"
        ]
        negative = [
            any(e.score >= threshold for e in hits) for case, hits in results if case["kind"] != "answerable"
        ]
        scores.append(
            {
                "threshold": threshold,
                "positive_recall": sum(positive) / len(positive),
                "negative_has_candidate_rate": sum(negative) / len(negative),
            }
        )
    eligible = [score for score in scores if score["positive_recall"] >= 0.9]
    best = (
        min(eligible, key=lambda score: (score["negative_has_candidate_rate"], -score["threshold"]))
        if eligible
        else None
    )
    return {
        "mode": "calibration",
        "sample_count": len(cases),
        "scores": scores,
        "suggested_threshold": best["threshold"] if best else None,
        "note": "建议值仍需完整生成验收；无候选率不是 LLM 正确拒答率，配置未自动修改。",
    }


def acceptance(cases, settings, repo, embedding):
    service = QueryService(settings, repo, embedding, DeepSeek(settings))
    rows = []
    positives = []
    for case in (c for c in cases if c["split"] == "acceptance"):
        row = {
            "id": case["id"],
            "expected_status": case["expected_status"],
            "expected_key_points": case["expected_key_points"],
            "expected_sources": case["expected_sources"],
        }
        try:
            hits = repo.search(
                embedding.query(case["query"]), 8, settings.similarity_threshold, embedding.profile
            )
            if case["kind"] == "answerable":
                recall = matched(hits, case["expected_sources"])
                positives.append(recall)
                row["retrieval_hit"] = recall
            response = service.execute(QueryRequest(query=case["query"]), str(uuid4()))
            row["response"] = response.model_dump()
            row["status_matches"] = response.status == case["expected_status"]
            row["manual_grounding_review"] = "pending" if response.status == "answered" else "not_applicable"
        except AppError as exc:
            row["error_code"] = exc.code
            row["status_matches"] = False
        rows.append(row)
    recall_rate = sum(positives) / len(positives) if positives else 0
    automatic_pass = recall_rate >= 0.9 and all(row["status_matches"] for row in rows)
    return {
        "mode": "acceptance",
        "sample_count": len(rows),
        "positive_recall_at_8": recall_rate,
        "automatic_checks_pass": automatic_pass,
        "overall_acceptance": "pending_manual_review" if automatic_pass else "failed",
        "note": "还须人工逐条核对答案要点、引用支撑以及 TC-01 至 TC-14；自动通过不代表完整验收通过。",
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["verify-fixtures", "calibrate", "acceptance"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases, manifest = load_cases()
    if args.mode == "verify-fixtures":
        print(f"验收资料哈希有效：{len(manifest['documents'])} 份文档，{len(cases)} 条查询")
        return
    settings = Settings()
    repo = Repository(Database(settings))
    repo.db.check()
    if not repo.has_active_documents():
        raise SystemExit("请先向专用验收数据库导入固定资料")
    embedding = Embedding(settings)
    report = (
        calibrate(cases, settings, repo, embedding)
        if args.mode == "calibrate"
        else acceptance(cases, settings, repo, embedding)
    )
    report.update(
        {
            "dataset_version": manifest["version"],
            "query_sha256": manifest["query_sha256"],
            "embedding": embedding.metadata,
            "llm": settings.deepseek_model,
            "threshold": settings.similarity_threshold,
        }
    )
    output = args.output or settings.data_dir / "reports" / f"{args.mode}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"报告已保存：{output}")


if __name__ == "__main__":
    main()
