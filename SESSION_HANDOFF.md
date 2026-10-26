# SESSION HANDOFF DOCUMENT

> **CRITICAL**: 次のセッションは必ずこのファイルを読んでから作業を開始すること

**最終更新**: 2026-04-24
**作業ディレクトリ**: C:\Users\BLITZ121\Downloads\develop\direction
**詳細状態**: `.claude/temp-context/session-state.md`

---

## 🎉 現在の状況: 改善版cron再稼働、明日07:00 JSTから自動実行

- 検索システム: 完全稼働中（Embedding 100%、サムネ57%）
- **夜間サムネ補完 cron**: **有効化済み**（改善版 f4655b3）
  - 実行時刻: **毎日 07:00 JST**
  - 設定: `--workers 1 --limit 80`, `timeout 4h`
- **lstep-auto**: **完全停止**（crontab行削除、プロセスkill、メモリ14GB確保）
- GitHub最新: `f4655b3 夜間サムネ補完cronの安定化改善`

---

## 次セッション: 明朝の動作確認

### Step 1: cron実行結果確認（明日 11:00 JST 頃以降）
```bash
ssh root@72.60.192.145
tail -200 /root/nightly-thumb.log
```

**期待ログ**:
```
Nightly thumbnail backfill start: 2026-04-25 07:00:01
...
Retrying 337 assets with 3062 missing thumbnails (limited to 80)
[1/80] xxx.MP4: ok=XX err=X ETA: XXmin
...
[80/80] xxx.MP4: ok=XX err=X ETA: 0min
RETRY COMPLETE in 2.XX hours
Nightly thumbnail backfill end: ...
```

### Step 2: DB進捗確認
```bash
python3 -c "
import psycopg2
DB='postgresql://neondb_owner:npg_W7wzN1jJyQXR@ep-lucky-shadow-a1xeitfg-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require'
conn=psycopg2.connect(DB); cur=conn.cursor()
cur.execute('SELECT COUNT(DISTINCT asset_id) FROM transcripts WHERE thumbnail_path IS NULL')
print('Thumb NULL動画:', cur.fetchone()[0])
"
```
期待: 337 → 約257 （1晩80動画消化）

### Step 3: 以降、5〜6晩で完走
- 337 / 80 = 約5晩（エラー込みで6晩）
- 日次チェックは任意

---

## MUST DO（必須）

1. **このファイルを読む** - 作業開始前に必ず
2. **`.claude/temp-context/session-state.md` も読む** - 詳細な状態記録
3. **cron変更時はlocalのdeploy/配下を編集 → git push → VPSへcp**（直接VPS編集禁止）
4. **スキル指定を遵守** - 「〇〇スキルを使って」は必ずSkillツールで

## MUST NOT DO（禁止）

1. **lstep-auto関連を勝手に再起動** - ユーザーの判断で完全停止
2. **既存ファイルを無視して新規作成** - 絶対禁止
3. **「シンプルにする」と称して異なる実装** - 絶対禁止
4. **スキル指示を無視した手動実装** - 絶対禁止

---

## 重要な接続情報

- VPS: KVM4, SSH root@72.60.192.145 （lstep-auto停止済、Direction+n8n+CRM+Dashboardが稼働）
- n8n: https://n8n.srv1159429.hstgr.cloud/ (Workflow: CURGHyWPbpScnB5Q)
- DB: Neon Serverless PostgreSQL (pgvector)
- GitHub: BLITZ-AI-agent-team/direction-tool (main: f4655b3)
- cron: `/etc/cron.d/direction-thumbnail-backfill` (毎日07:00 JST)

---

*このファイルはセッション終了時に自動生成されます（v2: JSON構造化データ併記）*
