# 動画制作・編集 業務効率化システム構築提案書

**プロジェクト名:** Direction - Video Production Intelligence System
**作成日:** 2026年3月16日
**リサーチ規模:** 7エージェント並列調査（1回目5エージェント + 2回目深掘り2エージェント）
**調査範囲:** 12+マーケットプレイス、50+SNS/コミュニティ、30+論文・専門メディア、100+ツール・API

---

## 全体アーキテクチャ

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Direction System（Claude Code統合）                │
│                                                                       │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐       │
│  │ Module 1  │  │ Module 2  │  │ Module 3  │  │ Module 4  │       │
│  │ 素材整理  │  │ 自動カット│  │ リサーチ  │  │ テロップ  │       │
│  │ & 分類    │  │ & 編集    │  │ & 参考    │  │ & 字幕    │       │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘       │
│        │              │              │              │               │
│  ┌─────┴─────┐  ┌─────┴─────┐  ┌─────┴─────┐  ┌─────┴─────┐       │
│  │ Module 5  │  │ Module 6  │  │ Module 7  │  │ Module 8  │       │
│  │ BGM/SE    │  │ 素材検索  │  │ 自動DL    │  │ 統合管理  │       │
│  │ リサーチ  │  │ & 掘起し  │  │ (夜間)    │  │ ダッシュ  │       │
│  └───────────┘  └───────────┘  └───────────┘  └───────────┘       │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    基盤レイヤー                               │   │
│  │  n8n自動化 │ Google Drive API │ FFmpeg 8.0 │ pgvector DB   │   │
│  │  MCP統合   │ Whisper/kotoba   │ pyannote   │ Remotion      │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Module 1: 撮影素材の自動シーン分類・名称付け

### 要件
- Google Driveの大量動画素材を内容分析してシーンごとにグルーピング
- 背景・画角を参考に自動名称付け
- 撮影時間順に並べ替え

### 技術構成

```
Google Drive (動画素材フォルダ)
  ↓ n8n Google Drive Trigger / rclone sync
ローカルストレージ
  ↓
[Step 1] FFprobe メタデータ抽出
  → 撮影日時、解像度、コーデック、再生時間
  ↓
[Step 2] PySceneDetect (AdaptiveDetector)
  → シーン変化点を自動検出、各シーンの代表フレーム画像を抽出
  ↓
[Step 3] Gemini Flash / Claude Vision でフレーム画像を分析
  → 「屋外ロケ/スタジオ」「インタビュー/Bロール」「引き/寄り」等を分類
  → 自動名称生成: 「01_スタジオ_MC引き_0000-0145」
  ↓
[Step 4] メタデータDB (PostgreSQL + pgvector) に登録
  → 撮影時間順ソート、シーングループ管理
  ↓
[Step 5] Google Drive フォルダ自動整理 or CSVレポート出力
```

### 使用ツール・API

| コンポーネント | ツール | 費用 |
|---|---|---|
| シーン検出 | PySceneDetect (OSS) | 無料 |
| 精密検出補完 | TransNetV2 (PyTorch OSS) | 無料 |
| フレーム画像分析 | Gemini Flash API / agentic-vision スキル | 低コスト |
| メタデータ抽出 | FFprobe (FFmpeg同梱) | 無料 |
| DB | PostgreSQL + pgvector (Neon/Supabase) | 無料〜$19/月 |
| ファイル管理 | Google Drive MCP Server | 無料 |

### 実現可能性: ★★★★★（全てOSS/低コストで即実装可能）

---

## Module 2: 動画の不要部分カット

### 要件
- 無音部分の自動カット
- 裏方スタッフの指示・声が入っているシーンのカット
- カット定義が難しい部分への対応

### 技術構成（3段階アプローチ）

#### Stage A: 無音カット（即実装可能）

```
動画ファイル
  ↓
auto-editor v30 (OSS)
  --edit audio:threshold=0.04
  --export premiere  ← DaVinci Resolve / Premiere Pro タイムライン出力
  ↓
NLE（DaVinci Resolve / Premiere Pro）で確認・微調整
```

