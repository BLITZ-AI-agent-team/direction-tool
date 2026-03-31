"""
Direction Module 6 - 過去動画素材の掘り起こしパイプライン

Google Drive上の動画素材を自動で:
1. 音声抽出（FFmpeg）
2. VFR→CFR変換
3. 文字起こし（kotoba-whisper / OpenAI API）
4. ベクトル化（OpenAI Embedding）
5. pgvector DB登録
6. SRT/JSONファイル生成 → Google Driveに保存
"""

import os
import sys
import json
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv

# Add parent paths
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from shared.db.client import DirectionDB

load_dotenv(Path(__file__).resolve().parent.parent / "config" / ".env")


# ============================================================
# FFmpeg: メタデータ抽出
# ============================================================
def extract_metadata(video_path):
    """FFprobeで動画メタデータを取得"""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    probe = json.loads(result.stdout)
    video_stream = next(
        (s for s in probe.get("streams", []) if s["codec_type"] == "video"), None
    )
    audio_stream = next(
        (s for s in probe.get("streams", []) if s["codec_type"] == "audio"), None
    )
    fmt = probe.get("format", {})

    # VFR検出
    is_vfr = False
    if video_stream:
        r_frame_rate = video_stream.get("r_frame_rate", "0/1")
        avg_frame_rate = video_stream.get("avg_frame_rate", "0/1")
        if r_frame_rate != avg_frame_rate:
            is_vfr = True

    fps = None
    if video_stream and video_stream.get("avg_frame_rate"):
        parts = video_stream["avg_frame_rate"].split("/")
        if len(parts) == 2 and int(parts[1]) > 0:
            fps = round(int(parts[0]) / int(parts[1]), 2)

    resolution = None
    if video_stream:
        resolution = f"{video_stream.get('width', '?')}x{video_stream.get('height', '?')}"

    return {
        "duration_sec": float(fmt.get("duration", 0)),
        "resolution": resolution,
        "fps": fps,
        "codec": video_stream.get("codec_name") if video_stream else None,
        "file_size_bytes": int(fmt.get("size", 0)),
        "is_vfr": is_vfr,
        "has_audio": audio_stream is not None,
    }


# ============================================================
# FFmpeg: VFR→CFR変換
# ============================================================
def convert_vfr_to_cfr(video_path, output_path=None):
    """VFR動画をCFR（固定フレームレート）に変換"""
    if output_path is None:
        stem = Path(video_path).stem
        output_path = Path(video_path).parent / f"{stem}_cfr.mp4"

    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vsync", "cfr",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "copy",
        str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"VFR→CFR conversion failed: {result.stderr[:500]}")
    return str(output_path)


# ============================================================
# FFmpeg: 音声抽出
# ============================================================
def extract_audio(video_path, output_path=None, sample_rate=16000):
    """動画から音声を抽出（16kHz/モノラル/WAV）"""
    if output_path is None:
        output_path = Path(tempfile.mkdtemp()) / "audio.wav"

    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-ar", str(sample_rate),
        "-ac", "1",
        "-vn",
        str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Audio extraction failed: {result.stderr[:500]}")
    return str(output_path)


# ============================================================
# 文字起こし: kotoba-whisper (ローカル)
# ============================================================
def transcribe_local(audio_path):
    """kotoba-whisper v2.2でローカル文字起こし"""
    try:
        import torch
        from transformers import pipeline

        model_id = os.getenv("WHISPER_MODEL", "kotoba-tech/kotoba-whisper-v2.2")
        device = os.getenv("WHISPER_DEVICE", "cpu")
        compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

        torch_dtype = torch.float32
        if device == "cuda":
            torch_dtype = torch.float16

        pipe = pipeline(
            "automatic-speech-recognition",
            model=model_id,
            torch_dtype=torch_dtype,
            device=device,
        )

        result = pipe(
            audio_path,
            return_timestamps=True,
            generate_kwargs={"language": "ja", "task": "transcribe"},
        )

        segments = []
        for chunk in result.get("chunks", []):
            ts = chunk.get("timestamp", (0, 0))
            segments.append({
                "start": ts[0] if ts[0] is not None else 0,
                "end": ts[1] if ts[1] is not None else 0,
                "text": chunk.get("text", "").strip(),
            })

        return segments

    except Exception as e:
        print(f"[WARN] Local whisper failed: {e}, falling back to API")
        return None


# ============================================================
# 文字起こし: OpenAI Whisper API (フォールバック)
# ============================================================
def transcribe_api(audio_path):
    """OpenAI Whisper APIで文字起こし"""
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # 25MB制限チェック
    file_size = os.path.getsize(audio_path)
    if file_size > 25 * 1024 * 1024:
        return transcribe_api_chunked(audio_path, client)

    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="ja",
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    segments = []
    for seg in response.segments:
        segments.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        })

    return segments


