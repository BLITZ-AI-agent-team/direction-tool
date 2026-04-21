# 214本エラー動画の再処理プラン

最終更新: 2026-04-21

## 前提条件

**このプランは以下の条件が揃ってから実行する:**
- ✅ `backfill_embeddings.py` 完了（`text_embedding IS NULL` が 0件）
- ✅ `backfill_thumbnails.py` 完了（または許容範囲まで処理済み）
- ✅ `retry_failed_thumbnails.py` 完了（DL失敗動画の再試行済み）

理由: サムネ再生成とエラー再処理が並行するとGoogle Drive APIレート制限に抵触する可能性。

---

## 背景

### 未処理動画の内訳（2026-04-21時点）
- **NO SPEECH: 683本** → 確定スキップ（音声なし動画、処理不要）
- **ERROR: 214本** → 再処理対象
- **EMPTY: 50本** → 再処理対象

### エラー原因分布（累計ログ集計）
| 原因 | 件数 | 改修版での解消見込み |
|---|---|---|
| Gemini JSONパース失敗 | ~2,019 | ✅ 3段階フォールバックで多くが解消 |
| Neon DB容量上限 | 2,098 | ✅ サムネVPS移行済みで解消 |
| DB接続切断 (SSL) | 494 | ⚠️ 一時的エラー、リトライで多くが解消 |
| 日本語ファイル名 | 183 | ✅ shutil.copy2のUUID名化で解消済み |
| タイムアウト | 85 | ⚠️ 大容量動画、リトライで一部解消 |
| Embedding次元不整合 | 17 | ✅ 768次元統一で解消 |

**予想成功率**: 214本中 150〜180本が新規処理完了に転じる見込み（70-85%）
**永久スキップ行き見込み**: 30〜60本（skip-list入り）

---

## 実行手順

### ステップ1: 事前確認

```bash
# 前処理が完了しているか確認
cd /root
source /root/direction-env/bin/activate
python3 -c "
import os, psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM transcripts WHERE text_embedding IS NULL')
print(f'Embedding NULL: {cur.fetchone()[0]}')
cur.execute('SELECT COUNT(*) FROM transcripts WHERE thumbnail_path IS NULL')
print(f'Thumbnail NULL: {cur.fetchone()[0]}')
"
```

Embedding NULL が 0、Thumbnail NULL が 許容範囲内（<500件）であることを確認。

### ステップ2: skip-listの初期化（初回のみ）

```bash
# 既存のskip-listがあればバックアップ
[ -f /root/skipped_videos.json ] && cp /root/skipped_videos.json /root/skipped_videos.json.bak

# 新規作成
echo '{}' > /root/skipped_videos.json
```

### ステップ3: 改修版 bulk_runner.py を1回実行（while true なし）

```bash
tmux new -s reprocess
source /root/direction-env/bin/activate
export GEMINI_API_KEY=$(cat /root/gemini_key.txt)
export DATABASE_URL=$(cat /root/dburl.txt | tr -d '\n' | tr -d ' ')
cd /root

# 1回目: 改修版のJSONパース強化で多くのエラーが解消される見込み
python3 bulk_runner.py \
  --folder-ids "$(cat /root/fids.txt | tr -d '\n' | tr -d ' ')" \
  --sa-key /root/sa-key.json \
  --workers 1 \
  --resume
```

**注意**: `--workers 1` は Gemini レート制限配慮。成功率を高めるため並列化しない。

**所要時間**: 214本 × 平均15秒/本 ≒ **1時間**

### ステップ4: 2回目実行（失敗動画のリトライ）

1回目で成功しなかった動画を再トライ。skip-list機能でカウントが +1。

```bash
# そのまま同じコマンドを再実行
python3 bulk_runner.py \
  --folder-ids "$(cat /root/fids.txt | tr -d '\n' | tr -d ' ')" \
  --sa-key /root/sa-key.json \
  --workers 1 \
  --resume
```

### ステップ5: 3回目実行（最後のリトライ、この後skip-list入り）

```bash
# 3回目。3回失敗した動画はここでskip-list入り確定
python3 bulk_runner.py \
  --folder-ids "$(cat /root/fids.txt | tr -d '\n' | tr -d ' ')" \
  --sa-key /root/sa-key.json \
  --workers 1 \
  --resume
```

### ステップ6: 最終状態確認

```bash
# DB登録済み動画数
python3 -c "
import os, psycopg2, json
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\"SELECT COUNT(*) FROM media_assets WHERE source_type='original'\")
print(f'DB登録済み: {cur.fetchone()[0]}本')

# skip-list確認
with open('/root/skipped_videos.json') as f:
    skip = json.load(f)
permanent = [d for d,info in skip.items() if info.get('count',0) >= 3]
print(f'永久スキップ: {len(permanent)}本')
print(f'skip-list総記録: {len(skip)}動画')
"
```

期待値:
- DB登録済み: **5,770〜5,800本**（+140〜170本増）
- 永久スキップ: **30〜60本**

### ステップ7: 永久スキップ動画の原因分析（オプション）

残った永久スキップ動画が本質的に処理不可能か調査:

```bash
python3 -c "
import json
with open('/root/skipped_videos.json') as f:
    skip = json.load(f)
# エラー原因別にカウント
from collections import Counter
errors = Counter()
for did, info in skip.items():
    if info.get('count',0) >= 3:
        msg = info.get('last_error','')[:60]
        errors[msg] += 1
for msg, cnt in errors.most_common(10):
    print(f'{cnt:3d}  {msg}')
"
```

このリストを確認して、共通パターンがあれば追加修正の余地あり。

---

## トラブル対応

### Gemini API 429 エラー頻発
```bash
# --workers を 1 に固定、--sleep で間隔を開ける（もしあれば）
# または時間をおいて再実行
```

### Neon DB容量ひっ迫
```bash
# DB容量確認
python3 -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\"SELECT pg_database_size(current_database())\")
size_mb = cur.fetchone()[0] / 1024 / 1024
print(f'DB size: {size_mb:.1f} MB / 512 MB')
"
```

300MB超えたら進行中止→要見直し。

### 特定動画だけ必ず失敗する場合
skip-list入りしてOK。手動でGoogle Driveで動画確認、破損していないかチェック。破損なら元ファイル再アップロードを依頼。

---

## 完了判定

以下が達成できれば完了:

- [ ] DB登録済み動画数が **5,770本以上**
- [ ] skip-list永久スキップが **60本以下**
- [ ] `bulk_runner.log` の最新サマリーで `ok >> error`
- [ ] ユーザーが検索UIで該当動画をヒットできる

---

## 関連ファイル

- `src/module6/scripts/bulk_runner.py` - 改修版（skip-list + JSONパース強化）
- `src/module6/scripts/backfill_embeddings.py` - Embedding再生成
- `src/module6/scripts/backfill_thumbnails.py` - サムネイル再生成
- `src/module6/scripts/retry_failed_thumbnails.py` - DL失敗動画のリトライ
- `/root/skipped_videos.json` - VPS上のskip-list（Git管理外）
- `/root/bulk_runner.log` - VPS上のログ（Git管理外）