- **auto-editor**: 無音・低活動部分を自動検出してカットリスト生成
- **非破壊出力**: Premiere/DaVinci/FCP向けのタイムラインXMLを出力
- **手動調整可能**: NLEで最終確認・微調整ができるHuman-in-the-Loopフロー

#### Stage B: スタッフ声の自動識別・除去

```
動画音声
  ↓
[Step 1] WhisperX (VAD + 文字起こし + 話者分離)
  → SPEAKER_00, SPEAKER_01, ... に分類
  → 単語レベルタイムスタンプ付き
  ↓
[Step 2] 話者識別
  方法A: pyannoteAI Voiceprint（商用API）
    → 出演者の声を事前登録 → 未登録話者 = スタッフ候補
  方法B: 手動ラベリング（初回のみ）
    → 「SPEAKER_00 = MC」「SPEAKER_02 = スタッフ」と指定
  ↓
[Step 3] スタッフ該当セグメントを自動マーク
  → FFmpeg で該当区間を無音化 or NLEマーカーとして出力
```

#### Stage C: 高度な不要シーン検出（将来拡張）

```
不要シーンの定義パターン:
  1. 「よーい、スタート」「カット」等のキーワード → Whisper転写でテキスト検索
  2. カメラ揺れ・ブレ（移動中）→ FFmpeg motion detection
  3. 黒フレーム・カラーバー → FFmpeg blackdetect
  4. 音声の急激な変化（マイクON/OFF） → FFmpeg silencedetect
```

### 使用ツール・API

| コンポーネント | ツール | 費用 |
|---|---|---|
| 無音カット | auto-editor v30 (OSS) | 無料 |
| 文字起こし+話者分離 | WhisperX + pyannote 3.1 | 無料（HuggingFaceトークン要） |
| 日本語特化 | kotoba-whisper v2.2 | 無料（large-v3比6.3倍速） |
| 話者事前登録 | pyannoteAI Voiceprint (商用API) | 有料（クレジット制） |
| 音声処理 | FFmpeg 8.0 silencedetect/blackdetect | 無料 |
| NLE連携 | DaVinci Resolve Scripting API | 無料版で利用可能 |

### 「カットしたいシーンの定義が難しい」への提案

以下の**段階的アプローチ**を推奨:

1. **Phase 1（即効）**: auto-editor で無音2秒以上を自動カット → 手動確認
2. **Phase 2（1〜2週間）**: WhisperX話者分離でスタッフ声を自動マーク → 手動確認
3. **Phase 3（学習型）**: 過去の編集履歴から「カットされたシーンの特徴」をAIに学習
   - 例: 「音量が急変→カットされやすい」「特定キーワード後→カットされやすい」
   - Claude Code上で壁打ちしながらルールを調整

### 実現可能性: ★★★★☆（Stage A/Bは即実装、Stage Cは段階的構築）

---

## Module 3: 参考動画のリサーチ

### 要件
- テレビ番組風の本格的動画の参考を探す
- TVer、YouTube以外の有用な参考先
- Claude Codeとの壁打ちで参考動画を探す

### テレビ番組風リファレンスの参考先一覧

| メディア | 特徴 | 合法的活用方法 | 費用 |
|---|---|---|---|
| **TVer** | 民放見逃し配信（7日間） | 視聴してメモ・分析（DL/録画は不可） | 無料 |
| **NHKオンデマンド** | ドキュメンタリー豊富 | 視聴して演出分析 | 月990円 |
| **ABEMA** | バラエティ・ニュース | テロップスタイル研究 | 無料/月720円 |
| **YouTube** | TV公式チャンネル多数 | YouTube Data API v3で検索 | 無料 |
| **Vimeo** | プロ映像作品（CM・PV） | Vimeo APIで検索 | 無料〜 |
| **FOD（フジテレビ）** | フジ番組アーカイブ | 視聴してスタイル研究 | 月976円 |
| **U-NEXT** | 映画・ドラマ充実 | 映画的演出研究 | 月2,189円 |
| **Shutterstock/Pexels** | 高品質ストック映像 | APIで類似映像検索 | 無料〜有料 |
| **Frame.io** | プロ制作ポートフォリオ | ワークフロー研究 | 有料 |

### 壁打ち→リサーチの自動化フロー

