# 動画制作・編集 業務効率化システム構築提案書

**プロジェクト名:** Direction - Video Production Intelligence System
**作成日:** 2026年3月16日
**最終更新:** 2026年3月25日
**リサーチ規模:** 7エージェント並列調査（1回目5エージェント + 2回目深掘り2エージェント）
**調査範囲:** 12+マーケットプレイス、50+SNS/コミュニティ、30+論文・専門メディア、100+ツール・API

---

## 確定環境・条件

| 項目 | 確定内容 |
|------|---------|
| 編集ソフト | **Adobe Premiere Pro**（既契約） |
| n8n | **Hostinger セルフホスト（Docker）** |
| Google Drive | **サービスアカウント接続** |
| GPU | **未確認**（非搭載でもクラウドAPI代替で問題なし） |
| 月額予算 | **上限なし**（実装効果に合わせて柔軟） |
| BGM/SE契約 | MotionArray + Artlist（既契約） |
| Epidemic Sound | 回答待ち |
| 優先Module | 回答待ち |

### GPU非搭載時の影響と対策

| 処理 | GPU搭載時 | GPU非搭載時 | クラウドAPI代替 |
|------|---------|----------|-------------|
| Whisper文字起こし（30分動画） | 約5分 | 約20〜40分 | OpenAI API $0.18/本 |
| CLIP映像ベクトル化 | 高速 | やや遅い | OpenAI Embedding API |
| PySceneDetect | CPU主体（影響小） | 同等 | - |
| pyannote話者分離 | 約2分 | 約10分 | pyannoteAI API €19/月 |

**結論:** GPU非搭載でもクラウドAPI（月$20〜40追加）で全機能を快適に利用可能。

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
│  │  n8n(Hostinger) │ Google Drive(SA) │ FFmpeg 8.0 │ pgvector │   │
│  │  Premiere Pro   │ kotoba-whisper   │ pyannote   │ Remotion │   │
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
Google Drive (動画素材フォルダ) ※サービスアカウント接続
  ↓ n8n Google Drive Trigger (Hostinger Docker) / rclone sync
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
| ファイル管理 | Google Drive API (サービスアカウント) | 無料 |

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
  --export premiere  ← Premiere Pro タイムライン出力
  ↓
Premiere Pro で確認・微調整
```

- **auto-editor**: 無音・低活動部分を自動検出してカットリスト生成
- **非破壊出力**: **Premiere Pro** 向けのタイムラインXMLを出力
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
    → 料金: €19/月（Developer）= 125時間分、無料トライアル150時間あり
  方法B: 手動ラベリング（初回のみ）
    → 「SPEAKER_00 = MC」「SPEAKER_02 = スタッフ」と指定
  ↓
[Step 3] スタッフ該当セグメントを自動マーク
  → FFmpeg で該当区間を無音化 or Premiere Pro マーカーとして出力
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
| 話者事前登録 | pyannoteAI Voiceprint | €19/月（125時間）、無料トライアル150時間 |
| 音声処理 | FFmpeg 8.0 silencedetect/blackdetect | 無料 |
| NLE連携 | **Premiere Pro ExtendScript/UXP** | 追加料金なし（CC契約内） |

### 「カットしたいシーンの定義が難しい」への提案

1. **Phase 1（即効）**: auto-editor で無音2秒以上を自動カット → Premiere Proで手動確認
2. **Phase 2（1〜2週間）**: WhisperX話者分離でスタッフ声を自動マーク → 手動確認
3. **Phase 3（学習型）**: 過去の編集履歴から「カットされたシーンの特徴」をAIに学習

### 実現可能性: ★★★★☆（Stage A/Bは即実装、Stage Cは段階的構築）

---

## Module 3: 参考動画のリサーチ・録画・解析

### 要件
- テレビ番組風の本格的動画の参考を探す
- Claude Codeとの壁打ちで参考動画を探す
- **TVer/ABEMAの画面録画を自動化**（私的使用目的）
- **録画をシーン分割・タグ付けして蓄積**
- **蓄積した参考動画から壁打ちでシーン検索**

### 3-A: 参考動画リサーチ（壁打ち→検索）

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
  → 人気順ソート → 上位10件のURL・サムネイル・再生数を提示
  ↓
[Step 3] TwelveLabs Marengo 3.0 で映像内容検索（高精度版）
  ↓
[Step 4] 蓄積済み参考動画ライブラリからも検索（3-C参照）
  ↓
[Step 5] 結果を統合・候補リスト提示
```

### 3-B: TVer/ABEMA 自動画面録画（私的使用）

