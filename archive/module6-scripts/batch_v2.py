"""
Direction Module 6 - バッチ処理 v2
gemini-3.1-flash-lite-preview + サムネイル同時生成
"""

import os, sys, json, re, tempfile, psycopg2
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pgvector.psycopg2 import register_vector
from module6.scripts.gdrive_sync import GoogleDriveClient
from module6.scripts.pipeline import extract_metadata, extract_audio, seconds_to_tc, save_srt
from module6.scripts.thumbnail import extract_thumbnail_base64
from shared.embedding import get_embeddings_batch
from google import genai

DB_URL = os.environ.get("DATABASE_URL",
    "postgresql://neondb_owner:npg_W7wzN1jJyQXR@ep-lucky-shadow-a1xeitfg-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require")
SA_KEY = os.environ.get("GOOGLE_SERVICE_ACCOUNT_KEY_PATH",
    r"C:\Users\BLITZ121\Downloads\develop\direction\JSON KEY\aiagent-dev-489706-b779fa218f8d.json")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-3.1-flash-lite-preview"

PROMPT = (
    'この音声を日本語で正確に文字起こししてください。'
    '各セグメントの開始秒と終了秒とテキストをJSON配列で返してください。'
    'フォーマット: [{"start": 0.0, "end": 5.0, "text": "テキスト"}] '
    'JSONのみ出力してください。'
)


def process_video(drive, video, folder_path, tmp_dir, gemini, processed_ids):
    fname = video["name"]
    fid = video["id"]
    parent_id = video.get("parents", [None])[0]
    size_mb = int(video.get("size", 0)) / (1024 * 1024)

    if fid in processed_ids:
        return {"status": "skip", "file": fname}

    local_path = Path(tmp_dir) / fname
    try:
        # Download
        drive.download_file(fid, str(local_path))
        meta = extract_metadata(local_path)
        audio_path = extract_audio(local_path)

        # Transcribe
        audio_file = gemini.files.upload(file=audio_path)
        resp = gemini.models.generate_content(
            model=MODEL, contents=[PROMPT, audio_file])
        text = resp.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        jm = re.search(r"\[.*\]", text, re.DOTALL)
        segments = json.loads(jm.group()) if jm else []

        if not segments:
            return {"status": "no_speech", "file": fname}

        # SRT + Drive upload
        stem = Path(fname).stem
        srt_path = Path(tmp_dir) / f"{stem}_transcript.srt"
        save_srt(segments, srt_path)
        if parent_id:
            try:
                drive.upload_transcripts(str(srt_path), None, parent_id)
            except:
                pass

        # Embed
        texts = [s["text"] for s in segments if s.get("text") and len(s["text"]) > 1]
        embeddings = get_embeddings_batch(texts) if texts else []

        # Thumbnails
        thumbs = []
        for seg in segments:
            start = max(0, seg.get("start", 0))
            b64 = extract_thumbnail_base64(str(local_path), start)
            thumbs.append(b64)

        # DB insert (fresh connection)
        conn = psycopg2.connect(DB_URL)
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
        conn.close()

        return {"status": "ok", "file": fname, "segs": len(segments),
                "dur": meta["duration_sec"]}

    except Exception as e:
        return {"status": "error", "file": fname, "error": str(e)}
    finally:
        if local_path.exists():
            local_path.unlink()
        for f in Path(tmp_dir).glob("audio*"):
            f.unlink()
        for f in Path(tmp_dir).glob("*_transcript*"):
            f.unlink()


def main(folder_id):
    os.environ["GOOGLE_SERVICE_ACCOUNT_KEY_PATH"] = SA_KEY
    drive = GoogleDriveClient()
    drive.connect()
    gemini = genai.Client(api_key=GEMINI_KEY)

    folder_path = drive.get_folder_path(folder_id)
    videos = drive.list_videos(folder_id)
    videos.sort(key=lambda x: int(x.get("size", 0)))

    # Check processed
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT drive_id FROM media_assets WHERE drive_id IS NOT NULL")
    processed_ids = set(r[0] for r in cur.fetchall())
    conn.close()

    remaining = [v for v in videos if v["id"] not in processed_ids]
    total_mb = sum(int(v.get("size", 0)) for v in remaining) / (1024 * 1024)

    print(f"Folder: {folder_path}")
    print(f"Total: {len(videos)}, Remaining: {len(remaining)} ({total_mb:.0f}MB)")
    print(f"Model: {MODEL}")

    tmp_dir = tempfile.mkdtemp(prefix="direction_v2_")
    results = []
    start = datetime.now()

    for i, video in enumerate(remaining, 1):
        fname = video["name"]
        size_mb = int(video.get("size", 0)) / (1024 * 1024)
        print(f"\n[{i}/{len(remaining)}] {fname} ({size_mb:.0f}MB)")

        result = process_video(drive, video, folder_path, tmp_dir, gemini, processed_ids)
        results.append(result)

        s = result["status"]
        if s == "ok":
            print(f"  OK ({result['segs']} segs, {result['dur']:.0f}s)")
        elif s == "skip":
            print(f"  SKIP")
        elif s == "no_speech":
            print(f"  NO SPEECH")
        else:
            print(f"  ERROR: {result.get('error', '?')[:80]}")

    elapsed = (datetime.now() - start).total_seconds()
    ok = sum(1 for r in results if r["status"] == "ok")
    err = sum(1 for r in results if r["status"] == "error")
    print(f"\n=== DONE: {ok} ok, {err} errors, {elapsed/60:.1f} min ===")


if __name__ == "__main__":
    folder_id = sys.argv[1] if len(sys.argv) > 1 else "1oQUAIOxDSPO73FhQEwcd5zV90bNDjEHQ"
    main(folder_id)
