#!/usr/bin/env python3
"""
Direction Module 6 - 大規模バッチ処理スクリプト（独立実行版）

Hostinger VPS または ローカルPCで直接実行可能。
Claude Codeのセッションに依存しない。

使い方:
  python bulk_runner.py --folder-ids "ID1,ID2,..." [--workers 3] [--resume]

特徴:
  - 並列処理（デフォルト3ワーカー）
  - 処理済み動画の自動スキップ（再開可能）
  - 無音動画の早期検出・スキップ
  - エラーリトライ（最大3回）
  - 進捗ログ・統計レポート
  - Gemini 3.1 Flash-Lite Preview使用
"""

import os
import sys
import json
import re
import time
import tempfile
import logging
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from pgvector.psycopg2 import register_vector

# ============================================================
# 設定
# ============================================================

DB_URL = os.environ.get("DATABASE_URL",
    "postgresql://neondb_owner:npg_W7wzN1jJyQXR@ep-lucky-shadow-a1xeitfg-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require")

GEMINI_MODEL = "gemini-3.1-flash-lite-preview"

PROMPT = (
    'この音声を日本語で正確に文字起こししてください。'
    '各セグメントの開始秒と終了秒とテキストをJSON配列で返してください。'
    'フォーマット: [{"start": 0.0, "end": 5.0, "text": "テキスト"}] '
    'JSONのみ出力してください。'
)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".mxf", ".m4v", ".webm"}

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bulk_runner.log", encoding="utf-8"),
    ]
)
log = logging.getLogger("bulk_runner")


# ============================================================
# Google Drive
# ============================================================

def get_drive_service(sa_key_path):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        sa_key_path, scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds)


def list_videos_recursive(service, folder_id):
    """再帰的に全動画ファイルを取得"""
    results = []
    # 動画取得
    query = f"'{folder_id}' in parents and trashed = false"
    mime_conds = " or ".join(
        f"mimeType = 'video/{ext.strip('.')}'" for ext in
        ["mp4", "quicktime", "x-msvideo", "x-matroska", "webm", "x-m4v"]
    )
    vq = f"{query} and ({mime_conds})"
    page_token = None
    while True:
        resp = service.files().list(
            q=vq, spaces="drive", pageToken=page_token,
            fields="nextPageToken, files(id, name, mimeType, size, parents)",
            pageSize=100, supportsAllDrives=True, includeItemsFromAllDrives=True
        ).execute()
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    # サブフォルダ再帰
    fq = f"'{folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    resp = service.files().list(
        q=fq, spaces="drive", fields="files(id, name)",
        pageSize=100, supportsAllDrives=True, includeItemsFromAllDrives=True
    ).execute()
    for sub in resp.get("files", []):
        time.sleep(0.3)
        results.extend(list_videos_recursive(service, sub["id"]))

    return results


def get_folder_path(service, folder_id):
    parts = []
    current = folder_id
    for _ in range(10):
        try:
            meta = service.files().get(
                fileId=current, fields="name,parents",
                supportsAllDrives=True).execute()
            parts.insert(0, meta["name"])
            parents = meta.get("parents", [])
            current = parents[0] if parents else None
            if not current:
                break
        except Exception:
            break
    return "/".join(parts)


def download_file(service, file_id, output_path):
    from googleapiclient.http import MediaIoBaseDownload
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(output_path, "wb") as f:
        dl = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = dl.next_chunk()


def upload_file(service, local_path, parent_folder_id, file_name=None):
    from googleapiclient.http import MediaFileUpload
    if file_name is None:
        file_name = Path(local_path).name
    existing = service.files().list(
        q=f"'{parent_folder_id}' in parents and name = '{file_name}' and trashed = false",
        fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True
    ).execute().get("files", [])
    media = MediaFileUpload(str(local_path))
    if existing:
        service.files().update(fileId=existing[0]["id"], media_body=media,
                               supportsAllDrives=True).execute()
    else:
        service.files().create(
            body={"name": file_name, "parents": [parent_folder_id]},
            media_body=media, fields="id", supportsAllDrives=True).execute()