```
[Claude Code 壁打ちセッション]
  ユーザー: 「料理番組風の、上からのカメラで手元をアップにしたシーンの参考がほしい」
  ↓
Claude Code が以下を自動実行:
  ↓
[Step 1] イメージの整理・構造化
  → カメラアングル: トップダウン / 手元アップ
  → ジャンル: 料理番組 / ハウツー
  → テイスト: 清潔感 / プロフェッショナル
  ↓
[Step 2] YouTube Data API v3 で検索
  → キーワード: "cooking show overhead shot", "料理番組 手元"
  → 人気順ソート → 上位10件のURL・サムネイル・再生数を提示
  ↓
[Step 3] TwelveLabs Marengo 3.0 で映像内容検索（高精度版）
  → 自然言語: "overhead camera angle showing hands cooking on cutting board"
  → 視覚+音声+テキストを横断検索
  ↓
[Step 4] Vimeo API でプロ映像検索
  → タグ: cooking, top-down, professional
  ↓
[Step 5] 結果を統合・候補リスト提示
  → URL、サムネイル、視聴数、類似スコア付き
```

### 使用ツール・MCP

| コンポーネント | ツール | 費用 |
|---|---|---|
| YouTube検索 | YouTube MCP Server (APIキー不要版) | 無料 |
| プロ映像検索 | Vimeo API | 無料 |
| 映像意味検索 | TwelveLabs MCP Server (Marengo 3.0) | 有料（無料枠あり） |
| ストック映像 | Pexels API / Shutterstock API | 無料/有料 |
| 壁打ちAI | Claude Code (会話型) | 既存環境 |
| リサーチ強化 | mega-research-plus スキル | 既存環境 |
| ムードボード | Milanote / Genery.io | 無料〜 |

### 重要な法的注意

- **TVer/各配信サービスのコンテンツDL・録画は著作権法違反**
- 「視聴して学ぶ」→ OK、「保存して二次利用」→ NG
- YouTube公式チャンネルの公開クリップは参照元として活用可能

### 実現可能性: ★★★★☆（YouTube/Vimeo APIは即利用可能、TwelveLabs高精度版は有料）

---

## Module 4: 動画テロップ自動生成

### 要件
- 動画コンセプトをClaude Codeと壁打ちで整理
- シーンに合うテロップ候補を提案
- 選択後にテロップ自動作成・入力
- 後から細かな修正が可能

### 技術構成

```
[Phase 1: コンセプト壁打ち]
  Claude Code 対話セッション
  → 動画全体のトーン（バラエティ/ドキュメンタリー/ニュース等）
  → 各シーンの内容・雰囲気の整理
  → テロップの方向性決定（色、フォント、アニメーション）
  ↓
[Phase 2: 文字起こし + テロップ候補生成]
  動画音声 → kotoba-whisper v2.2（日本語特化・6.3倍速）
  → タイムスタンプ付き文字起こし
  → Claude がシーンごとにテロップテキスト候補を複数提案
  → 強調すべきキーワード、フォントサイズ、色の提案
  ↓
[Phase 3: テロップスタイル選択]
  テンプレートライブラリ（TV番組ジャンル別）:
  ├─ バラエティ風: カラフル・太文字・影付き・ポップアニメーション
  ├─ ニュース風: 白文字・青背景バー・左下配置
  ├─ ドキュメンタリー風: 明朝体・控えめ・中央下
  └─ 情報バラエティ風: イエロー強調・赤アンダーライン
  ↓
[Phase 4: 自動生成（3つの出力先）]
  ├─ Option A: SRT/ASS → FFmpeg字幕焼き込み（最速・簡易）
  ├─ Option B: DaVinci Resolve API → Text+で自動配置（無料・修正容易）
  └─ Option C: Remotion React → プログラマティック動画生成（高品質・量産向き）
```

### 出力先別の特徴

| 出力先 | 修正のしやすさ | 品質 | コスト | 推奨用途 |
|---|---|---|---|---|
| **FFmpeg + ASS** | △（再生成が必要） | ○ | 無料 | 確認用プレビュー |
| **DaVinci Resolve API** | ◎（NLE上で自由編集） | ◎ | 無料 | **メイン推奨** |
| **Remotion** | ○（コード修正→再レンダリング） | ◎ | OSS | 量産・テンプレート |
| **After Effects MCP** | ◎（AE上で自由編集） | ◎◎ | AEライセンス要 | 最高品質 |

