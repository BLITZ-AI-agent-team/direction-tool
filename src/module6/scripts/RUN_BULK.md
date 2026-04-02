# Direction - 大規模バッチ処理 実行手順

## 対象
- 12フォルダ、6,506本の動画（3.5TB）
- 推定処理時間: Hostinger VPS実行時 約40〜50時間

## 実行方法

### 方法A: Hostinger VPSで実行（推奨・最速）

#### 1. VPSにSSH接続
```bash
ssh root@your-hostinger-ip
```

#### 2. 必要パッケージインストール
```bash
apt update && apt install -y python3 python3-pip ffmpeg
pip3 install psycopg2-binary pgvector google-genai google-api-python-client google-auth python-dotenv
```

#### 3. サービスアカウントキーをVPSにコピー
```bash
scp "JSON KEY/aiagent-dev-489706-b779fa218f8d.json" root@your-hostinger-ip:/root/sa-key.json
```

#### 4. スクリプトをVPSにコピー
```bash
scp src/module6/scripts/bulk_runner.py root@your-hostinger-ip:/root/bulk_runner.py
```

#### 5. 環境変数設定
```bash
export GEMINI_API_KEY="your-gemini-api-key"
export GOOGLE_SERVICE_ACCOUNT_KEY_PATH="/root/sa-key.json"
export DATABASE_URL="postgresql://neondb_owner:npg_W7wzN1jJyQXR@ep-lucky-shadow-a1xeitfg-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
```

#### 6. tmuxセッションで実行（SSH切断しても継続）
```bash
tmux new-session -s direction

python3 /root/bulk_runner.py \
  --folder-ids "1R_li45H1PdzCwdN720-OYOkjI_27B9FQ,1cxyu7UF5DcGL97W-9WXhWcTWm2SNN1Yb,1t8EOeJBEHDB9aHbYZ8TqaqwLS755axOk,1ClgJtBrjBlEF-UwsDcZUk1EGO4E2sNeT,18AfAW62BbhnCXLcZccNdtHtUlrEZE0MG,1CwuX6q19pR_tdd77bBgaXlpMrEZ844_0,1t1UU-Uks-wcr1B2mz_xtdlaN1Bc8ROED,1zk0_XmpBNF1vXbWvBPy4q_6io14JM73Q,100GyptgYzwrpmNSjx59KWSD_8ZEyidrN,122fDtGUjD-TULHUwOAopJOu9cA95TOCh,1L8t6pMR1zZ5oLPm8Hp3NuXSYUiiv7ZGc,1H8EzPL1mmPqcl6iuajV9k0Swsas9ATo9" \
  --sa-key /root/sa-key.json \
  --workers 3 \
  --resume

# tmuxからデタッチ: Ctrl+B → D
# tmuxに再接続: tmux attach -t direction
```

### 方法B: ローカルPCで実行

```bash
cd src/module6/scripts

python bulk_runner.py \
  --folder-ids "1R_li45H1PdzCwdN720-OYOkjI_27B9FQ,1cxyu7UF5DcGL97W-9WXhWcTWm2SNN1Yb,1t8EOeJBEHDB9aHbYZ8TqaqwLS755axOk,1ClgJtBrjBlEF-UwsDcZUk1EGO4E2sNeT,18AfAW62BbhnCXLcZccNdtHtUlrEZE0MG,1CwuX6q19pR_tdd77bBgaXlpMrEZ844_0,1t1UU-Uks-wcr1B2mz_xtdlaN1Bc8ROED,1zk0_XmpBNF1vXbWvBPy4q_6io14JM73Q,100GyptgYzwrpmNSjx59KWSD_8ZEyidrN,122fDtGUjD-TULHUwOAopJOu9cA95TOCh,1L8t6pMR1zZ5oLPm8Hp3NuXSYUiiv7ZGc,1H8EzPL1mmPqcl6iuajV9k0Swsas9ATo9" \
  --sa-key "../../JSON KEY/aiagent-dev-489706-b779fa218f8d.json" \
  --workers 3 \
  --resume
```

## 進捗確認

### ログファイル
```bash
tail -f bulk_runner.log
```

### DB確認
```bash
python3 -c "
import psycopg2
conn = psycopg2.connect('postgresql://neondb_owner:npg_W7wzN1jJyQXR@ep-lucky-shadow-a1xeitfg-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require')
cur = conn.cursor()
cur.execute('SELECT count(*) FROM media_assets')
print(f'Assets: {cur.fetchone()[0]}')
cur.execute('SELECT count(*) FROM transcripts')
print(f'Transcripts: {cur.fetchone()[0]}')
conn.close()
"
```

## 中断・再開

- **中断:** Ctrl+C で停止。処理済み動画はDBに記録済み
- **再開:** `--resume` フラグ付きで再実行。処理済み動画は自動スキップ

## トラブルシューティング

| 問題 | 対処 |
|------|------|
| Gemini APIレートリミット | `--workers 2` に減らす |
| DB接続切れ | 自動リトライで対応済み |
| ストレージ不足 | tmpファイルは各動画処理後に自動削除 |
| Google Drive 500エラー | 自動リトライで対応。頻発する場合は数分待って再開 |