```
[自動録画パイプライン]

Playwright（ブラウザ自動操作）
  → TVer / ABEMA にアクセス
  → 指定番組ページへ遷移
  → 再生ボタンクリック、全画面化
  ↓
FFmpeg（画面キャプチャ）
  → デスクトップ画面をリアルタイム録画
  → 時間指定 or 番組終了検知で録画停止
  → MP4保存
  ↓
[Windows環境コマンド例]
  ffmpeg -f gdigrab -framerate 30 -i desktop ^
    -f dshow -i audio="Stereo Mix" ^
    -c:v libx264 -preset ultrafast -c:a aac ^
    -t 3600 output.mp4
```

**注意:** 著作権法第30条（私的使用のための複製）の範囲内での利用。各サービスの利用規約では録画を禁止しているため、あくまで個人参考用途に限定し外部への共有・二次利用は不可。

### 3-C: 録画のシーン分割・タグ付け・蓄積・検索

```
[録画後の自動解析パイプライン]

録画MP4
  ↓
[Step 1] PySceneDetect → シーン自動分割
  → カット点検出、シーンごとのクリップに分割
  ↓
[Step 2] 各シーンの代表フレーム → Gemini Vision / CLIP でベクトル化
  → 映像の視覚的特徴を数値化
  ↓
[Step 3] kotoba-whisper → シーンごとの文字起こし
  → タイムスタンプ付き日本語転写
  ↓
[Step 4] Claude がシーンごとに複数タグを自動付与
  → 構成タグ:  「オープニング」「インタビュー」「Bロール」「エンディング」
  → 演出タグ:  「スローモーション」「ズームイン」「ワイプ」「PinP」
  → 雰囲気タグ: 「感動的」「コミカル」「緊張感」「爽やか」
  → テロップタグ:「ポップ体」「ニュース風」「手書き風」「ゴシック」
  → カメラタグ: 「引き」「寄り」「俯瞰」「手持ち」「固定」
  ↓
[Step 5] pgvector にベクトル + タグ + 文字起こし + メタデータを保存
  → メタデータ: 番組名、放送日、チャンネル、元URL
```

### 3-D: 蓄積した参考動画の壁打ち検索

```
[Claude Code 壁打ち検索]

ユーザー: 「料理番組で、手元をアップで撮ってて、
          テロップがポップな感じのシーンを探して」
  ↓
Claude Code（3層検索を同時実行）:
  1. テキスト検索 — 文字起こしからキーワードマッチ
  2. タグ検索   — カメラ=「寄り」, 雰囲気=「ポップ」, 内容=「料理」
  3. 映像類似検索 — CLIPベクトルで視覚的に類似するシーンを検索
  ↓
結果:
  1. TVer_料理番組A_2026-03-15_シーン04 (03:22-03:58)
     タグ: [料理, 手元アップ, ポップテロップ, 明るい照明]
     文字起こし: 「ここでバターを溶かしていきます...」
     [サムネイル画像]

  2. ABEMA_バラエティB_2026-03-10_シーン12 (15:30-16:05)
     タグ: [調理実演, 寄り, カラフルテロップ, スタジオ]
     文字起こし: 「このポイントが重要なんですよ...」
     [サムネイル画像]
```

### 使用ツール・MCP

| コンポーネント | ツール | 費用 |
|---|---|---|
| YouTube検索 | YouTube MCP Server (APIキー不要版) | 無料 |
| プロ映像検索 | Vimeo API | 無料 |
| 映像意味検索 | TwelveLabs MCP Server (Marengo 3.0) | 有料（無料枠あり） |
| ブラウザ自動操作 | Playwright (playwright-skill) | 無料 |
| 画面録画 | FFmpeg gdigrab | 無料 |
| シーン分割 | PySceneDetect (OSS) | 無料 |
| 映像ベクトル化 | CLIP / Gemini Vision / agentic-vision | 低コスト |
| 文字起こし | kotoba-whisper v2.2 | 無料 or API |
| タグ自動付与 | Claude API（マルチモーダル） | API従量 |
| ベクトルDB | PostgreSQL + pgvector | 無料〜$19/月 |
| 壁打ちAI | Claude Code (会話型) | 既存環境 |

### 実現可能性: ★★★★☆（全て技術的に可能、画面録画部分はPC専有が必要）

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
[Phase 4: 自動生成（Premiere Pro中心）]
  ├─ Option A: SRT/ASS → FFmpeg字幕焼き込み（最速・プレビュー用）
  ├─ Option B: Premiere Pro ExtendScript → キャプション自動配置（メイン推奨）
  └─ Option C: Remotion React → プログラマティック動画生成（量産向き）