# ============================================================
# FFmpeg
# ============================================================

def extract_metadata(video_path):
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
           "-show_format", "-show_streams", str(video_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    probe = json.loads(r.stdout)
    vs = next((s for s in probe.get("streams", []) if s["codec_type"] == "video"), None)
    fmt = probe.get("format", {})
    fps = None
    if vs and vs.get("avg_frame_rate"):
        parts = vs["avg_frame_rate"].split("/")
        if len(parts) == 2 and int(parts[1]) > 0:
            fps = round(int(parts[0]) / int(parts[1]), 2)
    res = f"{vs.get('width', '?')}x{vs.get('height', '?')}" if vs else None
    return {
        "duration_sec": float(fmt.get("duration", 0)),
        "resolution": res,
        "fps": fps,
        "codec": vs.get("codec_name") if vs else None,
        "file_size_bytes": int(fmt.get("size", 0)),
    }


def extract_audio(video_path, output_path=None):
    if output_path is None:
        output_path = str(video_path) + ".wav"
    cmd = ["ffmpeg", "-y", "-i", str(video_path),
           "-ar", "16000", "-ac", "1", "-vn", str(output_path)]
    subprocess.run(cmd, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
    return output_path


def extract_thumbnail_base64(video_path, timestamp_sec):
    import base64
    cmd = ["ffmpeg", "-y", "-ss", str(max(0, timestamp_sec)),
           "-i", str(video_path), "-vframes", "1",
           "-vf", "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2",
           "-f", "image2", "-q:v", "5", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    return base64.b64encode(r.stdout).decode("ascii")


def check_has_speech(audio_path):
    """音声にスピーチが含まれるか簡易チェック（音量ベース）"""
    cmd = ["ffmpeg", "-i", str(audio_path), "-af",
           "silencedetect=n=-30dB:d=1", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    stderr = r.stderr or ""
    silence_count = stderr.count("silence_end")
    # 無音区間が多い = スピーチが少ない
    # duration取得
    dur_cmd = ["ffprobe", "-v", "quiet", "-show_entries",
               "format=duration", "-of", "json", str(audio_path)]
    dr = subprocess.run(dur_cmd, capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    try:
        dur = float(json.loads(dr.stdout)["format"]["duration"])
    except Exception:
        dur = 0
    # 10秒未満で無音区間0 = 環境音のみの可能性高い
    if dur < 5 and silence_count == 0:
        return False
    return True


def seconds_to_tc(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def save_srt(segments, output_path):
    lines = []
    for i, seg in enumerate(segments, 1):
        st = seg["start"]
        en = seg["end"]
        h1, m1, s1 = int(st//3600), int((st%3600)//60), st%60
        h2, m2, s2 = int(en//3600), int((en%3600)//60), en%60
        lines.append(str(i))
        lines.append(f"{h1:02d}:{m1:02d}:{s1:06.3f} --> {h2:02d}:{m2:02d}:{s2:06.3f}".replace(".", ","))
        lines.append(seg["text"])
        lines.append("")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ============================================================
# Gemini
# ============================================================

def transcribe(audio_path, gemini_client, max_retries=3):
    for attempt in range(max_retries):
        try:
            import shutil, uuid
            ascii_path = "/tmp/" + str(uuid.uuid4()) + ".wav"
            shutil.copy2(audio_path, ascii_path)
            audio_file = gemini_client.files.upload(file=ascii_path)
            try:
                os.remove(ascii_path)
            except Exception:
                pass
            resp = gemini_client.models.generate_content(
                model=GEMINI_MODEL, contents=[PROMPT, audio_file])
            text = resp.text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-z]*\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            jm = re.search(r"\[.*\]", text, re.DOTALL)
            if jm:
                return json.loads(jm.group())
            return []
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 10 * (attempt + 1)
                log.warning(f"Transcribe retry {attempt+1}: {e}, waiting {wait}s")
                time.sleep(wait)
            else:
                raise


def embed_texts(texts, gemini_client, batch_size=50):
    embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        for text in batch:
            try:
                result = gemini_client.models.embed_content(
                    model="gemini-embedding-001", contents=text)
                embeddings.append(list(result.embeddings[0].values))
            except Exception as e:
                log.warning(f"Embed error: {e}")
                embeddings.append(None)
                time.sleep(2)
        time.sleep(0.5)  # Rate limit
    return embeddings


# ============================================================
# 1動画の処理
# ============================================================

def process_one(video_info, drive_service, gemini_client, db_pool,
                folder_path, tmp_dir):
    fname = video_info["name"]
    fid = video_info["id"]
    parent_id = video_info.get("parents", [None])[0]

    local_path = Path(tmp_dir) / fname
    audio_path = None

    try:
        # Download
        download_file(drive_service, fid, str(local_path))

        # Metadata
        meta = extract_metadata(local_path)

        # Audio
        audio_path = extract_audio(local_path)

        # Quick speech check
        if not check_has_speech(audio_path):
            log.info(f"  NO SPEECH (skipped): {fname}")
            return {"status": "no_speech", "file": fname}

        # Transcribe
        segments = transcribe(audio_path, gemini_client)
        if not segments:
            log.info(f"  EMPTY: {fname}")
            return {"status": "empty", "file": fname}

        # SRT + upload
        srt_path = Path(tmp_dir) / f"{Path(fname).stem}_transcript.srt"
        save_srt(segments, srt_path)
        if parent_id:
            try:
                upload_file(drive_service, str(srt_path), parent_id)
            except Exception:
                pass

        # Embed
        texts = [s["text"] for s in segments if s.get("text") and len(s["text"]) > 1]
        embeddings = embed_texts(texts, gemini_client) if texts else []

        # Thumbnails
        thumbs = []
        for seg in segments:
            b64 = extract_thumbnail_base64(str(local_path), max(0, seg.get("start", 0)))
            thumbs.append(b64)

        # DB
        conn = db_pool.getconn()
        try:
            register_vector(conn)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO media_assets
                    (file_path, file_name, drive_id, drive_folder_path, source_type,
                     duration_sec, resolution, fps, codec, file_size_bytes)
                VALUES (%s,%s,%s,%s,'original',%s,%s,%s,%s,%s) RETURNING id
            """, (str(local_path), fname, fid, folder_path,
                  meta["duration_sec"], meta["resolution"], meta["fps"],
                  meta["codec"], meta["file_size_bytes"]))
            asset_id = cur.fetchone()[0]

            emb_idx = 0
            for i, seg in enumerate(segments):
                if not seg.get("text") or len(seg["text"]) < 2:
                    continue
                emb = embeddings[emb_idx] if emb_idx < len(embeddings) else None
                emb_idx += 1
                thumb = thumbs[i] if i < len(thumbs) else None
                cur.execute("""
                    INSERT INTO transcripts
                        (asset_id, text, text_embedding, thumbnail_base64,
                         start_tc, end_tc, start_sec, end_sec)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (str(asset_id), seg["text"], emb, thumb,
                      seconds_to_tc(seg["start"]), seconds_to_tc(seg["end"]),
                      seg["start"], seg["end"]))

            conn.commit()
        finally:
            db_pool.putconn(conn)

        return {"status": "ok", "file": fname, "segs": len(segments),
                "dur": meta["duration_sec"]}

    except Exception as e:
        log.error(f"  ERROR: {fname}: {e}")
        return {"status": "error", "file": fname, "error": str(e)}

    finally:
        if local_path.exists():
            local_path.unlink()
        if audio_path and Path(audio_path).exists():
            Path(audio_path).unlink()
        for f in Path(tmp_dir).glob(f"{Path(fname).stem}_transcript*"):
            f.unlink()


# ============================================================
# メイン
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Direction Bulk Video Processor")
    parser.add_argument("--folder-ids", required=True,
                        help="Comma-separated Google Drive folder IDs")
    parser.add_argument("--sa-key", default=os.environ.get("GOOGLE_SERVICE_ACCOUNT_KEY_PATH", ""),
                        help="Service account key path")
    parser.add_argument("--workers", type=int, default=3,
                        help="Number of parallel workers (default: 3)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip already-processed videos")
    args = parser.parse_args()

    folder_ids = [fid.strip() for fid in args.folder_ids.split(",")]

    # Init
    from google import genai
    gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    drive_service = get_drive_service(args.sa_key)

    db_pool = ThreadedConnectionPool(1, args.workers + 2, DB_URL)

    # Get processed IDs
    processed_ids = set()
    if args.resume:
        conn = db_pool.getconn()
        cur = conn.cursor()
        cur.execute("SELECT drive_id FROM media_assets WHERE drive_id IS NOT NULL")
        processed_ids = set(r[0] for r in cur.fetchall())
        db_pool.putconn(conn)
        log.info(f"Resume mode: {len(processed_ids)} already processed")

    # Collect all videos
    log.info(f"Scanning {len(folder_ids)} folders...")
    all_videos = []
    for i, fid in enumerate(folder_ids, 1):
        log.info(f"  Scanning folder {i}/{len(folder_ids)}: {fid}")
        videos = list_videos_recursive(drive_service, fid)
        folder_path = get_folder_path(drive_service, fid)
        for v in videos:
            v["_folder_path"] = folder_path
        all_videos.extend(videos)
        time.sleep(1)

    # Filter
    remaining = [v for v in all_videos if v["id"] not in processed_ids]
    remaining.sort(key=lambda x: int(x.get("size", 0)))

    total_gb = sum(int(v.get("size", 0)) for v in remaining) / (1024**3)
    log.info(f"Total: {len(all_videos)}, Remaining: {len(remaining)} ({total_gb:.1f}GB)")
    log.info(f"Workers: {args.workers}")
    log.info(f"Model: {GEMINI_MODEL}")

    # Process
    tmp_dir = tempfile.mkdtemp(prefix="direction_bulk_")
    start_time = datetime.now()
    stats = {"ok": 0, "error": 0, "no_speech": 0, "empty": 0}

    # 並列実行ではなく、Gemini APIのレートリミットを考慮して
    # ワーカー数を制限した逐次+並列ハイブリッド
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for i, video in enumerate(remaining):
            # ワーカーごとに専用tmpディレクトリ
            worker_tmp = Path(tmp_dir) / f"worker_{i % args.workers}"
            worker_tmp.mkdir(exist_ok=True)

            future = executor.submit(
                process_one, video, drive_service, gemini_client,
                db_pool, video["_folder_path"], str(worker_tmp))
            futures[future] = (i, video["name"])

            # Gemini APIレートリミット回避
            time.sleep(2)

        for future in as_completed(futures):
            idx, fname = futures[future]
            try:
                result = future.result()
                status = result["status"]
                stats[status] = stats.get(status, 0) + 1

                elapsed = (datetime.now() - start_time).total_seconds()
                done = sum(stats.values())
                eta_min = (elapsed / done * (len(remaining) - done)) / 60 if done else 0

                if status == "ok":
                    log.info(f"[{done}/{len(remaining)}] OK: {fname} "
                             f"({result.get('segs', 0)} segs) "
                             f"ETA: {eta_min:.0f}min")
                elif status == "error":
                    log.error(f"[{done}/{len(remaining)}] ERROR: {fname}: "
                              f"{result.get('error', '?')[:80]}")
                else:
                    log.info(f"[{done}/{len(remaining)}] {status.upper()}: {fname}")

            except Exception as e:
                stats["error"] += 1
                log.error(f"Future error: {fname}: {e}")

    # Summary
    elapsed = (datetime.now() - start_time).total_seconds()
    log.info("=" * 60)
    log.info(f"COMPLETE in {elapsed/3600:.1f} hours")
    log.info(f"  OK: {stats.get('ok', 0)}")
    log.info(f"  No speech: {stats.get('no_speech', 0)}")
    log.info(f"  Empty: {stats.get('empty', 0)}")
    log.info(f"  Errors: {stats.get('error', 0)}")
    log.info("=" * 60)

    db_pool.closeall()


if __name__ == "__main__":
    main()
