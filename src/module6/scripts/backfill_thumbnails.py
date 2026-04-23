#!/usr/bin/env python3
"""
Direction Module 6 - Thumbnail Backfill Script

既存のtranscriptsでthumbnail_pathがNULLのレコードを対象に、
Google Driveから動画を再ダウンロードし、FFmpegで各セグメントの
サムネイルを抽出→ VPSファイル保存→ DB UPDATE する。

サムネイル無し動画 3,871本 × 平均13セグメント ≒ 50,000枚生成想定。
並列4ワーカーで約5〜8時間を想定。

使い方:
  python3 backfill_thumbnails.py --sa-key /root/sa-key.json [--workers 4]

特徴:
  - 動画ダウンロード並列化（デフォルト4ワーカー）
  - 動画サイズの小さい順に処理（序盤の進捗が早い）
  - 1動画1回ダウンロードで全セグメント一括抽出
  - --resume 不要（thumbnail_path IS NULL が自然なフィルタ）
  - 中断→再実行で続きから処理
  - 動画処理完了ごとに一時ファイル削除
"""

import os
import sys
import time
import json
import logging
import argparse
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
from psycopg2.pool import ThreadedConnectionPool

# Google API (httplib2) のデフォルトソケットタイムアウトは10秒で大容量動画DLに不十分。
# 180秒に延長することで大容量動画のチャンク読み取りが途中で切れないようにする。
import socket
socket.setdefaulttimeout(180)


DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_W7wzN1jJyQXR@ep-lucky-shadow-a1xeitfg-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require",
)

