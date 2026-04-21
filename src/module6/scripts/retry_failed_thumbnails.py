#!/usr/bin/env python3
"""
Direction Module 6 - Thumbnail Retry Script

backfill_thumbnails.py 完了後に残ったサムネイル無し動画を対象に、
ダウンロードリトライ機能付きで再処理する。

主な失敗原因と対策:
  - SSL DECRYPTION_FAILED / BAD_RECORD_TYPE → exponential backoffリトライ
  - IncompleteRead → 部分ファイル削除後リトライ
  - NoneType errors → Google Drive APIの一時エラー、リトライで解消

使い方:
  python3 retry_failed_thumbnails.py --sa-key /root/sa-key.json [--workers 2]

特徴:
  - DL失敗時に最大3回 exponential backoff リトライ（10s/20s/30s待機）
  - workers=2（SSL不安定対策で並列度を下げる）
  - 動画サイズの大きい順に処理（残ってるのは大容量が多いため）
  - 部分ダウンロードファイルの自動削除
"""

import os
import sys
import time
import logging
import argparse
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
from psycopg2.pool import ThreadedConnectionPool


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
        logging.FileHandler("retry_failed_thumbnails.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("retry_thumb")


def get_drive_service(sa_key_path):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        sa_key_path, scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds)


def download_file_with_retry(service, file_id, output_path, max_retries=3):
    """Exponential backoffリトライ付きダウンロード"""
    from googleapiclient.http import MediaIoBaseDownload
    last_err = None
    for attempt in range(max_retries):
        try:
            request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
            with open(output_path, "wb") as f:
                dl = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    _, done = dl.next_chunk()
            # 成功チェック: 0バイトじゃないこと
            if Path(output_path).stat().st_size > 0:
                return True
            raise RuntimeError("Downloaded file is empty")
        except Exception as e:
            last_err = e
            # 部分ファイル削除
            try:
                if Path(output_path).exists():
                    Path(output_path).unlink()
            except Exception:
                pass
            if attempt < max_retries - 1:
                wait = 10 * (attempt + 1)
                log.warning(f"  DL retry {attempt+1}/{max_retries}: "
                            f"{str(e)[:80]} waiting {wait}s")
                time.sleep(wait)
    if last_err:
        raise last_err
    return False


def extract_thumbnail(video_path, timestamp_sec, output_path, timeout=10):
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
    except Exception:
        return False


def process_one_asset(asset, drive_service, db_pool, tmp_dir):
    asset_id, drive_id, file_name, transcripts = asset

    local_path = Path(tmp_dir) / f"{asset_id}_{file_name}"
    thumb_dir = THUMB_ROOT / str(asset_id)
    thumb_dir.mkdir(parents=True, exist_ok=True)

    result = {"asset_id": asset_id, "file": file_name, "ok": 0, "err": 0}

    try:
        # Download with retry
        try:
            download_file_with_retry(drive_service, drive_id, str(local_path))
        except Exception as e:
            log.error(f"  DL FINAL FAIL: {file_name}: {str(e)[:100]}")
            result["err"] = len(transcripts)
            result["error"] = "download_failed_after_retries"
            return result

        # 既存サムネイルファイル確認
        existing_files = set(p.name for p in thumb_dir.glob("*.jpg"))
        seg_idx = 0
        while f"{seg_idx}.jpg" in existing_files:
            seg_idx += 1

        # サムネ抽出 + DB更新
        conn = db_pool.getconn()
        try:
            cur = conn.cursor()
            for transcript_id, start_sec in transcripts:
                thumb_file = thumb_dir / f"{seg_idx}.jpg"
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
    parser = argparse.ArgumentParser(description="Retry failed thumbnails")
    parser.add_argument("--sa-key",
                        default=os.environ.get("GOOGLE_SERVICE_ACCOUNT_KEY_PATH", ""),
                        help="Google Drive service account JSON path")
    parser.add_argument("--workers", type=int, default=2,
                        help="Parallel workers (default: 2 for stability)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max assets to process (0 = all)")
    args = parser.parse_args()

    if not args.sa_key or not Path(args.sa_key).exists():
        log.error(f"Service account key not found: {args.sa_key}")
        sys.exit(1)

    drive_service = get_drive_service(args.sa_key)
    db_pool = ThreadedConnectionPool(1, args.workers + 2, DB_URL)

    # サムネイル無し動画をサイズ降順で取得（残ってるのは大容量が多いため）
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
        ORDER BY size DESC
        """
    )
    assets_meta = cur.fetchall()
    log.info(f"Failed assets to retry: {len(assets_meta):,}")
    if args.limit > 0:
        assets_meta = assets_meta[: args.limit]

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
    log.info(f"Retrying {total:,} assets with {total_segs:,} missing thumbnails")
    log.info(f"Workers: {args.workers} (reduced for stability)")
    log.info(f"Download retries: 3 with exponential backoff")

    THUMB_ROOT.mkdir(parents=True, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="retry_thumb_")
    start_time = datetime.now()
    stats = {"assets_ok": 0, "assets_err": 0, "thumbs_ok": 0, "thumbs_err": 0}

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(process_one_asset, asset, drive_service, db_pool, tmp_dir): asset[2]
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
        log.info(f"RETRY COMPLETE in {elapsed/3600:.2f} hours")
        log.info(f"  Assets OK: {stats['assets_ok']:,}")
        log.info(f"  Assets ERR: {stats['assets_err']:,}")
        log.info(f"  Thumbnails generated: {stats['thumbs_ok']:,}")
        log.info(f"  Thumbnails failed: {stats['thumbs_err']:,}")
        log.info("=" * 60)
        db_pool.closeall()


if __name__ == "__main__":
    main()
