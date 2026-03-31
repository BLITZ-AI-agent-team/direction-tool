"""
Direction Module 6 - セマンティック検索

3層検索で過去動画素材からシーンを検索:
1. テキスト検索 — 文字起こしからキーワードマッチ
2. セマンティック検索 — ベクトル類似度で意味的に近いシーンを検索
3. 映像類似検索 — CLIPベクトルで視覚的に類似するシーンを検索（将来拡張）
"""

import os
import sys
import json
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from shared.db.client import DirectionDB

load_dotenv(Path(__file__).resolve().parent.parent / "config" / ".env")


def get_query_embedding(query_text):
    """検索クエリをベクトル化"""
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=query_text,
    )
    return response.data[0].embedding


def format_timecode(start_tc, end_tc):
    """タイムコードを読みやすい形式に"""
    start = start_tc.split(".")[0] if "." in start_tc else start_tc
    end = end_tc.split(".")[0] if "." in end_tc else end_tc
    return f"{start} ~ {end}"


def search(query, mode="hybrid", limit=10, source_type=None):
    """
    統合検索

    Args:
        query: 検索クエリ（自然言語）
        mode: "keyword" / "semantic" / "hybrid"
        limit: 最大件数
        source_type: "original" / "reference" / None（全て）

    Returns:
        検索結果リスト
    """
    db = DirectionDB()
    db.connect()

    results = []

    try:
        if mode in ("keyword", "hybrid"):
            keyword_results = db.search_by_keyword(query, limit=limit)
            for r in keyword_results:
                results.append({
                    "match_type": "keyword",
                    "file_name": r["file_name"],
                    "file_path": r["file_path"],
                    "folder": r.get("drive_folder_path", ""),
                    "timecode": format_timecode(r["start_tc"], r["end_tc"]),
                    "start_sec": r["start_sec"],
                    "end_sec": r["end_sec"],
                    "text": r["text"],
                    "similarity": 1.0,
                })

        if mode in ("semantic", "hybrid"):
            query_embedding = get_query_embedding(query)
            semantic_results = db.search_by_text(
                query_embedding, limit=limit, source_type=source_type
            )
            for r in semantic_results:
                results.append({
                    "match_type": "semantic",
                    "file_name": r["file_name"],
                    "file_path": r["file_path"],
                    "folder": r.get("drive_folder_path", ""),
                    "timecode": format_timecode(r["start_tc"], r["end_tc"]),
                    "start_sec": r["start_sec"],
                    "end_sec": r["end_sec"],
                    "text": r["text"],
                    "similarity": round(float(r.get("similarity", 0)), 4),
                })

        # 重複排除（同一ファイル・同一タイムコードの結果をマージ）
        seen = set()
        unique_results = []
        for r in results:
            key = (r["file_name"], r["start_sec"])
            if key not in seen:
                seen.add(key)
                unique_results.append(r)

        # similarity降順でソート
        unique_results.sort(key=lambda x: x["similarity"], reverse=True)

        return unique_results[:limit]

    finally:
        db.close()


def format_results(results):
    """検索結果を表示用にフォーマット"""
    if not results:
        return "検索結果が見つかりませんでした。"

    lines = []
    lines.append(f"検索結果: {len(results)}件")
    lines.append("=" * 60)

    for i, r in enumerate(results, 1):
        lines.append(f"\n{i}. {r['file_name']}")
        lines.append(f"   時間: {r['timecode']}")
        if r.get("folder"):
            lines.append(f"   フォルダ: {r['folder']}")
        lines.append(f"   テキスト: {r['text'][:100]}{'...' if len(r['text']) > 100 else ''}")
        lines.append(f"   類似度: {r['similarity']:.2%} ({r['match_type']})")

    return "\n".join(lines)


def search_and_display(query, mode="hybrid", limit=10, source_type=None):
    """検索して結果を表示"""
    print(f"\n検索: 「{query}」 (mode={mode}, limit={limit})")
    print("-" * 60)

    results = search(query, mode=mode, limit=limit, source_type=source_type)
    print(format_results(results))

    return results


# ============================================================
# CLI エントリーポイント
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Direction Module 6 - Semantic Search")
    parser.add_argument("query", help="Search query (natural language)")
    parser.add_argument("--mode", choices=["keyword", "semantic", "hybrid"],
                        default="hybrid", help="Search mode")
    parser.add_argument("--limit", type=int, default=10, help="Max results")
    parser.add_argument("--source", choices=["original", "reference"],
                        help="Filter by source type")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    results = search(args.query, mode=args.mode, limit=args.limit, source_type=args.source)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(format_results(results))