def transcribe_api_chunked(audio_path, client, chunk_duration=600):
    """25MB超の音声を分割してAPI文字起こし"""
    chunks_dir = Path(tempfile.mkdtemp())
    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
        "-f", "segment", "-segment_time", str(chunk_duration),
        "-ar", "16000", "-ac", "1",
        str(chunks_dir / "chunk_%03d.wav")
    ]
    subprocess.run(cmd, capture_output=True, text=True)

    all_segments = []
    offset = 0.0

    for chunk_file in sorted(chunks_dir.glob("chunk_*.wav")):
        with open(chunk_file, "rb") as f:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ja",
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        for seg in response.segments:
            all_segments.append({
                "start": seg.start + offset,
                "end": seg.end + offset,
                "text": seg.text.strip(),
            })

        # チャンクの実際の長さを取得
        probe_cmd = ["ffprobe", "-v", "quiet", "-show_entries",
                     "format=duration", "-of", "json", str(chunk_file)]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        chunk_info = json.loads(probe_result.stdout)
        offset += float(chunk_info["format"]["duration"])

        chunk_file.unlink()

    return all_segments


# ============================================================
# 文字起こし統合
# ============================================================
def transcribe(audio_path):
    """kotoba-whisperを試行→失敗時にOpenAI APIフォールバック"""
    segments = transcribe_local(audio_path)
    if segments is None:
        segments = transcribe_api(audio_path)
    return segments


# ============================================================
# タイムコード変換
# ============================================================
def seconds_to_tc(seconds):
    """秒をHH:MM:SS.mmm形式に変換"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def seconds_to_srt_tc(seconds):
    """秒をSRT形式（HH:MM:SS,mmm）に変換"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ============================================================
# SRT/JSON出力
# ============================================================
def save_srt(segments, output_path):
    """セグメントリストをSRTファイルに保存"""
    lines = []
    for i, seg in enumerate(segments, 1):
        start_tc = seconds_to_srt_tc(seg["start"])
        end_tc = seconds_to_srt_tc(seg["end"])
        lines.append(f"{i}")
        lines.append(f"{start_tc} --> {end_tc}")
        lines.append(seg["text"])
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return output_path


def save_transcript_json(segments, metadata, output_path):
    """セグメントリストをJSONファイルに保存"""
    data = {
        "metadata": metadata,
        "segments": [
            {
                "index": i,
                "start": seg["start"],
                "end": seg["end"],
                "start_tc": seconds_to_tc(seg["start"]),
                "end_tc": seconds_to_tc(seg["end"]),
                "text": seg["text"],
            }
            for i, seg in enumerate(segments, 1)
        ],
        "generated_at": datetime.now().isoformat(),
        "engine": "kotoba-whisper-v2.2",
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return output_path


# ============================================================
# OpenAI Embedding
# ============================================================
def get_embedding(text):
    """OpenAI text-embedding-3-smallでテキストをベクトル化"""
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return response.data[0].embedding


def get_embeddings_batch(texts, batch_size=100):
    """バッチでベクトル化"""
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=batch,
        )
        all_embeddings.extend([d.embedding for d in response.data])

    return all_embeddings


