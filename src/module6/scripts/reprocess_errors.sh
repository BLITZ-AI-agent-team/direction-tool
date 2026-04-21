#!/bin/bash
# Direction Module 6 - 214エラー動画再処理スクリプト
#
# 使い方（VPSで）:
#   bash /root/reprocess_errors.sh
#
# 実行条件:
#   - backfill_embeddings.py 完了済み
#   - backfill_thumbnails.py 完了済み（retry_failed_thumbnails.pyも完了推奨）
#   - /root/direction-env/ 仮想環境あり
#   - /root/fids.txt にフォルダID一覧
#   - /root/sa-key.json サービスアカウントキー
#
# 動作:
#   1. 事前チェック（DB状態、ファイル存在）
#   2. skip-list初期化（既存があればバックアップ）
#   3. bulk_runner.py を3回連続実行
#      - 1回目: 大半のエラー動画が改修版JSONパースで解消見込み
#      - 2回目: SSL/timeout等の一時エラーをリトライ
#      - 3回目: 残存エラーがskip-list入り（永久スキップ確定）
#   4. 最終結果確認

set -e

echo "========================================"
echo "Direction - 214エラー動画再処理開始"
echo "$(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# 仮想環境
source /root/direction-env/bin/activate

# 環境変数
export GEMINI_API_KEY=$(cat /root/gemini_key.txt)
export DATABASE_URL=$(cat /root/dburl.txt | tr -d '\n' | tr -d ' ')

# ファイル存在確認
for f in /root/bulk_runner.py /root/fids.txt /root/sa-key.json; do
    if [ ! -f "$f" ]; then
        echo "ERROR: $f が見つかりません。先に git pull + cp してください"
        exit 1
    fi
done

# 事前チェック: Embedding/Thumbnail 残件数
echo ""
echo "=== 事前チェック ==="
python3 <<'EOF'
import os, psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM transcripts WHERE text_embedding IS NULL")
emb = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM transcripts WHERE thumbnail_path IS NULL")
thumb = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM media_assets WHERE source_type='original'")
total = cur.fetchone()[0]
print(f"  DB登録済み動画: {total}本")
print(f"  Embedding NULL: {emb}件")
print(f"  Thumbnail NULL: {thumb}件")
if emb > 100:
    print(f"  ⚠️ Embedding NULLが多い。backfill_embeddings.py 完了済みか確認してください")
if thumb > 5000:
    print(f"  ⚠️ Thumbnail NULLが多い。backfill_thumbnails.py 完了確認してから再開推奨")
EOF

echo ""
read -p "続行しますか？ (y/N): " yn
if [[ ! "$yn" =~ ^[Yy]$ ]]; then
    echo "中止しました"
    exit 0
fi

# skip-list 準備
echo ""
echo "=== skip-list 初期化 ==="
if [ -f /root/skipped_videos.json ]; then
    cp /root/skipped_videos.json /root/skipped_videos.json.bak.$(date +%Y%m%d_%H%M%S)
    echo "  既存skip-listをバックアップ"
fi
# skip-list自体はそのまま（過去の累積は維持）

FOLDER_IDS=$(cat /root/fids.txt | tr -d '\n' | tr -d ' ')

# 3回実行ループ
for i in 1 2 3; do
    echo ""
    echo "========================================"
    echo "=== 実行 $i/3 回目 ==="
    echo "$(date '+%Y-%m-%d %H:%M:%S')"
    echo "========================================"

    python3 /root/bulk_runner.py \
        --folder-ids "$FOLDER_IDS" \
        --sa-key /root/sa-key.json \
        --workers 1 \
        --resume

    echo ""
    echo "--- $i 回目完了後の状態 ---"
    python3 <<'EOF'
import os, psycopg2, json
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM media_assets WHERE source_type='original'")
print(f"  DB登録済み: {cur.fetchone()[0]}本")
try:
    with open('/root/skipped_videos.json') as f:
        skip = json.load(f)
    permanent = [d for d,info in skip.items() if info.get('count',0) >= 3]
    print(f"  skip-list記録: {len(skip)}動画")
    print(f"  永久スキップ(3回失敗): {len(permanent)}本")
except Exception as e:
    print(f"  skip-list未作成: {e}")
EOF

    # 次回まで待機（Gemini API レート制限配慮）
    if [ $i -lt 3 ]; then
        echo ""
        echo "  30秒待機してから次の実行..."
        sleep 30
    fi
done

echo ""
echo "========================================"
echo "=== 全実行完了 ==="
echo "$(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# 最終レポート
echo ""
echo "=== 最終結果 ==="
python3 <<'EOF'
import os, psycopg2, json
from collections import Counter
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM media_assets WHERE source_type='original'")
print(f"  DB登録済み動画: {cur.fetchone()[0]}本")

try:
    with open('/root/skipped_videos.json') as f:
        skip = json.load(f)
    permanent = {d:info for d,info in skip.items() if info.get('count',0) >= 3}
    print(f"  永久スキップ動画: {len(permanent)}本")

    # エラー原因別Top10
    if permanent:
        errors = Counter()
        for info in permanent.values():
            msg = info.get('last_error','')[:70]
            errors[msg] += 1
        print(f"\n  === 永久スキップのエラー原因 Top10 ===")
        for msg, cnt in errors.most_common(10):
            print(f"    {cnt:3d}  {msg}")
except Exception as e:
    print(f"  skip-list読み込み失敗: {e}")
EOF

echo ""
echo "完了しました。詳細ログは /root/bulk_runner.log を参照。"