```

### 出力先別の特徴

| 出力先 | 修正のしやすさ | 品質 | コスト | 推奨用途 |
|---|---|---|---|---|
| **FFmpeg + ASS** | △（再生成が必要） | ○ | 無料 | 確認用プレビュー |
| **Premiere Pro ExtendScript** | ◎（NLE上で自由編集） | ◎ | 追加料金なし | **メイン推奨** |
| **Remotion** | ○（コード修正→再レンダリング） | ◎ | OSS | 量産・テンプレート |

### 使用ツール・MCP

| コンポーネント | ツール | 費用 |
|---|---|---|
| 日本語文字起こし | kotoba-whisper v2.2 | 無料 |
| テロップ壁打ち | Claude Code 対話 | 既存環境 |
| NLE自動配置 | **Premiere Pro ExtendScript/UXP** | 追加料金なし（CC契約内） |
| 高品質テンプレート | Remotion + @remotion/captions | OSS |
| テロップテンプレート | BOOTH無料素材 / Envato Elements | 無料〜有料 |
| 字幕ファイル管理 | subtitle-mcp (onebirdrocks) | 無料 |

### Premiere Pro自動化の注意点

- **ExtendScript**: 2026年9月までサポート。現在のメイン自動化手段
- **UXP（後継）**: 2026年以降の新標準。移行は段階的に対応
- **追加料金**: なし（既存のAdobe CC契約内で全機能利用可能）

### 実現可能性: ★★★★★（Premiere Pro ExtendScriptで追加料金なく即実装可能）

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
  ├─ Epidemic Sound MCP Server（契約時）
  │   → search_music(mood_slugs=["emotional"], bpm_max=80, vocals=false)
  │
  ├─ Freesound.org API（無料）
  │   → filter=tag:piano tag:emotional duration:[60 TO 120]
  │
  ├─ Jamendo API（無料・商用可）
  │   → 40万曲以上、ジャンル・ムード・BPM検索対応
  │
  └─ MMAudio (AI生成・著作権フリー)
      → 映像入力から同期BGMを自動生成（CVPR 2025）
  ↓
[結果統合・候補リスト提示]
  → 各曲: タイトル、プレビューURL、BPM、尺、ライセンス種別、費用
```

### BGM/SEソース一覧

| サービス | API | ライセンス | BPM検索 | ムード検索 | 費用 |
|---|---|---|---|---|---|
| **Epidemic Sound** | MCP Server あり | 商用可（サブスク） | ○ | ○ | 月$15〜 |
| **Artlist** | Enterprise API あり | 商用可（サブスク） | ○ | ○ | 既契約 |
| **MotionArray** | **APIなし（規約で自動化禁止）** | 商用可（サブスク） | - | - | 既契約 |
| **Freesound.org** | REST API（無料） | CC各種 | ○(descriptor) | ○(タグ) | 無料 |
| **Jamendo** | REST API（無料） | CC/商用可 | ○ | ○ | 無料 |
| **MMAudio** | HuggingFace Space + MCP | 自動生成（著作権フリー） | - | - | 無料 |

### 重要: MotionArrayについて

**MotionArrayの利用規約はAPI・ボット等による自動アクセスを明示的に禁止。** 従来通り手動検索を継続。代替として **Epidemic Sound MCP Server** が最も推奨（Claude Codeから直接検索・ダウンロード可能）。

### 実現可能性: ★★★★☆（Epidemic Sound MCP + Freesound APIで即構築可能）

---

## Module 6: 過去動画素材の掘り起こし

### 要件
- Google Driveの過去素材からピンポイントでシーンを検索
- 文字起こしファイルの自動生成
- セリフ・キーワードで候補を検索

### 技術構成

```
[自動インデックス化パイプライン（n8n on Hostinger）]

Google Drive (動画素材フォルダ) ※サービスアカウント接続
  ↓ n8n Google Drive Trigger (5分間隔ポーリング)
  ↓ 新規動画ファイル検出
  ↓
[Step 1] 音声抽出
  → FFmpeg: ffmpeg -i video.mp4 -ar 16000 -ac 1 audio.wav
  ↓
[Step 2] 文字起こし
  → kotoba-whisper v2.2 (日本語TV音声学習済み)
  → GPU非搭載時: OpenAI Whisper API ($0.006/分) で代替
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
[Step 5] 映像フレームインデックス（高精度版）
  → PySceneDetect でシーン分割
  → 代表フレーム → CLIP/SigLIP でベクトル化
  → pgvector に追加登録（映像の視覚検索が可能に）
```

