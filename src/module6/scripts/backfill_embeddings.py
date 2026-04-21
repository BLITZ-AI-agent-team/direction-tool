#!/usr/bin/env python3
"""
Direction Module 6 - Embedding Backfill Script

既存のtranscriptsでtext_embeddingがNULLのレコードを対象に、
Gemini gemini-embedding-001 (768次元) でEmbeddingを生成してUPDATEする。

動画3,567本 × 平均13セグメント ≒ 46,000 API呼び出し想定。
約2.5時間で完了（5-10 req/s レートリミット想定）。

使い方:
  GEMINI_API_KEY=xxx python3 backfill_embeddings.py [--batch 50] [--limit N]

特徴:
  - バッチ処理（デフォルト50件）
  - 自動リトライ（API失敗時）
  - 進捗ログ（100件ごと）
  - --resume 不要（text_embedding IS NULL が自然なフィルタ）
  - 途中中断→再実行で続きから処理
"""

import os
import sys
import time
import json
import logging
import argparse
from datetime import datetime

import psycopg2
from pgvector.psycopg2 import register_vector


DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_W7wzN1jJyQXR@ep-lucky-shadow-a1xeitfg-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("backfill_embeddings.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("backfill_emb")


def embed_one(client, text, max_retries=3):
    from google.genai import types as gtypes
    last_err = None
    for attempt in range(max_retries):
        try:
            result = client.models.embed_content(
                model="gemini-embedding-001",
                contents=text,
                config=gtypes.EmbedContentConfig(output_dimensionality=768),
            )
            return list(result.embeddings[0].values)
        except Exception as e:
            last_err = e
            wait = 5 * (attempt + 1)
            log.warning(f"  embed retry {attempt+1}: {str(e)[:80]} waiting {wait}s")
            time.sleep(wait)
    raise last_err


def main():
    parser = argparse.ArgumentParser(description="Embedding backfill")
    parser.add_argument("--batch", type=int, default=50,
                        help="Batch size per DB fetch (default: 50)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Maximum total records to process (0 = all)")
    parser.add_argument("--sleep", type=float, default=0.2,
                        help="Sleep between API calls in seconds (default: 0.2)")
    args = parser.parse_args()

    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.error("GEMINI_API_KEY not set")
        sys.exit(1)
    gemini_client = genai.Client(api_key=api_key)

    conn = psycopg2.connect(DB_URL)
    register_vector(conn)

    # 総件数確認
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM transcripts WHERE text_embedding IS NULL")
    total_missing = cur.fetchone()[0]
    log.info(f"Missing embeddings: {total_missing:,} transcripts")

    if args.limit > 0:
        target = min(total_missing, args.limit)
    else:
        target = total_missing
    log.info(f"Target this run: {target:,}")
    if target == 0:
        log.info("Nothing to do. Exiting.")
        return

    start = datetime.now()
    processed = 0
    errors = 0

    while processed < target:
        # バッチ取得
        cur.execute(
            """
            SELECT id, text FROM transcripts
            WHERE text_embedding IS NULL
              AND text IS NOT NULL
              AND LENGTH(text) >= 2
            ORDER BY id
            LIMIT %s
            """,
            (args.batch,),
        )
        rows = cur.fetchall()
        if not rows:
            log.info("No more records to process.")
            break

        for row_id, text in rows:
            try:
                emb = embed_one(gemini_client, text)
                cur.execute(
                    "UPDATE transcripts SET text_embedding = %s WHERE id = %s",
                    (emb, row_id),
                )
                processed += 1
            except Exception as e:
                log.error(f"  [{processed}/{target}] ERROR id={row_id}: {str(e)[:100]}")
                errors += 1
                # 空の配列で埋めずに、失敗として残す（次回再挑戦可能）
            time.sleep(args.sleep)

            if processed % 100 == 0:
                conn.commit()
                elapsed = (datetime.now() - start).total_seconds()
                rate = processed / elapsed if elapsed > 0 else 0
                eta_min = (target - processed) / rate / 60 if rate > 0 else 0
                log.info(f"[{processed:,}/{target:,}] {rate:.1f}/s "
                         f"errors={errors} ETA: {eta_min:.0f}min")

            if processed >= target:
                break

        conn.commit()

    conn.commit()
    conn.close()
    elapsed = (datetime.now() - start).total_seconds()
    log.info("=" * 60)
    log.info(f"COMPLETE in {elapsed/3600:.2f} hours")
    log.info(f"  Processed: {processed:,}")
    log.info(f"  Errors: {errors:,}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