THUMB_ROOT = Path(os.environ.get("THUMB_ROOT", "/root/thumbnails"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("backfill_thumbnails.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("backfill_thumb")


def get_drive_service(sa_key_path):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        sa_key_path, scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds)


def download_file(service, file_id, output_path):
    from googleapiclient.http import MediaIoBaseDownload
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(output_path, "wb") as f:
        dl = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = dl.next_chunk()


def extract_thumbnail(video_path, timestamp_sec, output_path, timeout=30):
    """単一サムネイル抽出 (320x180, JPEG q=5)"""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(max(0, timestamp_sec)),
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2",
        "-q:v", "5",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return result.returncode == 0 and Path(output_path).exists()
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def process_one_asset(asset, sa_key_path, db_pool, tmp_dir):
    """1動画の全サムネイル再生成（スレッドセーフ: 各ワーカーで独立drive_service生成）"""
    asset_id, drive_id, file_name, transcripts = asset
    # transcripts: [(transcript_id, start_sec), ...] (thumbnail_path IS NULL のみ)

    # スレッドセーフのため、各ワーカーで独立したdrive_serviceを生成
    # httplib2はスレッドセーフではないため共有禁止
    drive_service = get_drive_service(sa_key_path)

    local_path = Path(tmp_dir) / f"{asset_id}_{file_name}"
    thumb_dir = THUMB_ROOT / str(asset_id)
    thumb_dir.mkdir(parents=True, exist_ok=True)

    result = {"asset_id": asset_id, "file": file_name, "ok": 0, "err": 0}

    try:
        # Download
        try:
            download_file(drive_service, drive_id, str(local_path))
        except Exception as e:
            log.error(f"  DL FAIL: {file_name}: {str(e)[:80]}")
            result["err"] = len(transcripts)
            result["error"] = "download_failed"
            return result

        if not local_path.exists() or local_path.stat().st_size == 0:
            result["err"] = len(transcripts)
            result["error"] = "empty_download"
            return result

        # 既存サムネイルファイルを確認して seg_idx を決定
        # 既存の seg_idx を把握して重複避ける
        existing_files = set(p.name for p in thumb_dir.glob("*.jpg"))
        seg_idx = 0
        while f"{seg_idx}.jpg" in existing_files:
            seg_idx += 1

        # サムネイル抽出 + DB UPDATE
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            for transcript_id, start_sec in transcripts:
                thumb_file = thumb_dir / f"{seg_idx}.jpg"
                # 既存ファイルがあればインクリメント
                while thumb_file.exists():
                    seg_idx += 1
                    thumb_file = thumb_dir / f"{seg_idx}.jpg"

                ok = extract_thumbnail(local_path, float(start_sec), thumb_file)
                if ok:
                    thumb_path_val = f"{asset_id}/{seg_idx}.jpg"
                    cur.execute(
                        "UPDATE transcripts SET thumbnail_path = %s WHERE id = %s",
                        (thumb_path_val, transcript_id),
                    )
                    result["ok"] += 1
                    seg_idx += 1
                else:
                    result["err"] += 1
            conn.commit()
        finally:
            db_pool.putconn(conn)

        return result

    except Exception as e:
        log.error(f"  ERROR: {file_name}: {str(e)[:100]}")
        result["err"] = len(transcripts)
        result["error"] = str(e)[:100]
        return result

    finally:
        if local_path.exists():
            try:
                local_path.unlink()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="Thumbnail backfill")
    parser.add_argument("--sa-key",
                        default=os.environ.get("GOOGLE_SERVICE_ACCOUNT_KEY_PATH", ""),
                        help="Google Drive service account JSON path")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel download workers (default: 4)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max assets to process in this run (0 = all)")
    args = parser.parse_args()

    if not args.sa_key or not Path(args.sa_key).exists():
        log.error(f"Service account key not found: {args.sa_key}")
        sys.exit(1)

    # httplib2はスレッドセーフでないため、各処理ワーカー内でdrive_serviceを新規生成する。
    # メイン側では不要（DBクエリのみ実行）。
    db_pool = ThreadedConnectionPool(1, args.workers + 2, DB_URL)

    # サムネイル無しのasset一覧を取得（動画サイズ昇順）
    conn = db_pool.getconn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.id, a.drive_id, a.file_name, COALESCE(a.file_size_bytes, 0) AS size
        FROM media_assets a
        WHERE a.source_type = 'original'
          AND a.drive_id IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM transcripts t
            WHERE t.asset_id = a.id AND t.thumbnail_path IS NULL
          )
        ORDER BY size ASC
        """
    )
    assets_meta = cur.fetchall()
    log.info(f"Assets needing thumbnails: {len(assets_meta):,}")
    if args.limit > 0:
        assets_meta = assets_meta[: args.limit]
        log.info(f"Limiting to {len(assets_meta):,} assets")

    # 各asset単位でtranscripts取得
    assets = []
    for asset_id, drive_id, file_name, _size in assets_meta:
        cur.execute(
            """
            SELECT id, start_sec FROM transcripts
            WHERE asset_id = %s AND thumbnail_path IS NULL
            ORDER BY start_sec
            """,
            (asset_id,),
        )
        transcripts = cur.fetchall()
        if transcripts:
            assets.append((asset_id, drive_id, file_name, transcripts))

    db_pool.putconn(conn)
    total = len(assets)
    total_segs = sum(len(a[3]) for a in assets)
    log.info(f"Processing {total:,} assets with {total_segs:,} missing thumbnails")
    log.info(f"Workers: {args.workers}")

    THUMB_ROOT.mkdir(parents=True, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="backfill_thumb_")
    start_time = datetime.now()
    stats = {"assets_ok": 0, "assets_err": 0, "thumbs_ok": 0, "thumbs_err": 0}

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(process_one_asset, asset, args.sa_key, db_pool, tmp_dir): asset[2]
                for asset in assets
            }

            for i, future in enumerate(as_completed(futures), 1):
                fname = futures[future]
                try:
                    r = future.result()
                    stats["thumbs_ok"] += r.get("ok", 0)
                    stats["thumbs_err"] += r.get("err", 0)
                    if r.get("ok", 0) > 0 and not r.get("error"):
                        stats["assets_ok"] += 1
                    else:
                        stats["assets_err"] += 1

                    elapsed = (datetime.now() - start_time).total_seconds()
                    rate = i / elapsed if elapsed > 0 else 0
                    eta_min = (total - i) / rate / 60 if rate > 0 else 0

                    log.info(f"[{i:,}/{total:,}] {fname}: "
                             f"ok={r.get('ok', 0)} err={r.get('err', 0)} "
                             f"ETA: {eta_min:.0f}min")
                except Exception as e:
                    stats["assets_err"] += 1
                    log.error(f"Future error: {fname}: {str(e)[:100]}")

    finally:
        elapsed = (datetime.now() - start_time).total_seconds()
        log.info("=" * 60)
        log.info(f"COMPLETE in {elapsed/3600:.2f} hours")
        log.info(f"  Assets OK: {stats['assets_ok']:,}")
        log.info(f"  Assets ERR: {stats['assets_err']:,}")
        log.info(f"  Thumbnails generated: {stats['thumbs_ok']:,}")
        log.info(f"  Thumbnails failed: {stats['thumbs_err']:,}")
        log.info("=" * 60)
        db_pool.closeall()


if __name__ == "__main__":
    main()