### 検索インターフェース（Claude Code上）

```
ユーザー: 「先月撮影した動画で、田中さんが商品の説明をしているシーンを探して」
  ↓
Claude Code（3層検索）:
  1. pgvector セマンティック検索
  2. Whisper転写テキストの全文検索
  3. CLIPベクトルによる映像類似検索
  ↓
結果:
  → 「2026-02-15_CamA_003.mp4 の 04:23〜06:15」
  → 各シーンのサムネイル画像 + 転写テキスト抜粋を表示
```

### コスト見積もり（月100本・平均30分の動画）

| コンポーネント | 費用/月 |
|---|---|
| n8n (Hostinger セルフホスト) | Hostinger契約内 |
| Whisper API ($0.006/分) ※GPU非搭載時 | $18 (100本×30分) |
| Embedding ($0.02/1Mトークン) | $1〜2 |
| pgvector DB (Neon Serverless) | $0〜19 |
| **合計** | **$20〜40/月** |

### 実現可能性: ★★★★★（n8nテンプレート組み合わせで即構築可能）

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
│  完了後: curl → n8n Webhook (Hostinger) → 通知              │
└─────────────────────────────────────────────────────────────┘
```

### 具体的な設定手順

**Step 1: rclone インストール・設定**
```bash
winget install Rclone.Rclone
rclone config
# → "gdrive" という名前で Google Drive を追加
# → サービスアカウントJSON指定で認証
```

**Step 2: バッチスクリプト作成**（`C:\scripts\gdrive_sync.bat`）
```batch
@echo off
set LOG=C:\logs\rclone\sync_%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%.log

rclone sync gdrive:/動画素材 D:\LocalStorage\動画素材 ^
  --transfers 4 --checkers 8 --drive-chunk-size 64M ^
  --log-file=%LOG% --log-level=INFO

:: n8n Webhook (Hostinger) で完了通知
curl -s -X POST "https://n8n.your-hostinger-domain.com/webhook/gdrive-sync-done" ^
  -H "Content-Type: application/json" ^
  -d "{\"status\":\"done\",\"date\":\"%DATE%\"}"
```

**Step 3: Task Scheduler 設定**
1. `taskschd.msc` → 「タスクの作成」
2. トリガー: 毎日 02:00
3. 操作: `C:\scripts\gdrive_sync.bat`
4. 「ユーザーがログオンしているかどうかにかかわらず実行する」

### 実現可能性: ★★★★★（rclone + Task Scheduler で即日構築可能）

---

## Module 8: 統合管理ダッシュボード

### Claude Code をハブとした統合操作

```
[Claude Code 統合コマンド体系]

/素材整理 [フォルダパス]
  → Module 1: シーン分割・分類・名称付け

/カット [動画パス]
  → Module 2: 無音カット + スタッフ声マーク → Premiere Pro出力

/参考検索 "[イメージの説明]"
  → Module 3-A: YouTube/Vimeo/TwelveLabs検索

/参考録画 [TVer/ABEMA URL]
  → Module 3-B: 画面録画 → 3-C: シーン分割・タグ付け・蓄積

/参考ライブラリ "[検索クエリ]"
  → Module 3-D: 蓄積した参考動画の壁打ち検索

/テロップ [動画パス]
  → Module 4: 文字起こし→テロップ候補→Premiere Pro自動配置

/BGM検索 "[イメージの説明]"
  → Module 5: Epidemic Sound + Freesound + Jamendo 横断検索

/素材検索 "[キーワード]"
  → Module 6: 過去素材のセマンティック検索

/ダウンロード [Google Driveパス]
  → Module 7: バックグラウンドダウンロード開始

/ステータス
  → 各Module の処理状況表示