### 使用ツール・MCP

| コンポーネント | ツール | 費用 |
|---|---|---|
| 日本語文字起こし | kotoba-whisper v2.2 | 無料 |
| テロップ壁打ち | Claude Code 対話 | 既存環境 |
| NLE自動配置 | DaVinci Resolve Scripting API + auto-subs | 無料 |
| 高品質テンプレート | Remotion + @remotion/captions | OSS |
| AE連携 | After Effects MCP Server (Dakkshin) | 無料(MIT) + AEライセンス |
| テロップテンプレート | BOOTH無料素材 / Envato Elements | 無料〜有料 |
| 字幕ファイル管理 | subtitle-mcp (onebirdrocks) | 無料 |

### 実現可能性: ★★★★★（DaVinci Resolve APIルートなら全て無料で即実装可能）

---

## Module 5: BGM・SE リサーチ

### 要件
- MotionArray / Artlist を利用中
- Claude Codeで音のイメージを壁打ち → 自動で候補提示
- 有料2サイトに加えて無料BGM/SEも選択可能に

### 技術構成

```
[Claude Code 壁打ちセッション]
  ユーザー: 「感動的なシーンで使うBGM。ピアノメインでゆっくりめ、
            ボーカルなし、1分30秒くらい」
  ↓
Claude Code がイメージを構造化:
  → ムード: emotional, touching
  → 楽器: piano
  → BPM: 60-80
  → ボーカル: なし (instrumental)
  → 尺: 90秒
  ↓
[並列検索]
  ├─ Epidemic Sound MCP Server
  │   → search_music(mood_slugs=["emotional"], bpm_max=80, vocals=false)
  │   → プレビューURL + ダウンロード可能
  │
  ├─ Freesound.org API
  │   → filter=tag:piano tag:emotional duration:[60 TO 120]
  │   → CC音源（無料・帰属表記要）
  │
  ├─ Jamendo API
  │   → 40万曲以上の商用可能音楽
  │   → ジャンル・ムード・BPM検索対応
  │
  └─ MMAudio (AI生成)
      → 映像入力から同期BGMを自動生成（CVPR 2025）
      → 完全オリジナル・著作権フリー
  ↓
[結果統合・候補リスト提示]
  → 各曲: タイトル、プレビューURL、BPM、尺、ライセンス種別、費用
  → ユーザーが選択 → ダウンロード or AI生成実行
```

### BGM/SEソース一覧

| サービス | API | ライセンス | BPM検索 | ムード検索 | 費用 |
|---|---|---|---|---|---|
| **Epidemic Sound** | MCP Server あり | 商用可（サブスク） | ○ | ○ | 月$15〜 |
| **Artlist** | Enterprise API あり | 商用可（サブスク） | ○ | ○ | 要見積もり |
| **MotionArray** | **APIなし（規約で自動化禁止）** | 商用可（サブスク） | - | - | 既契約 |
| **Freesound.org** | REST API（無料） | CC各種 | ○(descriptor) | ○(タグ) | 無料 |
| **Jamendo** | REST API（無料） | CC/商用可 | ○ | ○ | 無料 |
| **MMAudio** | HuggingFace Space + MCP | 自動生成（著作権フリー） | - | - | 無料 |
| **SOUNDRAW** | Webのみ | 商用可 | ○ | ○ | 月$19.99〜 |
| **Beatoven.ai** | Webのみ | 商用可 | 指定可 | ○(16ムード) | 月$6〜 |

### 重要な発見: MotionArrayについて

**MotionArrayの利用規約はAPI・ボット等による自動アクセスを明示的に禁止しています。** スクレイピングやAPI自動化は利用規約違反となるため、MotionArrayの素材は従来通り手動検索を継続してください。

代替として **Epidemic Sound MCP Server** が最も推奨されます（Claude Codeから直接検索・ダウンロード可能）。

### 実現可能性: ★★★★☆（Epidemic Sound MCP + Freesound APIで即構築可能）

