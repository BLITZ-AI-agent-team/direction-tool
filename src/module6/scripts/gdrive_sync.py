"""
Direction Module 6 - Google Drive連携

Google Driveとの双方向連携:
1. 新規動画ファイルの検出
2. 文字起こしファイル(SRT/JSON)のアップロード（元動画と同じフォルダに保存）
3. n8n Webhookからのトリガー受信
"""

import os
import sys
import json
import io
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

load_dotenv(Path(__file__).resolve().parent.parent / "config" / ".env")

VIDEO_EXTENSIONS = {
    "video/mp4", "video/quicktime", "video/x-msvideo",
    "video/x-matroska", "video/webm", "video/x-m4v",
}
VIDEO_FILE_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mxf"}


class GoogleDriveClient:
    def __init__(self, key_path=None):
        self.key_path = key_path or os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_PATH")
        self.service = None

    def connect(self):
        credentials = service_account.Credentials.from_service_account_file(
            self.key_path,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        self.service = build("drive", "v3", credentials=credentials)
        return self

    def list_videos(self, folder_id=None, modified_after=None):
        """指定フォルダ内の動画ファイルを一覧取得"""
        if folder_id is None:
            folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")

        query_parts = [f"'{folder_id}' in parents", "trashed = false"]

        mime_conditions = " or ".join(
            f"mimeType = '{mime}'" for mime in VIDEO_EXTENSIONS
        )
        query_parts.append(f"({mime_conditions})")

        if modified_after:
            query_parts.append(f"modifiedTime > '{modified_after}'")

        query = " and ".join(query_parts)

        results = []
        page_token = None

        while True:
            response = self.service.files().list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, parents)",
                pageToken=page_token,
                pageSize=100,
            ).execute()

            results.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return results

    def list_videos_recursive(self, folder_id=None):
        """サブフォルダを含めて再帰的に動画ファイルを一覧取得"""
        if folder_id is None:
            folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")

        all_videos = []

        # 現在のフォルダの動画を取得
        videos = self.list_videos(folder_id)
        folder_path = self.get_folder_path(folder_id)
        for v in videos:
            v["folder_path"] = folder_path
        all_videos.extend(videos)

        # サブフォルダを取得して再帰
        subfolders = self.list_subfolders(folder_id)
        for sf in subfolders:
            sub_videos = self.list_videos_recursive(sf["id"])
            all_videos.extend(sub_videos)

        return all_videos

    def list_subfolders(self, folder_id):
        """サブフォルダ一覧を取得"""
        query = f"'{folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        response = self.service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            pageSize=100,
        ).execute()
        return response.get("files", [])

    def get_folder_path(self, folder_id):
        """フォルダIDからフルパスを取得"""
        parts = []
        current_id = folder_id

        while current_id:
            try:
                file_meta = self.service.files().get(
                    fileId=current_id,
                    fields="id, name, parents"
                ).execute()
                parts.insert(0, file_meta["name"])
                parents = file_meta.get("parents", [])
                current_id = parents[0] if parents else None
            except Exception:
                break

        return "/".join(parts)

    def download_file(self, file_id, output_path):
        """ファイルをダウンロード"""
        request = self.service.files().get_media(fileId=file_id)
        with open(output_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    print(f"  Download: {int(status.progress() * 100)}%")
        return output_path

    def upload_file(self, local_path, parent_folder_id, file_name=None):
        """ファイルをGoogle Driveにアップロード（元動画と同じフォルダに保存）"""
        if file_name is None:
            file_name = Path(local_path).name

        # 同名ファイルが既にあれば更新
        existing = self.service.files().list(
            q=f"'{parent_folder_id}' in parents and name = '{file_name}' and trashed = false",
            fields="files(id)",
        ).execute().get("files", [])

        if existing:
            media = MediaFileUpload(str(local_path))
            result = self.service.files().update(
                fileId=existing[0]["id"],
                media_body=media,
            ).execute()
            return result["id"]
        else:
            file_metadata = {
                "name": file_name,
                "parents": [parent_folder_id],
            }
            media = MediaFileUpload(str(local_path))
            result = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id",
            ).execute()
            return result["id"]

    def upload_transcripts(self, srt_path, json_path, parent_folder_id):
        """文字起こしファイルをGoogle Driveにアップロード"""
        results = {}
        if srt_path and Path(srt_path).exists():
            results["srt_id"] = self.upload_file(srt_path, parent_folder_id)
            print(f"  Uploaded SRT: {Path(srt_path).name}")
        if json_path and Path(json_path).exists():
            results["json_id"] = self.upload_file(json_path, parent_folder_id)
            print(f"  Uploaded JSON: {Path(json_path).name}")
        return results


