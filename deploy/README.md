# Direction - VPS デプロイ手順

このディレクトリはVPS（Hostinger KVM4, `root@72.60.192.145`）上のcron/logrotate設定を管理します。

## ディレクトリ構成

```
deploy/
├── README.md                                       # このファイル
├── scripts/
│   └── nightly-thumbnail-backfill.sh               # cron から呼ばれるラッパー
├── cron/
│   └── direction-thumbnail-backfill                # /etc/cron.d/ に配置
└── logrotate/
    └── direction-thumbnail                         # /etc/logrotate.d/ に配置
```

## 夜間サムネイル補完バッチのセットアップ

### 事前確認（VPS で実行）

```bash
ssh root@72.60.192.145
ls -la /root/retry_failed_thumbnails.py \
       /root/sa-key.json \
       /root/gemini_key.txt \
       /root/dburl.txt \
       /root/direction-env/bin/activate
which flock logrotate timeout python3
ls /etc/cron.d/ /etc/logrotate.d/ | head
```

全部存在していればセットアップ可能です。

### デプロイ手順（VPS で実行）

```bash
# リポジトリを最新に（/root にクローン済みの前提）
cd /root/direction-repo   # 実際のクローンパスに読み替え
git pull origin main

# 1) 最新の retry_failed_thumbnails.py を反映
cp src/module6/scripts/retry_failed_thumbnails.py /root/retry_failed_thumbnails.py

# 2) ラッパースクリプトを配置
cp deploy/scripts/nightly-thumbnail-backfill.sh /root/nightly-thumbnail-backfill.sh
chmod 755 /root/nightly-thumbnail-backfill.sh

# 3) cron設定を配置
cp deploy/cron/direction-thumbnail-backfill /etc/cron.d/direction-thumbnail-backfill
chmod 644 /etc/cron.d/direction-thumbnail-backfill
chown root:root /etc/cron.d/direction-thumbnail-backfill

# 4) logrotate設定を配置
cp deploy/logrotate/direction-thumbnail /etc/logrotate.d/direction-thumbnail
chmod 644 /etc/logrotate.d/direction-thumbnail
chown root:root /etc/logrotate.d/direction-thumbnail

# 5) cron再読込（Debian/Ubuntu系）
systemctl reload cron || service cron reload

# 6) 動作確認
crontab -u root -l          # user cron (この設定には出ない)
cat /etc/cron.d/direction-thumbnail-backfill
logrotate -d /etc/logrotate.d/direction-thumbnail   # dry-run (設定チェック)
```

### 手動テスト実行

本番 cron を待たずに試したい場合:

```bash
# ラッパーをフォアグラウンド実行（ログは画面と /root/nightly-thumb.log の両方へ）
/root/nightly-thumbnail-backfill.sh 2>&1 | tee -a /root/nightly-thumb.log
```

短時間で試したいときは `retry_failed_thumbnails.py` に `--limit 3` 等を追加:

```bash
timeout 10m python3 /root/retry_failed_thumbnails.py \
    --sa-key /root/sa-key.json --workers 2 --limit 3
```

### 排他制御の仕組み

cron 行の `flock -n /var/lock/direction-thumb.lock` により、前夜の処理が5時間で終わらず翌朝2時に残っていた場合、新規起動はスキップされます（ログに `... Resource temporarily unavailable` が出ない場合は無言でskip）。

### 停止したい場合

```bash
rm /etc/cron.d/direction-thumbnail-backfill
systemctl reload cron
```

`/root/nightly-thumbnail-backfill.sh` とログファイルは残して問題ありません。

## 想定ログ出力例

```
================================================
Nightly thumbnail backfill start: 2026-04-24 02:00:01
================================================
2026-04-24 02:00:03 [INFO] Failed assets to retry: 337
2026-04-24 02:00:04 [INFO] Retrying 337 assets with 3,062 missing thumbnails
...
2026-04-24 06:45:12 [INFO] RETRY COMPLETE in 4.75 hours
Nightly thumbnail backfill end: 2026-04-24 06:45:12 (exit=0)
```

## 運用上の注意

- **VPS時刻**: Hostinger VPS はデフォルトUTC。JST 11:00相当。業務開始前には十分終わる想定。
- **DB容量**: Neon 512MB の50%以下で運用中。サムネは VPS ファイルのため、DB増加は `thumbnail_path` カラムのみ。
- **API コスト**: Gemini embedding/transcribe は使用しません（既存セグメントのサムネ補完のみ）。Google Drive DL と FFmpeg のみのため追加コスト0。
- **失敗の扱い**: DL/抽出失敗はログに記録されますが、次回リトライで再挑戦されます。恒久的に失敗する動画は人手で確認。