---

## Module 6: 過去動画素材の掘り起こし

### 要件
- Google Driveの過去素材からピンポイントでシーンを検索
- 文字起こしファイルの自動生成
- セリフ・キーワードで候補を検索

### 技術構成

```
[自動インデックス化パイプライン（n8n）]

Google Drive (動画素材フォルダ)
  ↓ n8n Google Drive Trigger (5分間隔ポーリング)
  ↓ 新規動画ファイル検出
  ↓
[Step 1] 音声抽出
  → FFmpeg: ffmpeg -i video.mp4 -ar 16000 -ac 1 audio.wav
  ↓
[Step 2] 文字起こし
  → kotoba-whisper v2.2 (日本語TV音声学習済み)
  → 出力: タイムスタンプ付きJSON + SRTファイル
  ↓
[Step 3] テキストファイルをGoogle Driveの同じフォルダに保存
  → video_001.mp4 → video_001_transcript.srt
  → video_001.mp4 → video_001_transcript.json
  ↓
[Step 4] ベクトルインデックス化
  → テキスト → OpenAI text-embedding-3-small でベクトル化
  → PostgreSQL + pgvector に保存
  → メタデータ: ファイル名、フォルダパス、撮影日時、カメラ番号、話者情報
  ↓
[Step 5] 映像フレームインデックス（オプション・高精度版）
  → PySceneDetect でシーン分割
  → 代表フレーム → CLIP/SigLIP でベクトル化
  → pgvector に追加登録（映像の視覚検索が可能に）
```

### 検索インターフェース（Claude Code上）

```
ユーザー: 「先月撮影した動画で、田中さんが商品の説明をしているシーンを探して」
  ↓
Claude Code:
  1. pgvector にセマンティック検索
     → "田中 商品 説明" の埋め込みベクトルで類似検索
  2. Whisper転写テキストの全文検索（バックアップ）
  3. 結果:
     → 「2026-02-15_CamA_003.mp4 の 04:23〜06:15」
     → 「2026-02-20_CamB_007.mp4 の 12:30〜14:45」
     → 各シーンのサムネイル画像 + 転写テキスト抜粋を表示
```

### コスト見積もり（月100本・平均30分の動画）

| コンポーネント | 費用/月 |
|---|---|
| n8n (自動化) | $0〜20 (セルフホスト〜Cloud) |
| Whisper API ($0.006/分) | $18 (100本×30分) |
| Embedding ($0.02/1Mトークン) | $1〜2 |
| pgvector DB (Neon Serverless) | $0〜19 |
| **合計** | **$20〜60/月** |

### ローカル完全無料版（GPU推奨）

kotoba-whisper + pgvector をDockerでローカル実行すれば、APIコスト$0で運用可能。NVIDIA GPU搭載PCがあればリアルタイムの6.3倍速で処理可能。

### 実現可能性: ★★★★★（n8nテンプレート組み合わせで即構築可能、月$20〜60）

---

## Module 7: 動画素材ファイルの自動ダウンロード

### 要件
- Google Driveの指定ファイルをバックグラウンドでダウンロード
- 夜間に自動実行

### 技術構成

```
[夜間自動ダウンロード設定]

┌─ Windows Task Scheduler ─────────────────────────────────┐
│  トリガー: 毎日 02:00                                       │
│  アクション: C:\scripts\gdrive_sync.bat                      │
│                                                              │
│  rclone sync gdrive:/動画素材/撮影済み D:\LocalStorage\動画  │
│    --transfers 4          ← 4並列ダウンロード               │
│    --checkers 8           ← 8並列チェック                    │
│    --drive-chunk-size 64M ← 大容量ファイル最適化             │
│    --log-file C:\logs\rclone\sync.log                       │
│                                                              │
│  完了後: curl → n8n Webhook → Slack/LINE通知                │
└─────────────────────────────────────────────────────────────┘
```

### 具体的な設定手順

**Step 1: rclone インストール・設定**
```bash
# rclone インストール（winget）
winget install Rclone.Rclone

# Google Drive 接続設定
rclone config
# → "gdrive" という名前で Google Drive を追加
# → OAuth認証フロー完了
# → 重要: Google Cloud Console で自前の Client ID を作成（レートリミット回避）
```

