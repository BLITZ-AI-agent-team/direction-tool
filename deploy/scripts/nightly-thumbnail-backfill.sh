#!/bin/bash
# Direction Module 6 - Nightly thumbnail backfill wrapper
#
# 呼び出し元: /etc/cron.d/direction-thumbnail-backfill
# 目的: 環境変数のロードと仮想環境有効化をラップし、cron行を簡潔に保つ
#
# 前提ファイル（VPS側）:
#   - /root/direction-env/          venv
#   - /root/gemini_key.txt          Gemini API key (1行)
#   - /root/dburl.txt               DATABASE_URL (1行、末尾改行OK)
#   - /root/sa-key.json             Google Drive service account
#   - /root/retry_failed_thumbnails.py  backfill スクリプト本体

set -eo pipefail

# ログ用タイムスタンプ
echo ""
echo "================================================"
echo "Nightly thumbnail backfill start: $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"

cd /root

# 仮想環境
# shellcheck disable=SC1091
source /root/direction-env/bin/activate

# 認証情報ロード（ファイル経由でプロセスリストに出ないようにする）
GEMINI_API_KEY=$(cat /root/gemini_key.txt)
DATABASE_URL=$(tr -d '\n ' < /root/dburl.txt)
export GEMINI_API_KEY DATABASE_URL

# 4時間タイムアウト（--limit 80 なら十分、業務開始前に必ず終わる）
# workers 1 で低メモリ運用（2だと昨夜のcron失敗時にメモリ枯渇とtimeout重複で全滅した）
# --limit 80 で1回80動画に絞り、5晩程度で337動画を消化
timeout 4h python3 /root/retry_failed_thumbnails.py \
    --sa-key /root/sa-key.json \
    --workers 1 \
    --limit 80

exit_code=$?
echo "Nightly thumbnail backfill end: $(date '+%Y-%m-%d %H:%M:%S') (exit=${exit_code})"
exit "${exit_code}"
