"""
Direction Module 6 - サムネイル生成

動画の各セグメント（タイムコード）からフレーム画像を抽出し、
Web検索画面で表示するためのサムネイルを生成する。

保存先: Hostingerサーバー上（n8n Webhook経由で配信）
サイズ: 320x180px JPEG（1枚あたり10〜30KB）
"""

import os
import subprocess
import base64
from pathlib import Path


def extract_thumbnail(video_path, timestamp_sec, output_path=None, width=320, height=180):
    """動画の指定タイムコードからサムネイル画像を抽出"""
    if output_path is None:
        stem = Path(video_path).stem
        output_path = Path(video_path).parent / f"{stem}_{timestamp_sec:.1f}.jpg"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp_sec),
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "-q:v", "5",
        str(output_path)
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        return None
    return str(output_path)


def extract_thumbnail_base64(video_path, timestamp_sec, width=320, height=180):
    """動画の指定タイムコードからサムネイルをBase64文字列として取得"""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp_sec),
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "-f", "image2",
        "-q:v", "5",
        "pipe:1"
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        return None
    return base64.b64encode(result.stdout).decode("ascii")


def generate_thumbnails_for_segments(video_path, segments, output_dir=None):
    """全セグメントのサムネイルをBase64で生成"""
    thumbnails = []
    for seg in segments:
        start = seg.get("start", 0)
        if start < 0:
            start = 0
        b64 = extract_thumbnail_base64(video_path, start)
        thumbnails.append({
            "start_sec": start,
            "base64": b64,
            "data_uri": f"data:image/jpeg;base64,{b64}" if b64 else None,
        })
    return thumbnails