```

---

## 導入ロードマップ

### Phase 1: 即日〜1週間（クイックウィン）

| 施策 | 効果 | コスト |
|---|---|---|
| auto-editor 導入 | 無音カット→Premiere Pro出力を80%自動化 | 無料 |
| rclone 夜間同期設定 | ダウンロード待ち時間ゼロ | 無料 |
| kotoba-whisper で文字起こし | テロップ素材の自動生成 | 無料 or API |

### Phase 2: 1〜2週間（コア機能構築）

| 施策 | 効果 | コスト |
|---|---|---|
| n8n(Hostinger) Google Drive→Whisper→pgvector | 過去素材の検索システム構築 | $20〜40/月 |
| Premiere Pro ExtendScript テロップ自動配置 | テロップ作業の大幅削減 | 追加料金なし |
| PySceneDetect によるシーン自動分割 | 素材整理の自動化 | 無料 |

### Phase 3: 2〜4週間（高度機能）

| 施策 | 効果 | コスト |
|---|---|---|
| TVer/ABEMA 画面録画+解析パイプライン | 参考動画ライブラリの自動構築 | 無料 |
| WhisperX + pyannote 話者分離 | スタッフ声の自動識別 | 無料〜€19/月 |
| BGM横断検索（Epidemic Sound MCP等） | BGM選定のAI自動化 | サービス契約次第 |

### Phase 4: 1〜2ヶ月（統合・最適化）

| 施策 | 効果 | コスト |
|---|---|---|
| Claude Code カスタムスキル統合 | 全Module の統合操作 | 開発工数 |
| テロップテンプレートライブラリ構築 | TV番組風テロップの量産 | 開発工数 |
| 参考動画ライブラリの壁打ち検索UI | 蓄積データの高度活用 | 開発工数 |

---

## 必要な環境・依存関係

### ソフトウェア

| ソフトウェア | バージョン | 用途 | 費用 |
|---|---|---|---|
| Python | 3.10+ | スクリプト実行基盤 | 無料 |
| FFmpeg | 8.0+ | 動画・音声処理・画面録画 | 無料 |
| rclone | 最新 | Google Drive同期 | 無料 |
| **Adobe Premiere Pro** | 最新 | 動画編集（メインNLE） | 既契約（CC） |
| n8n | 最新 | ワークフロー自動化 | Hostinger契約内 |
| Docker | 最新 | コンテナ実行（Hostinger） | Hostinger契約内 |
| PostgreSQL + pgvector | 15+ | ベクトル検索DB | 無料〜$19/月 |
| Node.js | 20+ | Remotion実行 | 無料 |
| Playwright | 最新 | ブラウザ自動操作（録画用） | 無料 |

### APIキー（必要に応じて）

| API | 用途 | 費用 |
|---|---|---|
| Google Cloud (Drive API) | 素材管理（サービスアカウント） | 無料枠内 |
| OpenAI (Whisper API) | 文字起こし（GPU非搭載時） | $0.006/分 |
| OpenAI (Embedding) | ベクトル検索 | $0.02/1Mトークン |
| Epidemic Sound | BGM検索 MCP | 回答待ち |
| Freesound.org | 無料SE検索 | 無料 |
| Jamendo | 無料BGM検索 | 無料 |
| HuggingFace Token | pyannote モデル利用 | 無料 |
| pyannoteAI | 話者識別Voiceprint | €19/月（125時間） |
| Gemini Flash | 映像分析・タグ付け | 低コスト |

---

## 実現不可能 / 制約がある項目

### 1. MotionArray の自動検索 → **不可能**
- 利用規約でAPI・ボットによる自動アクセスを明示的に禁止
- **代替案:** Epidemic Sound MCP Server、または手動検索を継続

### 2. 裏方スタッフの声の完全自動除去 → **部分的に可能**
- pyannote話者分離で「識別」は可能だが、「除去」は「無音化」になる
- 同じトラックで出演者とスタッフが同時に話している場合は分離困難
- **代替案:** ピンマイク収録との組み合わせが最も確実

### 3. テロップデザインの完全AI自動提案 → **段階的に実現**
- 現時点で「シーンの雰囲気からテロップデザインをAI自動生成」する成熟したツールは存在しない
- **代替案:** TV番組ジャンル別テンプレートライブラリ + Claude壁打ち → テンプレート適用

---

## 他システムとの差別化ポイント

1. **Claude Code統合型**: 全ての操作をClaude Codeの対話インターフェースから実行可能
2. **Premiere Proネイティブ連携**: ExtendScript/UXPによる追加料金なしの自動化
3. **参考動画ライブラリ**: TVer/ABEMAの画面録画→シーン分割→タグ付け→壁打ち検索
4. **n8n(Hostinger) + MCP統合**: セルフホスト自動化とAIエージェントの組み合わせ
5. **日本語特化**: kotoba-whisper（TV音声学習済み）による高精度日本語処理
6. **Human-in-the-Loop設計**: AI提案→人間確認→修正のサイクルを前提とした設計
7. **段階的導入**: Phase 1（無料・即日）から始めて段階的に拡張可能

---

## 次のステップ（未回答項目）

1. **優先度**: どのModuleから着手するか → **回答待ち**
2. **Epidemic Sound**: 新規契約を検討するか → **回答待ち**