**Step 2: バッチスクリプト作成**（`C:\scripts\gdrive_sync.bat`）

```batch
@echo off
set LOG=C:\logs\rclone\sync_%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%.log

rclone sync gdrive:/動画素材 D:\LocalStorage\動画素材 ^
  --transfers 4 --checkers 8 --drive-chunk-size 64M ^
  --log-file=%LOG% --log-level=INFO

:: n8n Webhook で完了通知
curl -s -X POST "http://localhost:5678/webhook/gdrive-sync-done" ^
  -H "Content-Type: application/json" ^
  -d "{\"status\":\"done\",\"date\":\"%DATE%\"}"
```

**Step 3: Task Scheduler 設定**
1. `taskschd.msc` → 「タスクの作成」
2. トリガー: 毎日 02:00
3. 操作: `C:\scripts\gdrive_sync.bat`
4. 「ユーザーがログオンしているかどうかにかかわらず実行する」

**Step 4: n8n 通知ワークフロー**
```
Webhook Trigger (/webhook/gdrive-sync-done)
  → Slack通知: 「夜間同期完了: XX ファイル同期済み」
  → エラー時: メール通知 + ログ添付
```

### 実現可能性: ★★★★★（rclone + Task Scheduler で即日構築可能）

---

## Module 8: 統合管理ダッシュボード

### Claude Code をハブとした統合操作

全Moduleを Claude Code 上で統合操作できるようにします。

```
[Claude Code 統合コマンド体系]

/素材整理 [フォルダパス]
  → Module 1 実行: シーン分割・分類・名称付け

/カット [動画パス]
  → Module 2 実行: 無音カット + スタッフ声マーク

/参考検索 "[イメージの説明]"
  → Module 3 実行: 壁打ち→YouTube/Vimeo/TwelveLabs検索

/テロップ [動画パス]
  → Module 4 実行: 文字起こし→テロップ候補提案→自動生成

/BGM検索 "[イメージの説明]"
  → Module 5 実行: Epidemic Sound + Freesound + Jamendo 横断検索

/素材検索 "[キーワード]"
  → Module 6 実行: 過去素材のセマンティック検索

/ダウンロード [Google Driveパス]
  → Module 7 実行: バックグラウンドダウンロード開始

/ステータス
  → 各Module の処理状況表示
```

---

## 導入ロードマップ

### Phase 1: 即日〜1週間（クイックウィン）

| 施策 | 効果 | コスト |
|---|---|---|
| auto-editor 導入 | 無音カット作業を80%自動化 | 無料（pip install） |
| rclone 夜間同期設定 | ダウンロード待ち時間ゼロ | 無料 |
| kotoba-whisper で文字起こし | テロップ素材の自動生成 | 無料 |

### Phase 2: 1〜2週間（コア機能構築）

| 施策 | 効果 | コスト |
|---|---|---|
| n8n Google Drive→Whisper→pgvector パイプライン | 過去素材の検索システム構築 | $20〜60/月 |
| DaVinci Resolve auto-subs 導入 | テロップ自動配置 | 無料 |
| PySceneDetect によるシーン自動分割 | 素材整理の自動化 | 無料 |

### Phase 3: 2〜4週間（高度機能）

| 施策 | 効果 | コスト |
|---|---|---|
| Epidemic Sound MCP 連携 | BGM検索のAI自動化 | Epidemic Sound サブスク |
| WhisperX + pyannote 話者分離 | スタッフ声の自動識別 | 無料 |
| YouTube/TwelveLabs MCP 連携 | 参考動画リサーチの自動化 | TwelveLabs有料（無料枠あり） |

### Phase 4: 1〜2ヶ月（統合・最適化）

| 施策 | 効果 | コスト |
|---|---|---|
| Claude Code カスタムスキル統合 | 全Module の統合操作 | 開発工数 |
| テロップテンプレートライブラリ構築 | TV番組風テロップの量産 | 開発工数 |
| マルチカメラ SmartSwitch 活用 | カメラ切替の自動化 | DaVinci Resolve 20 無料 |

---

## 必要な環境・依存関係

### ソフトウェア