# ============================================================
# メインパイプライン
# ============================================================
def process_video(video_path, drive_id=None, drive_folder_path=None,
                  output_dir=None, db=None):
    """
    単一動画ファイルの完全処理パイプライン

    1. メタデータ抽出
    2. VFR→CFR変換（必要な場合）
    3. 音声抽出
    4. 文字起こし
    5. SRT/JSON出力
    6. ベクトル化
    7. DB登録
    """
    video_path = Path(video_path)
    file_name = video_path.name

    if output_dir is None:
        output_dir = video_path.parent
    output_dir = Path(output_dir)

    close_db = False
    if db is None:
        db = DirectionDB()
        db.connect()
        close_db = True

    try:
        print(f"[1/7] メタデータ抽出: {file_name}")
        metadata = extract_metadata(video_path)

        # DB登録（重複チェック）
        existing = None
        if drive_id:
            existing = db.find_asset_by_drive_id(drive_id)
        if not existing:
            existing = db.find_asset_by_path(str(video_path))

        if existing:
            asset_id = existing["id"]
            if db.is_processed(asset_id, "transcribe"):
                print(f"[SKIP] Already processed: {file_name}")
                return {"status": "skipped", "asset_id": str(asset_id)}
        else:
            asset = db.upsert_media_asset(
                file_path=str(video_path),
                file_name=file_name,
                drive_id=drive_id,
                drive_folder_path=drive_folder_path,
                source_type="original",
                duration_sec=metadata["duration_sec"],
                resolution=metadata["resolution"],
                fps=metadata["fps"],
                codec=metadata["codec"],
                file_size_bytes=metadata["file_size_bytes"],
                cfr_converted=False,
            )
            asset_id = asset["id"]

        db.set_processing(asset_id, "transcribe")

        # VFR対策
        process_path = video_path
        print(f"[2/7] VFR検出: {'VFR' if metadata['is_vfr'] else 'CFR'}")
        if metadata["is_vfr"]:
            print(f"  → CFR変換実行中...")
            process_path = Path(convert_vfr_to_cfr(video_path))

        # 音声抽出
        print(f"[3/7] 音声抽出")
        audio_path = extract_audio(process_path)

        # 文字起こし
        print(f"[4/7] 文字起こし（kotoba-whisper → API fallback）")
        segments = transcribe(audio_path)
        print(f"  → {len(segments)} セグメント検出")

        # SRT/JSON出力
        print(f"[5/7] SRT/JSON出力")
        stem = video_path.stem
        srt_path = output_dir / f"{stem}_transcript.srt"
        json_path = output_dir / f"{stem}_transcript.json"

        save_srt(segments, srt_path)
        save_transcript_json(segments, {
            "file_name": file_name,
            "duration_sec": metadata["duration_sec"],
            "resolution": metadata["resolution"],
            "fps": metadata["fps"],
        }, json_path)

        # ベクトル化
        print(f"[6/7] テキストベクトル化")
        texts = [seg["text"] for seg in segments if seg["text"]]
        embeddings = []
        if texts:
            embeddings = get_embeddings_batch(texts)

        # DB登録
        print(f"[7/7] DB登録")
        emb_idx = 0
        for seg in segments:
            if not seg["text"]:
                continue

            embedding = embeddings[emb_idx] if emb_idx < len(embeddings) else None
            emb_idx += 1

            db.insert_transcript(
                asset_id=asset_id,
                text=seg["text"],
                start_tc=seconds_to_tc(seg["start"]),
                end_tc=seconds_to_tc(seg["end"]),
                start_sec=seg["start"],
                end_sec=seg["end"],
                text_embedding=embedding,
            )

        db.set_completed(asset_id, "transcribe", result_ref=str(json_path))

        # 一時ファイルクリーンアップ
        if Path(audio_path).exists():
            Path(audio_path).unlink()
        if metadata["is_vfr"] and process_path != video_path and process_path.exists():
            process_path.unlink()

        print(f"[DONE] {file_name}: {len(segments)} segments indexed")

        return {
            "status": "completed",
            "asset_id": str(asset_id),
            "segments_count": len(segments),
            "srt_path": str(srt_path),
            "json_path": str(json_path),
        }

    except Exception as e:
        if existing or asset_id:
            db.set_failed(asset_id, "transcribe", error_message=str(e))
        raise

    finally:
        if close_db:
            db.close()


# ============================================================
# バッチ処理
# ============================================================
def process_directory(directory, db=None):
    """ディレクトリ内の全動画ファイルをバッチ処理"""
    video_extensions = {".mp4", ".mov", ".avi", ".mkv", ".mxf", ".m4v", ".webm"}
    directory = Path(directory)

    video_files = [
        f for f in directory.rglob("*")
        if f.suffix.lower() in video_extensions
        and "_transcript" not in f.stem
        and "_cfr" not in f.stem
    ]

    print(f"Found {len(video_files)} video files in {directory}")

    results = []
    for i, vf in enumerate(video_files, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(video_files)}] Processing: {vf.name}")
        print(f"{'='*60}")
        try:
            result = process_video(vf, db=db)
            results.append(result)
        except Exception as e:
            print(f"[ERROR] {vf.name}: {e}")
            results.append({"status": "error", "file": str(vf), "error": str(e)})

    return results


# ============================================================
# CLI エントリーポイント
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Direction Module 6 - Video Indexing Pipeline")
    parser.add_argument("input", help="Video file or directory path")
    parser.add_argument("--output-dir", help="Output directory for SRT/JSON files")
    parser.add_argument("--drive-id", help="Google Drive file ID")
    parser.add_argument("--drive-folder", help="Google Drive folder path")
    parser.add_argument("--init-db", action="store_true", help="Initialize database schema")
    args = parser.parse_args()

    db = DirectionDB()
    db.connect()

    if args.init_db:
        print("Initializing database schema...")
        db.init_schema()
        print("Done.")

    input_path = Path(args.input)

    if input_path.is_dir():
        results = process_directory(input_path, db=db)
    elif input_path.is_file():
        result = process_video(
            input_path,
            drive_id=args.drive_id,
            drive_folder_path=args.drive_folder,
            output_dir=args.output_dir,
            db=db,
        )
        results = [result]
    else:
        print(f"Error: {input_path} not found")
        sys.exit(1)

    db.close()

    # サマリー
    completed = sum(1 for r in results if r.get("status") == "completed")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    errors = sum(1 for r in results if r.get("status") == "error")
    print(f"\n{'='*60}")
    print(f"Summary: {completed} completed, {skipped} skipped, {errors} errors")
    print(f"{'='*60}")
