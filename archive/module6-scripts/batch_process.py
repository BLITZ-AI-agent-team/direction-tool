"""
Direction Module 6 - 本番バッチ処理

Google Driveフォルダの全動画を処理:
1. ファイル一覧取得（正しいファイル名・フォルダパス）
2. ダウンロード → 音声抽出 → 文字起こし → ベクトル化 → DB登録
3. SRT/JSONをGoogle Driveにアップロード
"""

import os
import sys
import json
import re
import tempfile
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / "config" / ".env")

from shared.db.client import DirectionDB
from shared.embedding import get_embeddings_batch
from module6.scripts.gdrive_sync import GoogleDriveClient
from module6.scripts.pipeline import (
    extract_metadata, extract_audio, save_srt, save_transcript_json, seconds_to_tc
)
from google import genai


def transcribe_with_gemini(audio_path, api_key):
    """Gemini 3.1 Flash-Lite Previewで文字起こし"""
    client = genai.Client(api_key=api_key)
    audio_file = client.files.upload(file=audio_path)
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-preview",
        contents=[
            'この音声を日本語で正確に文字起こししてください。'
            '各セグメントの開始秒と終了秒とテキストをJSON配列で返してください。'
            'フォーマット: [{"start": 0.0, "end": 5.0, "text": "テキスト"}] '
            'JSONのみ出力してください。',
            audio_file,
        ],
    )
    json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group())
    return [{"start": 0, "end": 0, "text": response.text}]


def process_single_video(drive, db, video_info, tmp_dir, gemini_api_key):
    """1本の動画を完全処理"""
    file_id = video_info["id"]
    file_name = video_info["name"]
    folder_path = video_info.get("folder_path", "")
    parent_id = video_info.get("parents", [None])[0]
    size_mb = int(video_info.get("size", 0)) / (1024 * 1024)

    # 処理済みチェック
    existing = db.find_asset_by_drive_id(file_id)
    if existing and db.is_processed(existing["id"], "transcribe"):
        print(f"  [SKIP] Already processed")
        return {"status": "skipped", "file": file_name}

    local_path = Path(tmp_dir) / file_name

    try:
        # 1. ダウンロード
        print(f"  [1/6] Downloading ({size_mb:.0f}MB)...")
        drive.download_file(file_id, str(local_path))

        # 2. メタデータ
        print(f"  [2/6] Metadata...")
        meta = extract_metadata(local_path)

        # 3. 音声抽出
        print(f"  [3/6] Audio extraction...")
        audio_path = extract_audio(local_path)

        # 4. 文字起こし
        print(f"  [4/6] Transcribing...")
        segments = transcribe_with_gemini(audio_path, gemini_api_key)
        print(f"       {len(segments)} segments")

        # 5. SRT/JSON保存
        print(f"  [5/6] Saving SRT/JSON...")
        stem = Path(file_name).stem
        srt_path = Path(tmp_dir) / f"{stem}_transcript.srt"
        json_path = Path(tmp_dir) / f"{stem}_transcript.json"
        save_srt(segments, srt_path)
        save_transcript_json(segments, {
            "file_name": file_name,
            "duration_sec": meta["duration_sec"],
            "resolution": meta["resolution"],
            "fps": meta["fps"],
            "folder_path": folder_path,
        }, json_path)

        # Google Driveにアップロード
        if parent_id:
            try:
                drive.upload_transcripts(str(srt_path), str(json_path), parent_id)
            except Exception as e:
                print(f"       [WARN] Drive upload failed: {e}")

        # 6. ベクトル化 + DB登録
        print(f"  [6/6] Embedding + DB insert...")
        texts = [s["text"] for s in segments if s.get("text")]
        embeddings = get_embeddings_batch(texts) if texts else []

        # DB登録
        asset = db.upsert_media_asset(
            file_path=str(local_path),
            file_name=file_name,
            drive_id=file_id,
            drive_folder_path=folder_path,
            source_type="original",
            duration_sec=meta["duration_sec"],
            resolution=meta["resolution"],
            fps=meta["fps"],
            codec=meta["codec"],
            file_size_bytes=meta["file_size_bytes"],
        )
        asset_id = asset["id"]
        db.set_processing(asset_id, "transcribe")

        for i, seg in enumerate(segments):
            if not seg.get("text"):
                continue
            emb = embeddings[i] if i < len(embeddings) else None
            db.insert_transcript(
                asset_id=asset_id,
                text=seg["text"],
                start_tc=seconds_to_tc(seg["start"]),
                end_tc=seconds_to_tc(seg["end"]),
                start_sec=seg["start"],
                end_sec=seg["end"],
                text_embedding=emb,
            )

        db.set_completed(asset_id, "transcribe", result_ref=str(json_path))
        print(f"  [OK] {len(segments)} segments indexed")

        return {"status": "completed", "file": file_name, "segments": len(segments)}

    except Exception as e:
        print(f"  [ERROR] {e}")
        return {"status": "error", "file": file_name, "error": str(e)}

    finally:
        # クリーンアップ
        if local_path.exists():
            local_path.unlink()
        audio_wav = Path(tmp_dir) / "audio.wav"
        if audio_wav.exists():
            audio_wav.unlink()


def main():
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "1y9FQPkIf_yOmzv6tSkjCHDYT9zoIdpBb")
    gemini_api_key = os.getenv("GEMINI_API_KEY")

    # 接続
    drive = GoogleDriveClient()
    drive.connect()
    db = DirectionDB()
    db.connect()

    # フォルダパス取得
    folder_path = drive.get_folder_path(folder_id)
    print(f"Folder: {folder_path}")

    # 動画一覧
    videos = drive.list_videos(folder_id)
    videos.sort(key=lambda x: int(x.get("size", 0)))

    print(f"Found {len(videos)} videos")
    total_size = sum(int(v.get("size", 0)) for v in videos) / (1024 * 1024 * 1024)
    print(f"Total size: {total_size:.1f} GB")
    print("=" * 60)

    # フォルダパスを各動画に付与
    for v in videos:
        v["folder_path"] = folder_path

    # 処理
    tmp_dir = tempfile.mkdtemp(prefix="direction_batch_")
    results = []
    start_time = datetime.now()

    for i, video in enumerate(videos, 1):
        file_name = video["name"]
        size_mb = int(video.get("size", 0)) / (1024 * 1024)
        print(f"\n[{i}/{len(videos)}] {file_name} ({size_mb:.0f}MB)")
        print("-" * 60)

        result = process_single_video(drive, db, video, tmp_dir, gemini_api_key)
        results.append(result)

    # サマリー
    elapsed = (datetime.now() - start_time).total_seconds()
    completed = sum(1 for r in results if r.get("status") == "completed")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    errors = sum(1 for r in results if r.get("status") == "error")
    total_segments = sum(r.get("segments", 0) for r in results)

    print(f"\n{'=' * 60}")
    print(f"BATCH COMPLETE")
    print(f"  Total: {len(videos)} videos")
    print(f"  Completed: {completed}")
    print(f"  Skipped: {skipped}")
    print(f"  Errors: {errors}")
    print(f"  Total segments indexed: {total_segments}")
    print(f"  Elapsed: {elapsed / 60:.1f} minutes")
    print(f"{'=' * 60}")

    # エラー詳細
    for r in results:
        if r.get("status") == "error":
            print(f"  ERROR: {r['file']}: {r.get('error', 'unknown')}")

    db.close()


if __name__ == "__main__":
    main()