| ソフトウェア | バージョン | 用途 | 費用 |
|---|---|---|---|
| Python | 3.10+ | スクリプト実行基盤 | 無料 |
| FFmpeg | 8.0+ | 動画・音声処理 | 無料 |
| rclone | 最新 | Google Drive同期 | 無料 |
| DaVinci Resolve | 20+ | 動画編集・SmartSwitch | 無料版あり |
| n8n | 最新 | ワークフロー自動化 | 無料（セルフホスト） |
| Docker | 最新 | コンテナ実行 | 無料 |
| PostgreSQL + pgvector | 15+ | ベクトル検索DB | 無料 |
| Node.js | 20+ | Remotion実行 | 無料 |

### APIキー（必要に応じて）

| API | 用途 | 費用 |
|---|---|---|
| Google Cloud (Drive API) | 素材管理 | 無料枠内 |
| OpenAI (Whisper API) | 文字起こし（ローカル代替あり） | $0.006/分 |
| OpenAI (Embedding) | ベクトル検索（ローカル代替あり） | $0.02/1Mトークン |
| Epidemic Sound | BGM検索 MCP | サブスク（月$15〜） |
| Freesound.org | 無料SE検索 | 無料 |
| HuggingFace Token | pyannote モデル利用 | 無料 |

### 月額運用コスト見積もり

| 構成 | 月額 |
|---|---|
| **最小構成（全てローカル/無料）** | **$0**（GPU搭載PC前提） |
| **推奨構成（API活用）** | **$40〜80/月** |
| **フル構成（全API+有料サービス）** | **$100〜200/月** |

---

## 実現不可能 / 制約がある項目

### 1. MotionArray の自動検索 → **不可能**
- 利用規約でAPI・ボットによる自動アクセスを明示的に禁止
- **代替案:** Epidemic Sound MCP Server に移行、または手動検索を継続

### 2. TVer からの自動コンテンツ取得 → **法的リスク極めて高い**
- 公式APIなし、スクレイピングは著作権法違反リスク
- **代替案:** 視聴してメモ・分析する合法的リサーチ、YouTube公式チャンネル活用

### 3. 裏方スタッフの声の完全自動除去 → **部分的に可能**
- pyannote話者分離で「識別」は可能だが、「除去」は「無音化」になる
- 同じトラックで出演者とスタッフが同時に話している場合は分離困難
- **代替案:** ピンマイク収録との組み合わせが最も確実

### 4. テロップデザインの完全AI自動提案 → **段階的に実現**
- 現時点で「シーンの雰囲気からテロップデザインをAI自動生成」する成熟したツールは存在しない
- **代替案:** TV番組ジャンル別テンプレートライブラリ + Claude壁打ちで方向性決定 → テンプレート適用

---

## 他システムとの差別化ポイント

1. **Claude Code統合型**: 全ての操作をClaude Codeの対話インターフェースから実行可能
2. **n8n + MCP統合**: ワークフロー自動化とAIエージェントの組み合わせ
3. **日本語特化**: kotoba-whisper（TV音声学習済み）による高精度日本語処理
4. **Human-in-the-Loop設計**: AI提案→人間確認→修正のサイクルを前提とした設計
5. **段階的導入**: Phase 1（無料・即日）から始めて段階的に拡張可能
6. **既存ワークフローとの共存**: DaVinci Resolve / Premiere Pro のネイティブ連携

---

## 次のステップ

この提案をご確認いただき、以下についてご判断をお願いします:

1. **優先度**: どのModuleから着手するか
2. **編集ソフト**: メインの編集ソフトはDaVinci Resolve / Premiere Pro / その他のどれか
3. **インフラ**: n8nはセルフホスト（Docker）/ n8n Cloud のどちらを希望か
4. **GPU**: ローカルPCにNVIDIA GPUは搭載されているか（ローカルWhisper高速化に影響）
5. **Epidemic Sound**: 既にサブスクリプション契約しているか、またはMCP連携のために新規契約を検討するか
6. **Google Drive**: サービスアカウント or OAuth認証のどちらで接続するか
7. **予算**: 月額の運用予算目安（$0 / $40〜80 / $100〜200）

ご判断いただければ、即座に実装に入ります。