# ============================================================
# n8n Webhook連携
# ============================================================
def notify_n8n(webhook_url, payload):
    """n8n Webhookに処理結果を通知"""
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except Exception as e:
        print(f"[WARN] n8n notification failed: {e}")
        return None


# ============================================================
# 統合: Google Drive → パイプライン → アップロード
# ============================================================
def process_drive_folder(folder_id=None, local_temp_dir=None, webhook_url=None):
    """
    Google Driveフォルダの動画を検出→ダウンロード→処理→結果アップロード
    """
    from pipeline import process_video

    drive = GoogleDriveClient()
    drive.connect()

    if local_temp_dir is None:
        import tempfile
        local_temp_dir = Path(tempfile.mkdtemp(prefix="direction_"))
    local_temp_dir = Path(local_temp_dir)
    local_temp_dir.mkdir(parents=True, exist_ok=True)

    videos = drive.list_videos_recursive(folder_id)
    print(f"Found {len(videos)} video files in Google Drive")

    results = []

    from shared.db.client import DirectionDB
    db = DirectionDB()
    db.connect()

    for i, video in enumerate(videos, 1):
        file_id = video["id"]
        file_name = video["name"]
        folder_path = video.get("folder_path", "")
        parent_id = video.get("parents", [None])[0]

        print(f"\n[{i}/{len(videos)}] {file_name}")

        # 処理済みチェック
        existing = db.find_asset_by_drive_id(file_id)
        if existing and db.is_processed(existing["id"], "transcribe"):
            print(f"  [SKIP] Already processed")
            results.append({"status": "skipped", "file": file_name})
            continue

        # ダウンロード
        local_path = local_temp_dir / file_name
        print(f"  Downloading...")
        drive.download_file(file_id, str(local_path))

        # パイプライン処理
        try:
            result = process_video(
                local_path,
                drive_id=file_id,
                drive_folder_path=folder_path,
                output_dir=local_temp_dir,
                db=db,
            )

            # SRT/JSONをGoogle Driveにアップロード
            if result.get("status") == "completed" and parent_id:
                print(f"  Uploading transcripts to Drive...")
                drive.upload_transcripts(
                    result.get("srt_path"),
                    result.get("json_path"),
                    parent_id,
                )

            results.append(result)

        except Exception as e:
            print(f"  [ERROR] {e}")
            results.append({"status": "error", "file": file_name, "error": str(e)})

        finally:
            # ローカル一時ファイルクリーンアップ
            if local_path.exists():
                local_path.unlink()

    db.close()

    # n8n通知
    if webhook_url:
        completed = sum(1 for r in results if r.get("status") == "completed")
        errors = sum(1 for r in results if r.get("status") == "error")
        notify_n8n(webhook_url, {
            "event": "module6_batch_complete",
            "total": len(videos),
            "completed": completed,
            "errors": errors,
            "timestamp": datetime.now().isoformat(),
        })

    return results


# ============================================================
# CLI エントリーポイント
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Direction Module 6 - Google Drive Sync")
    parser.add_argument("--folder-id", help="Google Drive folder ID")
    parser.add_argument("--temp-dir", help="Local temp directory for processing")
    parser.add_argument("--webhook", help="n8n webhook URL for notifications")
    parser.add_argument("--list-only", action="store_true", help="List videos without processing")
    args = parser.parse_args()

    drive = GoogleDriveClient()
    drive.connect()

    folder_id = args.folder_id or os.getenv("GOOGLE_DRIVE_FOLDER_ID")

    if args.list_only:
        videos = drive.list_videos_recursive(folder_id)
        print(f"Found {len(videos)} video files:")
        for v in videos:
            size_mb = int(v.get("size", 0)) / (1024 * 1024)
            print(f"  {v['name']} ({size_mb:.1f} MB) - {v.get('folder_path', '')}")
    else:
        results = process_drive_folder(
            folder_id=folder_id,
            local_temp_dir=args.temp_dir,
            webhook_url=args.webhook,
        )
        completed = sum(1 for r in results if r.get("status") == "completed")
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        errors = sum(1 for r in results if r.get("status") == "error")
        print(f"\nSummary: {completed} completed, {skipped} skipped, {errors} errors")
