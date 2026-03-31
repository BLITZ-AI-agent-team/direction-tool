# Direction - 要件定義書（最終確定版）

**プロジェクト名:** Direction - Video Production Intelligence System
**作成日:** 2026年3月31日
**改訂:** 2026年3月31日（最終確定事項反映）
**ステータス:** 最終確定版

---

## 1. プロジェクト概要

### 1.1 目的
動画制作者・動画編集者の業務効率化を実現する、Claude Code統合型の動画制作支援システムを構築する。

### 1.2 対象ユーザー
- 動画制作担当者（テレビ番組風の本格的・高品質な動画を制作）
- 動画編集担当者（Adobe Premiere Proを使用）

### 1.3 解決する課題
1. 撮影素材の整理・分類に膨大な時間がかかる
2. 無音カット・不要シーンの除去が手作業
3. 参考動画のリサーチに時間がかかる
4. テロップの文字起こし・作成・配置が手作業
5. BGM・SEの検索に時間がかかる
6. 過去の動画素材からピンポイントでシーンを探せない
7. Google Driveからのダウンロードに時間がかかる

---

## 2. 確定環境・前提条件

### 2.1 確定済み環境

| 項目 | 内容 | 備考 |
|------|------|------|
| 動画編集ソフト | Adobe Premiere Pro | 既契約（Adobe CC） |
| テロップ制作 | Adobe Photoshop | 既契約（Adobe CC）、初期テンプレートデザイン用 |
| テロップ運用 | MOGRT（Motion Graphics Template） | Premiere Pro上で直接テキスト編集可能 |
| クラウドストレージ | Google Drive | サービスアカウント接続（最小権限設定） |
| 自動化基盤 | n8n（Hostinger セルフホスト・Docker） | 既稼働 |
| BGM/SEサービス | MotionArray + Artlist | 既契約 |
| 文字起こしエンジン | kotoba-whisper v2.2 | 日本語TV音声学習済み、large-v3比6.3倍速 |
| AIプラットフォーム | Claude Code + Taisun Agent v2.40.0 | 既稼働 |
| OS | **macOS（メイン）/ Windows（サブ）** | システム稼働PC（GPU未確認） |

### 2.2 未確定事項

| 項目 | ステータス | 影響範囲 |
|------|----------|---------|
| GPU搭載有無 | 未確認 | ローカルWhisper速度（非搭載でもAPI代替可） |
| Epidemic Sound契約 | 現時点ではなし | Module 5 BGM検索の精度・利便性 |
| 優先Module | **Module 6 → 7 → 3 の順（3つ）** | 開発着手順序 |
| After Effects契約有無 | 未確認 | MOGRT自動生成に必要 |

### 2.3 技術的前提

- GPU非搭載の場合：クラウドAPI（OpenAI Whisper API $0.006/分等）で代替。月$20〜40追加
- n8n on Hostinger：FFmpeg・yt-dlp等のシステムコマンド実行が可能（Docker環境）
- Google Drive：サービスアカウントによるサーバー間認証（ユーザー操作不要）
- **VFR（可変フレームレート）対策:** スマートフォン撮影素材はVFR（可変フレームレート）であることが多く、タイムコードのズレを引き起こす。取り込み時にFFmpegで自動CFR（固定フレームレート）変換を行う

### 2.4 サービスアカウント最小権限設定

| 設定項目 | 内容 |
|---------|------|
| アクセス範囲 | 指定フォルダのみ（ドメイン全体アクセス不可） |
| 権限レベル | 編集者（Editor）— ファイル読取・書込・作成 |
| 制限事項 | フォルダ削除権限なし、共有設定変更権限なし |
| 鍵ローテーション | 90日ごとにサービスアカウント鍵を更新 |

---

## 3. 機能要件

### 3.0 共通アーキテクチャ

#### 3.0.1 データフロー設計

全Moduleは独立して処理を実行するが、処理結果は共有pgvector DBに保存する。他のModuleは同じファイルの処理結果がDBにあれば再利用し、なければ独立して処理を実行する（共有キャッシュ型アーキテクチャ）。

```
[素材取り込み]
  ↓ FFmpeg CFR変換（VFR対策）
  ↓ FFmpeg音声抽出（16kHz/モノラル）
  ↓ kotoba-whisper文字起こし
  ↓ PySceneDetectシーン分割
  ↓ 結果をpgvector DBに格納
  ↓
[各Module] → DB参照（キャッシュHit時は再処理スキップ）
```

**競合制御:** pgvector共有テーブルに対して楽観的ロック（バージョニング）を適用し、Race Conditionを防止する。将来的にスケールする場合はPub/Subモデルへの移行を検討する。

#### 3.0.2 タイムコード統合フォーマット

auto-editor、WhisperX、PySceneDetect等の全解析結果を**単一JSON中間フォーマット**に統合し、Premiere Pro側で一括適用する。別々のXML/SRT出力によるタイムコードズレを防止する。

```json
{
  "source_file": "素材001.mp4",
  "fps": 29.97,
  "cfr_converted": true,
  "duration_sec": 1800,
  "scenes": [
    {
      "id": 1,
      "start_tc": "00:00:00:00",
      "end_tc": "00:00:45:15",
      "grid_image": "scene_001_grid_4x4.jpg",
      "clip_classification": "スタジオ_MC引き",
      "transcript": "...",
      "speakers": [
        {"id": "SPEAKER_01", "role": "MC", "method": "voiceprint"}
      ],
      "silence_regions": [...],
      "markers": [...]
    }
  ]
}
```

#### 3.0.3 UX設計方針

**2層段階アプローチ（確定済み）:**

| Phase | UX | 目的 |
|-------|-----|------|
| Phase 1〜2 | Claude Code デスクトップアプリ（チャットUI） | 機能検証・基本運用 |
| Phase 3〜 | Premiere Pro UXPパネル | 本番運用・ネイティブ統合 |

※ Web中間層（Streamlit/Gradio）は構築しない（サンクコスト回避）。
※ Claude CodeからPremiere Pro UXP連携はClaude Code → ExtendScript/UXP CLI呼び出しで実現。

#### 3.0.4 エラーハンドリング共通方針

| エラー分類 | 通知チャネル | リトライポリシー |
|-----------|------------|---------------|
| API接続エラー（Whisper/Gemini/Drive等） | Claude Code内通知 + Slack/LINE | 3回リトライ（指数バックオフ: 1s→5s→30s） |
| ローカル処理エラー（FFmpeg/PySceneDetect等） | Claude Code内通知 | リトライなし、ログ出力→手動対応 |
| Photoshop/Premiere起動失敗 | Claude Code内通知 | リトライなし、アプリ起動確認をユーザーに通知 |
| pgvector接続失敗 | Slack/LINE アラート | 5回リトライ（10秒間隔） |
| Google Driveクォータ超過 | Slack/LINE アラート | 24時間待機後に自動再試行 |

---

### 3.1 Module 1: 素材自動整理・分類

#### 3.1.1 機能概要
Google Driveにアップロードされた動画素材を自動解析し、シーンごとに分類・名称付けする。

#### 3.1.2 機能要件

| ID | 要件 | 優先度 |
|----|------|-------|
| M1-01 | Google Driveの指定フォルダ内の動画ファイルを自動検出する | 必須 |
| M1-02 | FFprobeで撮影日時・解像度・再生時間等のメタデータを抽出する | 必須 |
| M1-02b | **VFR素材を検出し、FFmpegでCFR（固定フレームレート）に自動変換する** | 必須 |
| M1-03 | PySceneDetectでシーン変化点を自動検出し、シーンごとに分割する | 必須 |
| M1-04 | 各シーンの**複数フレーム（4x4グリッド画像タイル）**をAI（Gemini Flash/Claude Vision）で分析し、背景・画角・内容を分類する | 必須 |
| M1-04b | **シーン分割後、CLIPベクトル間のコサイン類似度で0.9以上のシーンをクラスタリング統合し、代表グリッドのみをクラウドAPIに送信する（APIコスト95%削減）** | 必須 |
| M1-05 | 分類結果に基づいて自動名称を生成する（例:「01_スタジオ_MC引き_0000-0145」） | 必須 |
| M1-06 | 撮影時間（保存時間）順に並べ替えて一覧化する | 必須 |
| M1-07 | 結果をCSVレポートとして出力する | 必須 |
| M1-08 | Google Driveのフォルダ構造を自動整理する（オプション） | 任意 |
| M1-09 | メタデータをPostgreSQL + pgvectorに登録する | 必須 |

#### 3.1.3 入出力

| 項目 | 内容 |
|------|------|
| 入力 | Google Driveフォルダパスまたはローカルフォルダパス |
| 出力 | シーン分割済み動画クリップ、CSVレポート、4x4グリッド代表フレーム画像、DBレコード、統合JSON |

#### 3.1.4 使用技術

| コンポーネント | 技術 |
|---|---|
| VFR→CFR変換 | FFmpeg (-vsync cfr) |
| シーン検出 | PySceneDetect (AdaptiveDetector) |
| 精密補完 | TransNetV2 |
| メタデータ抽出 | FFprobe |
| シーンクラスタリング | CLIP (ローカル) + コサイン類似度 |
| フレーム分析 | Gemini Flash API（4x4グリッド画像入力） |
| DB | PostgreSQL + pgvector |
| ファイル操作 | Google Drive API (サービスアカウント) |

#### 3.1.5 受入テスト基準

| 項目 | 基準 |
|------|------|
| シーン分類精度 | 80%以上（人間判定との一致率） |
| VFR検出率 | 100%（VFR素材を見逃さない） |
| 処理速度 | 30分動画を15分以内に完了 |
| CSV出力 | 全シーンが正しいタイムコード・名称で記録されている |

---

### 3.2 Module 2: 不要部分自動カット

#### 3.2.1 機能概要
動画の無音部分、環境音のみの部分、裏方スタッフの声が入ったシーンを自動検出し、**統合JSON中間フォーマット**として出力する。Premiere Proへの反映はJSON経由で一括適用する。

#### 3.2.2 機能要件

| ID | 要件 | 優先度 |
|----|------|-------|
| M2-01 | 無音区間（閾値調整可能、デフォルト3秒以上）を自動検出する | 必須 |
| M2-02 | **検出結果を統合JSON中間フォーマットに出力し、Premiere Pro ExtendScript/UXPで一括適用する** | 必須 |
| M2-03 | WhisperXで音声の文字起こし+話者分離を行う | 必須 |
| M2-04 | 話者ごとにラベル（SPEAKER_00等）を付与する | 必須 |
| M2-05 | **レギュラー出演者は声紋登録（pyannoteAI Voiceprint）で識別し、ゲスト・一時出演者はLLMテキスト文脈判定で役割推論するハイブリッド方式** | 推奨 |
| M2-06 | 手動ラベリングによる話者識別（初回設定）をサポートする | 必須 |
| M2-07 | スタッフ該当セグメントをJSON中間フォーマット内のマーカーとして出力する | 必須 |
| M2-08 | 「よーい、スタート」「カット」等のキーワードを転写テキストから検出する（LLM文脈判定の一部として統合） | 推奨 |
| M2-09 | 黒フレーム・カラーバーを自動検出する（FFmpeg blackdetect） | 任意 |
| M2-10 | カメラ揺れ・ブレを検出する（FFmpeg motion detection） | 任意 |
| M2-11 | VAD（Voice Activity Detection）で人の声がある区間を検出する（Silero VAD） | 必須 |
| M2-12 | 人声がない区間について、環境音分類AI（YAMNet/PANNs）で音の種類を判別する（風、サイレン、車、雑踏等） | 必須 |
| M2-13 | 3秒以上の「環境音のみ」の区間を自動カット対象としてマークする | 必須 |
| M2-14 | セリフが含まれる区間は環境音が混在していてもカットしない（保持） | 必須 |

#### 3.2.3 カット判定ロジック

```
├─ 3秒以上の完全無音 → カット
├─ 3秒以上の環境音のみ（VADで人声なし）→ カット
├─ 人のセリフあり + 環境音混在 → カットなし（保持）
└─ 人のセリフあり → カットなし（保持）
```

#### 3.2.4 入出力

| 項目 | 内容 |
|------|------|
| 入力 | 動画ファイルパス、閾値設定（オプション） |
| 出力 | **統合JSON中間フォーマット**（無音区間・環境音区間・話者分離・マーカー含む）、Premiere Pro一括適用スクリプト |

#### 3.2.5 話者分離ハイブリッド方式

```
[レギュラー出演者]
  → 事前にクリアな音声サンプルを登録（pyannoteAI Voiceprint）
  → 声紋マッチングで自動識別

[ゲスト・一時出演者]
  → WhisperXで単純話者分離（SPEAKER_00, SPEAKER_01...）
  → LLMがテキスト文脈で役割推論
    - メタ発言検出:「カットー」「よーい、スタート」→ スタッフ判定
    - 対話パターン分析: 質問する側=MC、答える側=ゲスト
    - 敬語・口調パターンでスタッフ/出演者を推定
```

#### 3.2.6 使用技術

| コンポーネント | 技術 |
|---|---|
| 無音カット | auto-editor v30 |
| 文字起こし+話者分離 | WhisperX + pyannote 3.1 |
| 日本語文字起こし | kotoba-whisper v2.2 |
| レギュラー話者識別 | pyannoteAI Voiceprint (€19/月) |
| ゲスト話者推論 | Claude Code LLMテキスト文脈判定 |
| 音声処理 | FFmpeg 8.0 |
| 人声検出（VAD） | Silero VAD（WhisperX内蔵） |
| 環境音分類 | YAMNet（TensorFlow Hub）/ PANNs |
| 音声レベル検出 | FFmpeg silencedetect |
| NLE連携 | Premiere Pro ExtendScript/UXP（JSON一括適用） |
| 出力フォーマット | 統合JSON中間フォーマット |

#### 3.2.7 制約事項
- 出演者とスタッフが同時に話している箇所は自動分離が困難
- 「除去」ではなく「無音化」またはマーカー付与による通知方式
- ピンマイク収録との併用で精度が大幅に向上
- ゲスト話者のLLM文脈判定は100%の精度を保証しない（推定結果の手動確認が必要）

#### 3.2.8 受入テスト基準

| 項目 | 基準 |
|------|------|
| 無音区間検出精度 | 95%以上（3秒以上の無音を検出） |
| 環境音区間検出精度 | 90%以上（3秒以上の環境音のみ区間を検出） |
| レギュラー話者識別精度 | 90%以上（声紋登録済み話者） |
| タイムコード精度 | ±0.5秒以内（Premiere Pro配置時） |
| JSON出力整合性 | auto-editor結果とWhisperX結果が同一タイムライン上で一致 |

---

### 3.3 Module 3: 参考動画リサーチ・録画・解析

#### 3.3.1 機能概要
4つのサブ機能で構成される参考動画管理システム。

#### 3.3.2 機能要件

**3-A: 参考動画リサーチ（壁打ち→検索）**

| ID | 要件 | 優先度 |
|----|------|-------|
| M3A-01 | Claude Codeとの対話でイメージを構造化する（カメラ/ジャンル/テイスト等） | 必須 |
| M3A-02 | YouTube Data API v3でキーワード・人気順検索を行う | 必須 |
| M3A-02b | **ディレクター指定のリファレンスチャンネル/再生リスト限定検索を行う（汎用検索のノイズ回避）** | 必須 |
| M3A-03 | Vimeo APIでプロ映像を検索する | 推奨 |
| M3A-04 | TwelveLabs Marengo 3.0で映像意味検索を行う | 任意 |
| M3A-05 | 検索結果をURL・サムネイル・再生数付きで一覧表示する | 必須 |

**3-B: 参考動画取り込み（完全自動方式）**

> **最終確定:** 完全自動画面録画をメインフローとする。DRM対策（ハードウェアアクセラレーション無効化+OBS Studioウィンドウキャプチャ）を組み込み、Playwright + OBS自動録画オーケストレーションで実現する。

| ID | 要件 | 優先度 |
|----|------|-------|
| M3B-01 | **Tier 1（自動・メイン）:** Playwright でブラウザ操作し、OBS Studioでウィンドウキャプチャ録画を自動制御する（TVer/ABEMA対応） | 必須 |
| M3B-02 | **Tier 2（自動・推奨）:** YouTube公式チャンネルからyt-dlpで自動ダウンロードし、解析パイプラインに投入する | 必須 |
| M3B-03 | **Tier 3（手動フォールバック）:** 自動録画失敗時にOBS手動録画し、指定フォルダに配置すると自動解析パイプラインが検出して処理する | 必須 |
| M3B-04 | 取り込みファイルをMP4形式でローカル保存する | 必須 |
| M3B-05 | 録画失敗時のエラー検知→リトライ→ユーザー通知を実装する | 必須 |

**DRMリスク・法的注意事項:**
- TVer/ABEMAはWidevine DRMを使用しており、ハードウェアアクセラレーション有効時は映像が保護され録画不可
- **DRM対策として以下を実装:**
  1. ハードウェアアクセラレーション無効化 + ソフトウェアレンダリングモードでブラウザを起動
  2. OBS Studio（ウィンドウキャプチャモード）を採用（FFmpeg gdigrabより安定）
  3. Playwrightでブラウザ操作→OBS起動・録画開始→停止のオーケストレーション
- 各サービスの利用規約では録画を禁止している（**私的使用目的のみ・外部共有不可**）
- DRM回避の完全な保証はなく、コンテンツ保護強化により録画不可になる場合がある
- 録画できない場合はTier 3（手動フォールバック）に切り替える

**3-C: 録画のシーン分割・タグ付け・蓄積**

| ID | 要件 | 優先度 |
|----|------|-------|
| M3C-01 | PySceneDetectで録画動画をシーン分割する | 必須 |
| M3C-02 | 各シーンの**複数フレーム（4x4グリッド画像）**をCLIPでクラスタリングし、代表グリッドをGemini Visionでベクトル化する | 必須 |
| M3C-03 | kotoba-whisperでシーンごとの文字起こしを行う | 必須 |
| M3C-04 | Claudeがシーンごとに複数タグを自動付与する | 必須 |
| M3C-05 | タグカテゴリ: 構成、演出、雰囲気、テロップスタイル、カメラワーク | 必須 |
| M3C-06 | ベクトル+タグ+文字起こし+メタデータをpgvectorに保存する | 必須 |
| M3C-07 | メタデータに番組名・放送日・チャンネル・元URLを含める | 必須 |

**3-D: 蓄積した参考動画の壁打ち検索**

| ID | 要件 | 優先度 |
|----|------|-------|
| M3D-01 | Claude Codeとの壁打ちで検索クエリを整理する | 必須 |
| M3D-02 | テキスト検索（文字起こしからキーワードマッチ）を行う | 必須 |
| M3D-03 | タグ検索（複数タグの組み合わせフィルタ）を行う | 必須 |
| M3D-04 | 映像類似検索（CLIPベクトルのコサイン類似度）を行う | 必須 |
| M3D-05 | 検索結果をサムネイル・タグ・文字起こし抜粋付きで表示する | 必須 |

#### 3.3.3 受入テスト基準

| 項目 | 基準 |
|------|------|
| YouTube検索精度 | リファレンスチャンネル指定時、関連度の高い動画が上位5件に含まれる |
| Tier 1自動録画成功率 | DRM非適用コンテンツで90%以上の録画成功 |
| Tier 3自動検出 | 指定フォルダ配置後60秒以内に解析パイプラインが起動する |
| タグ付け精度 | 75%以上（人間判定との一致率） |
| セマンティック検索 | 関連シーンが上位10件に含まれる確率80%以上 |

---

### 3.4 Module 4: テロップ自動生成

#### 3.4.1 機能概要
動画の文字起こしから、シーンに合ったテロップを自動作成する。**SRTキャプション→MOGRTテンプレート→Photoshop初期デザインの3段階フロー**で、Premiere Pro上での直接編集（非破壊ワークフロー）を実現する。

#### 3.4.2 機能要件

| ID | 要件 | 優先度 |
|----|------|-------|
| M4-01 | kotoba-whisper v2.2で動画音声の日本語文字起こしを行う | 必須 |
| M4-02 | タイムスタンプ付きのテキスト（SRT/JSON）を出力する | 必須 |
| M4-03 | Claude Codeとの壁打ちで動画コンセプト・テロップ方向性を整理する | 必須 |
| M4-04 | シーンごとにテロップテキスト候補を複数提案する | 必須 |
| M4-05 | 強調キーワード、フォントサイズ、色の方向性を提案する | 必須 |
| M4-06 | TV番組ジャンル別テンプレートを用意する（バラエティ/ニュース/ドキュメンタリー/情報バラエティ） | 必須 |
| M4-07 | **【Phase 1】SRTをPremiere Proキャプショントラックに流し込み、テキストスタイルで装飾する** | 必須 |
| M4-08 | **【Phase 2】MOGRTテンプレートをAEで作成（TV番組ジャンル別4種）→ ExtendScriptでテキスト差し替え → Premiere Pro配置** | 必須 |
| M4-09 | **【Phase 3以降】Photoshopは「テンプレートの初期デザイン」のみに使用。運用時のテキスト流し込みはMOGRT経由** | 推奨 |
| M4-10 | Premiere Pro上でテロップの位置・タイミング・テキスト・色を手動修正可能にする（非破壊編集） | 必須 |
| M4-11 | SRT/ASS→FFmpeg字幕焼き込みによるプレビュー生成をサポートする | 推奨 |

#### 3.4.3 テロップ制作フロー（3段階）

```
[Phase 1: 即時運用（SRTキャプション）]
  kotoba-whisper v2.2 → タイムスタンプ付きSRT
  → Premiere Pro キャプショントラックに流し込み
  → テキストスタイルで装飾
  → Premiere上で直接テキスト編集可能 ✓

[Phase 2: 標準運用（MOGRT）]
  After Effects → MOGRTテンプレート作成（ジャンル別4種）
  → ExtendScript でテキスト差し替え
  → Premiere Pro タイムライン配置
  → Premiere上で直接テキスト・色・サイズ編集可能 ✓

[Phase 3以降: 高品質版（PS初期デザイン）]
  Photoshop → テンプレート初期デザイン（視覚的に作り込み）
  → MOGRT化 → 以降はPhase 2と同じフロー
```

> **討論根拠:** PNG書き出しフローは、ディレクターチェック後のテキスト修正が毎回「Photoshop修正→PNG再書き出し→Premiere差し替え」の手戻りになり実運用で破綻する。MOGRTならPremiere Pro上で直接テキスト・色・サイズを編集でき、非破壊ワークフローが実現する。

#### 3.4.4 使用技術

| コンポーネント | 技術 |
|---|---|
| 文字起こし | kotoba-whisper v2.2 |
| テロップ壁打ち | Claude Code |
| Phase 1 | Premiere Pro キャプショントラック + テキストスタイル |
| Phase 2 | After Effects MOGRT + ExtendScript テキスト差し替え |
| Phase 3 | Photoshop（初期デザインのみ）→ MOGRT化 |
| NLE配置 | Premiere Pro ExtendScript/UXP |
| プレビュー | FFmpeg + ASS/SRT |
| テンプレート素材 | BOOTH / Envato Elements |

#### 3.4.5 制約事項
- MOGRTの自動生成にはAfter Effectsが必要（Adobe CC契約に含まれるか確認要）
- Photoshop ExtendScriptは今後UXPに移行予定（2026年9月以降段階的）
- 追加料金なし（既存Adobe CC契約内）
- 複雑なアニメーションテロップはAfter Effects連携が必要（将来拡張）

#### 3.4.6 受入テスト基準

| 項目 | 基準 |
|------|------|
| 文字起こし精度 | 90%以上（日本語TV音声） |
| SRTタイミング精度 | ±0.5秒以内 |
| MOGRT配置後のテキスト編集 | Premiere Pro上で直接編集可能であること |
| テンプレート適用 | 4ジャンル全てで正常にテキスト差し替えが動作 |

---

### 3.5 Module 5: BGM・SEリサーチ

#### 3.5.1 機能概要
**2系統の検索モード**を提供する：(1) Claude Codeとの壁打ちで音のイメージを言語化→構造化→検索、(2) 参考曲URL/ファイルから音響特徴を抽出→類似曲検索。

#### 3.5.2 機能要件

| ID | 要件 | 優先度 |
|----|------|-------|
| M5-01 | **【壁打ちモード】** Claude Codeとの壁打ちで音のイメージを構造化する（ムード/楽器/BPM/ボーカル/尺） | 必須 |
| M5-02 | Freesound.org APIで無料SE/BGMを検索する | 必須 |
| M5-03 | Jamendo APIで無料・商用可能BGMを検索する | 必須 |
| M5-04 | Epidemic Sound MCP Serverで商用BGMを検索する（契約時） | 推奨 |
| M5-05 | MMAudioで映像入力からAI BGMを自動生成する | 任意 |
| M5-06 | 検索結果をタイトル・プレビューURL・BPM・尺・ライセンス種別付きで表示する | 必須 |
| M5-07 | 選択した曲のダウンロードを実行する | 必須 |
| M5-08 | **【参考曲類似検索モード】** 参考曲のURL/ファイルから音響特徴（BPM・キー・ムード・楽器構成）を抽出し、類似曲を検索する | 推奨 |

#### 3.5.3 制約事項
- MotionArrayはAPI自動化が利用規約で禁止（手動検索を継続）
- Artlist Enterprise APIは要見積もり（既存契約では手動検索）
- 各音源のライセンス（CC/商用可/帰属表記）を結果に明示する
- 参考曲類似検索の精度は音響特徴抽出エンジンに依存する

#### 3.5.4 受入テスト基準

| 項目 | 基準 |
|------|------|
| 壁打ちモード | 構造化されたクエリで関連BGMが上位10件に含まれる |
| 参考曲類似検索 | 入力曲と同ジャンル・類似BPMの曲が結果に含まれる |
| API応答速度 | 検索結果を10秒以内に返す |

---

### 3.6 Module 6: 過去動画素材の掘り起こし

#### 3.6.1 機能概要
Google Driveの全動画素材を自動文字起こし・インデックス化し、セマンティック検索を可能にする。

#### 3.6.2 機能要件

| ID | 要件 | 優先度 |
|----|------|-------|
| M6-01 | n8n Google Drive Triggerで新規動画ファイルを自動検出する | 必須 |
| M6-02 | FFmpegで動画から音声を抽出する（16kHz/モノラル） | 必須 |
| M6-02b | **VFR素材を検出し、CFR変換を先行実行する** | 必須 |
| M6-03 | kotoba-whisper v2.2（またはOpenAI Whisper API）で文字起こしする | 必須 |
| M6-04 | タイムスタンプ付きJSON + SRTファイルを出力する | 必須 |
| M6-05 | 文字起こしファイルをGoogle Driveの同じフォルダに自動保存する | 必須 |
| M6-06 | テキストをベクトル化してpgvectorに保存する | 必須 |
| M6-07 | メタデータ（ファイル名/フォルダパス/撮影日時/カメラ番号/話者情報）を登録する | 必須 |
| M6-08 | PySceneDetectでシーン分割し、**4x4グリッド代表フレーム**をCLIPベクトル化する | 推奨 |
| M6-09 | Claude Code上でセマンティック検索を実行する | 必須 |
| M6-10 | 検索結果をファイル名・タイムスタンプ・サムネイル・転写テキスト付きで表示する | 必須 |
| M6-11 | **pgvector DBにキャッシュがある場合は再処理をスキップする（Module 1と重複排除）** | 必須 |

#### 3.6.3 使用技術

| コンポーネント | 技術 |
|---|---|
| トリガー | n8n Google Drive Trigger (Hostinger Docker) |
| 音声抽出 | FFmpeg |
| VFR→CFR変換 | FFmpeg (-vsync cfr) |
| 文字起こし | kotoba-whisper v2.2 / OpenAI Whisper API |
| ベクトル化 | OpenAI text-embedding-3-small |
| DB | PostgreSQL + pgvector (Neon Serverless) |
| 映像ベクトル化 | CLIP / SigLIP（4x4グリッド） |
| 検索 | Claude Code セマンティック検索 |

#### 3.6.4 コスト見積もり（月100本・平均30分）

| コンポーネント | 費用/月 |
|---|---|
| n8n (Hostinger) | 既存契約内 |
| Whisper API (GPU非搭載時) | $18 |
| Embedding | $1〜2 |
| pgvector DB | $0〜19 |
| **合計** | **$20〜40/月** |

#### 3.6.5 受入テスト基準

| 項目 | 基準 |
|------|------|
| 新規動画検出 | アップロード後5分以内にパイプライン起動 |
| セマンティック検索精度 | 関連シーンが上位10件に含まれる確率80%以上 |
| 検索応答速度 | 5秒以内 |
| 重複排除 | Module 1処理済みファイルに対して再処理が発生しない |

---

### 3.7 Module 7: 動画素材自動ダウンロード

#### 3.7.1 機能概要
Google Driveの動画素材を、**電源接続を検知して自動**でローカルPCにダウンロードする。

#### 3.7.2 対応OS

| OS | メイン/サブ | トリガー方式 |
|----|-----------|------------|
| **macOS** | **メイン** | sleepwatcher / pmset 電源イベント監視 |
| Windows | サブ | Task Scheduler 電源イベントトリガー |

#### 3.7.3 機能要件

| ID | 要件 | 優先度 |
|----|------|-------|
| M7-01 | rcloneでGoogle Drive→ローカルフォルダの同期を行う | 必須 |
| M7-02 | サービスアカウントで認証する（OAuth不要） | 必須 |
| M7-03 | **電源接続を検知し、30分待機後に自動で同期を開始する** | 必須 |
| M7-04 | 4並列ダウンロード + 8並列チェックで高速化する | 必須 |
| M7-05 | 同期完了後にn8n Webhook (Hostinger) へ通知する | 必須 |
| M7-06 | 通知先（Slack/LINE/メール）にステータスを送信する | 推奨 |
| M7-07 | ログファイルを日付別に保存する | 必須 |
| M7-08 | エラー時に別途アラート通知を送信する | 推奨 |
| M7-09 | **macOS: sleepwatcher/pmsetで電源接続イベントを監視する** | 必須 |
| M7-10 | **Windows: Task Scheduler電源イベントトリガーで同期を開始する** | 必須 |
| M7-11 | **スリープ状態（蓋閉じ+電源接続）からの自動復帰に対応する** | 必須 |
| M7-12 | **同期完了後に再スリープする** | 推奨 |

#### 3.7.4 電源接続トリガーフロー

```
[macOS]
  退勤 → MacBook蓋閉じ（スリープ）
  → 移動（バッテリー駆動、スリープ継続）
  → 帰宅、電源ケーブル接続
  → sleepwatcher/pmset が電源接続を検知
  → 30分待機（接続安定確認）
  → rclone同期開始
  → 完了 → n8n Webhook通知 → 再スリープ

[Windows]
  退勤 → PC蓋閉じ（スリープ）
  → 帰宅、電源接続
  → Task Scheduler 電源イベントトリガー発火
  → 30分待機 → rclone同期開始
  → 完了 → 通知 → 再スリープ
```

#### 3.7.5 注意事項
- **スリープならOK、シャットダウン/休止状態はNG**
- macOS: 蓋閉じ時の動作が「スリープ」であること（休止状態はNG）
- 電源接続が必須（バッテリー駆動中は実行しない）
- 30分待機は電源接続の安定性確認のため（抜き差しによる誤動作防止）

#### 3.7.6 受入テスト基準

| 項目 | 基準 |
|------|------|
| 同期完了率 | 100GB以内のファイルを8時間以内に完了 |
| 電源トリガー | 電源接続後30分以内に同期が開始される |
| スリープ復帰 | 蓋閉じ+電源接続状態から自動復帰しDLが実行される |
| エラー通知 | エラー発生後5分以内にアラート送信 |
| ログ出力 | 同期ファイル数・サイズ・所要時間が記録されている |
| 再スリープ | 同期完了後にスリープ状態に戻る |

---

### 3.8 Module 8: 統合管理ダッシュボード

#### 3.8.1 機能概要
全ModuleをClaude Code上のスキルコマンドとして統合操作する。Phase 3以降はPremiere Pro UXPパネルとしてネイティブ統合する。

#### 3.8.2 コマンド体系（Phase 1〜2: Claude Code）

| コマンド | 実行Module | 機能 |
|---------|-----------|------|
| `/素材整理 [フォルダパス]` | Module 1 | シーン分割・分類・名称付け |
| `/カット [動画パス]` | Module 2 | 無音カット + 環境音カット + 話者分離 → JSON出力 → Premiere Pro一括適用 |
| `/参考検索 "[イメージ]"` | Module 3-A | YouTube/Vimeo/TwelveLabs検索（リファレンスチャンネル限定可） |
| `/参考取り込み [フォルダパス]` | Module 3-B | 自動録画ファイルの自動解析・蓄積 |
| `/参考ライブラリ "[検索クエリ]"` | Module 3-D | 蓄積した参考動画の壁打ち検索 |
| `/テロップ [動画パス]` | Module 4 | 文字起こし → テロップ候補 → SRT/MOGRT → Premiere Pro |
| `/BGM検索 "[イメージ]"` | Module 5 | 壁打ちモード: Freesound + Jamendo + Epidemic Sound 横断検索 |
| `/BGM類似 [URL/ファイル]` | Module 5 | 参考曲類似検索モード |
| `/素材検索 "[キーワード]"` | Module 6 | 過去素材のセマンティック検索 |
| `/ダウンロード [Driveパス]` | Module 7 | バックグラウンドダウンロード開始 |
| `/ステータス` | 全体 | 各Moduleの処理状況表示 |

#### 3.8.3 UXPパネル移行計画（Phase 3以降）

| 優先度 | UXPパネル化対象 | 理由 |
|--------|---------------|------|
| 高 | Module 2（カット） | Premiere Pro上でのプレビュー確認が最も必要 |
| 高 | Module 4（テロップ） | テロップ配置・修正のフィードバックループ短縮 |
| 中 | Module 1（素材整理） | サムネイルプレビュー表示 |
| 中 | Module 6（素材検索） | 検索結果のビジュアル表示 |
| 低 | その他 | Claude Codeで十分に機能 |

---

## 4. 非機能要件

### 4.1 パフォーマンス

| 項目 | 要件 |
|------|------|
| 文字起こし処理速度 | 30分動画を10分以内（GPU搭載時）/ 40分以内（API利用時） |
| シーン検出速度 | 30分動画を5分以内 |
| VFR→CFR変換速度 | 30分動画を3分以内 |
| 検索応答速度 | セマンティック検索結果を5秒以内に返す |
| 夜間同期 | 100GB以内のファイルを8時間以内に完了 |

### 4.2 可用性

| 項目 | 要件 |
|------|------|
| n8nワークフロー | Hostinger上で24時間稼働 |
| pgvector DB | Neon Serverlessの99.95% SLA |
| ローカル処理 | PC起動中またはスリープ時（電源接続トリガーで自動復帰） |

### 4.3 セキュリティ

| 項目 | 要件 |
|------|------|
| Google Drive認証 | サービスアカウント（鍵ファイルの安全な管理） |
| サービスアカウント権限 | 最小権限（指定フォルダのみ、削除権限なし） |
| 鍵ローテーション | 90日ごとにサービスアカウント鍵を更新 |
| APIキー管理 | 環境変数または.envファイル（.gitignoreで除外） |
| 録画データ | ローカル保存のみ、外部共有禁止 |
| 転写データ | 同一Google Driveフォルダ内に保存 |

### 4.4 保守性

| 項目 | 要件 |
|------|------|
| Photoshop ExtendScript→UXP移行 | 2026年9月以降段階的に対応 |
| Premiere Pro ExtendScript→UXP移行 | 同上 |
| kotoba-whisperバージョン管理 | pip/condaで管理、メジャー更新時に精度検証 |
| pgvectorスキーマ移行 | マイグレーションスクリプトで管理 |

---

## 5. pgvectorスキーマ設計

### 5.1 テーブル定義

```sql
-- 共通: 素材メタデータ
CREATE TABLE media_assets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  file_path TEXT NOT NULL,
  drive_id TEXT,
  source_type TEXT NOT NULL CHECK (source_type IN ('original', 'reference', 'download')),
  duration_sec FLOAT,
  resolution TEXT,
  fps FLOAT,
  cfr_converted BOOLEAN DEFAULT FALSE,
  recorded_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  version INTEGER DEFAULT 1  -- 楽観的ロック用
);

-- シーン分割結果
CREATE TABLE scenes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id UUID REFERENCES media_assets(id),
  scene_index INTEGER NOT NULL,
  start_tc TEXT NOT NULL,
  end_tc TEXT NOT NULL,
  duration_sec FLOAT,
  grid_image_path TEXT,         -- 4x4グリッド画像パス
  clip_embedding vector(512),    -- CLIPベクトル
  classification TEXT,
  auto_name TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  version INTEGER DEFAULT 1
);

-- 文字起こし
CREATE TABLE transcripts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id UUID REFERENCES media_assets(id),
  scene_id UUID REFERENCES scenes(id),
  text TEXT NOT NULL,
  text_embedding vector(1536),   -- OpenAI text-embedding-3-small
  start_tc TEXT NOT NULL,
  end_tc TEXT NOT NULL,
  speaker_id TEXT,
  speaker_role TEXT,             -- MC, ゲスト, スタッフ等
  identification_method TEXT,    -- voiceprint / llm_context / manual
  confidence FLOAT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- タグ
CREATE TABLE scene_tags (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scene_id UUID REFERENCES scenes(id),
  category TEXT NOT NULL,        -- 構成, 演出, 雰囲気, テロップスタイル, カメラワーク
  tag_name TEXT NOT NULL,
  source TEXT DEFAULT 'auto',    -- auto / manual
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 参考動画メタデータ
CREATE TABLE reference_metadata (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id UUID REFERENCES media_assets(id),
  program_name TEXT,
  broadcast_date DATE,
  channel TEXT,
  source_url TEXT,
  tier TEXT CHECK (tier IN ('auto_obs', 'youtube', 'manual_obs')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 処理キャッシュ（重複処理排除用）
CREATE TABLE processing_cache (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id UUID REFERENCES media_assets(id),
  process_type TEXT NOT NULL,    -- scene_detect, transcribe, clip_embed, etc.
  module_id TEXT NOT NULL,       -- module_1, module_2, etc.
  status TEXT DEFAULT 'completed',
  result_ref TEXT,               -- 結果参照先
  completed_at TIMESTAMPTZ DEFAULT NOW(),
  version INTEGER DEFAULT 1
);
```

### 5.2 インデックス定義

```sql
-- ベクトル検索用
CREATE INDEX idx_scenes_clip_embedding ON scenes
  USING ivfflat (clip_embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX idx_transcripts_text_embedding ON transcripts
  USING ivfflat (text_embedding vector_cosine_ops) WITH (lists = 100);

-- メタデータ検索用
CREATE INDEX idx_media_assets_source_type ON media_assets(source_type);
CREATE INDEX idx_media_assets_drive_id ON media_assets(drive_id);
CREATE INDEX idx_scenes_asset_id ON scenes(asset_id);
CREATE INDEX idx_transcripts_asset_id ON transcripts(asset_id);
CREATE INDEX idx_transcripts_speaker_id ON transcripts(speaker_id);
CREATE INDEX idx_scene_tags_category ON scene_tags(category);
CREATE INDEX idx_scene_tags_tag_name ON scene_tags(tag_name);
CREATE INDEX idx_processing_cache_asset_process ON processing_cache(asset_id, process_type);
```

### 5.3 楽観的ロック方式

```sql
-- 更新時にバージョンチェック
UPDATE scenes
SET classification = '新分類', version = version + 1, updated_at = NOW()
WHERE id = $1 AND version = $2;
-- affected_rows = 0 → 競合発生 → リトライ
```

---

## 6. ストレージ計画

### 6.1 月間ストレージ増加量見積もり

| データ種別 | 月間増加量 | 備考 |
|-----------|----------|------|
| 撮影素材（ローカル同期） | 100〜500 GB | Module 7ダウンロード分 |
| 参考動画録画（自動OBS録画） | 20〜50 GB | 週5本×1時間×2〜5GB |
| シーン分割クリップ | 撮影素材の10〜30% | 元素材の部分コピー |
| 4x4グリッド画像 | 1〜5 GB | 1枚300KB×数千シーン |
| 文字起こしファイル（SRT/JSON） | 100〜500 MB | テキストデータ |
| pgvectorデータ | 500MB〜2GB | ベクトル+メタデータ |
| **合計** | **150〜600 GB/月** | |

### 6.2 ストレージ管理方針

| 方針 | 内容 |
|------|------|
| ローカルSSD | 最低2TB推奨（3〜4ヶ月分） |
| アーカイブ | 3ヶ月以上前の素材はGoogle Driveのみに保持 |
| 自動クリーンアップ | 配信済み動画の中間ファイル（グリッド画像・分割クリップ）を30日後に自動削除 |
| pgvector | Neon Serverless無料枠（500MB）→ 超過時にProプラン（$19/月・10GB） |

---

## 7. 外部連携

### 7.1 API・サービス一覧

| サービス | 用途 | 認証方式 | 費用 |
|---------|------|---------|------|
| Google Drive API | ファイル管理 | サービスアカウント（最小権限） | 無料枠内 |
| OpenAI Whisper API | 文字起こし（GPU非搭載時） | APIキー | $0.006/分 |
| OpenAI Embedding API | ベクトル検索 | APIキー | $0.02/1Mトークン |
| Gemini Flash API | 映像分析・タグ付け（4x4グリッド入力） | APIキー | 低コスト |
| YouTube Data API v3 | 参考動画検索（リファレンスチャンネル限定可） | APIキー | 無料（10,000ユニット/日） |
| Freesound.org API | 無料SE/BGM検索 | APIキー | 無料 |
| Jamendo API | 無料BGM検索 | APIキー | 無料 |
| Epidemic Sound MCP | BGM検索・DL（契約時） | APIキー | サブスク |
| pyannoteAI API | レギュラー話者識別Voiceprint | APIキー | €19/月（125時間） |
| Neon Serverless | pgvector DB | 接続文字列 | 無料〜$19/月 |

### 7.2 MCP サーバー

| MCP | 用途 |
|-----|------|
| YouTube MCP Server | YouTube検索（APIキー不要版） |
| Google Drive MCP Server | ファイル管理 |
| subtitle-mcp | 字幕ファイル管理 |
| Epidemic Sound MCP | BGM検索（契約時） |
| playwright MCP | ブラウザ自動操作（Module 3-B 自動録画） |

---

## 8. 導入ロードマップ

### Phase 1: 即日〜1週間（Module 6 + Module 7 基盤構築）

| タスク | 内容 | 担当 |
|--------|------|------|
| P1-01 | rclone + 電源接続トリガー同期設定（macOS sleepwatcher / Windows Task Scheduler）（Module 7） | 開発 |
| P1-02 | n8n Google Drive Trigger → Whisper → pgvector パイプライン構築（Module 6） | 開発 |
| P1-03 | kotoba-whisper環境構築・文字起こしテスト | 開発 |
| P1-04 | **pgvectorスキーマ構築・楽観的ロック実装** | 開発 |
| P1-05 | **VFR素材検出・CFR変換スクリプト構築** | 開発 |
| P1-06 | Claude Code セマンティック検索インターフェース構築（Module 6） | 開発 |

### Phase 2: 1〜2週間（Module 3 参考動画リサーチ・録画・解析）

| タスク | 内容 | 担当 |
|--------|------|------|
| P2-01 | YouTube検索 + リファレンスチャンネル限定検索構築（Module 3-A） | 開発 |
| P2-02 | Playwright + OBS 自動録画オーケストレーション実装（Module 3-B Tier 1） | 開発 |
| P2-03 | yt-dlp 自動ダウンロードパイプライン構築（Module 3-B Tier 2） | 開発 |
| P2-04 | 手動フォールバック自動検出フロー構築（Module 3-B Tier 3） | 開発 |
| P2-05 | PySceneDetect シーン分割 + CLIPクラスタリング + 4x4グリッド生成（Module 3-C） | 開発 |
| P2-06 | 参考動画ライブラリ壁打ち検索機能構築（Module 3-D） | 開発 |

### Phase 3: 2〜4週間（残Moduleのコア機能構築）

| タスク | 内容 | 担当 |
|--------|------|------|
| P3-01 | auto-editor導入・**JSON中間フォーマット出力テスト**（Module 2） | 開発 |
| P3-02 | **WhisperX + pyannote話者分離 + LLMテキスト文脈判定ハイブリッド実装**（Module 2） | 開発 |
| P3-03 | Silero VAD + YAMNet 環境音検出・自動カット実装（Module 2） | 開発 |
| P3-04 | **SRT→Premiere Proキャプショントラック流し込みテスト**（Module 4） | 開発 |
| P3-05 | PySceneDetect + CLIPクラスタリング + 素材分類（Module 1） | 開発 |
| P3-06 | BGM横断検索（壁打ちモード + **参考曲類似検索モード**）構築（Module 5） | 開発 |

### Phase 4: 1〜2ヶ月（統合・最適化）

| タスク | 内容 | 担当 |
|--------|------|------|
| P4-01 | Claude Codeカスタムスキル統合（全Moduleのコマンド化） | 開発 |
| P4-02 | **MOGRTテンプレートライブラリ構築（TV番組ジャンル別）** | 開発+制作 |
| P4-03 | **Premiere Pro UXPパネル開発開始（Module 2/4優先）** | 開発 |
| P4-04 | **UXPパネル完成・Premiere Proネイティブ統合** | 開発 |
| P4-05 | 全体テスト・フィードバック反映・運用ドキュメント作成 | 開発+制作 |

---

## 9. リスク・制約事項

| リスク | 影響度 | 対策 |
|--------|-------|------|
| GPU非搭載でローカルWhisperが遅い | 中 | クラウドAPI（$20〜40/月）で代替 |
| Photoshop ExtendScript廃止（2026年9月以降） | 中 | UXPへの段階的移行計画 |
| TVer/ABEMAのDRM（Widevine）による録画不可 | 高 | DRM対策（HWアクセラレーション無効化+OBS Studioウィンドウキャプチャ）を実装。失敗時はTier 3手動フォールバック |
| TVer/ABEMAの利用規約変更 | 低 | 録画機能を停止可能な設計、私的使用目的のみ |
| MotionArray API自動化不可 | 低 | Epidemic Sound MCP/手動検索で対応 |
| pyannoteAI API料金変更 | 低 | OSS版（HuggingFace）への切り替え可能 |
| Google Drive APIクォータ超過 | 低 | ポーリング間隔調整、rclone併用 |
| pgvector Race Condition | 中 | 楽観的ロック（バージョニング）で対策 |
| VFR素材のタイムコードズレ | 中 | 取り込み時にCFR自動変換 |
| After Effects未契約時のMOGRT生成不可 | 中 | Phase 1はSRTキャプションで運用可能 |
| ローカルストレージ容量不足 | 中 | 月150〜600GB増加想定、2TB SSD推奨+自動クリーンアップ |

---

## 10. 月額運用コスト見積もり

| 構成パターン | 月額 | 含まれるもの |
|---|---|---|
| **ローカル完結（GPU搭載時）** | **$0〜19** | pgvector DBのみ有料の可能性 |
| **推奨構成（API活用）** | **$40〜80** | Whisper API + Embedding + pgvector + pyannoteAI |
| **フル構成** | **$100〜200** | 上記 + Epidemic Sound + TwelveLabs |

※ Adobe CC契約・Hostinger契約・MotionArray/Artlist契約は既存費用のため含まず

---

## 11. 確定事項サマリー

### 11.1 UX方式

**2層段階方式で確定済み。**

| Phase | UX |
|-------|-----|
| Phase 1〜2 | Claude Code デスクトップアプリ（チャットUI） |
| Phase 3〜 | Premiere Pro UXPパネル |

Web中間層（Streamlit/Gradio）は構築しない。Claude CodeからPremiere Pro UXP連携はExtendScript/UXP CLI呼び出しで実現。

### 11.2 Module 3-B

**完全自動に確定。DRM対策を組み込み。**

| Tier | 方式 |
|------|------|
| Tier 1（メイン） | Playwright + OBS自動録画（ハードウェアアクセラレーション無効化+ウィンドウキャプチャ） |
| Tier 2（推奨） | YouTube公式 → yt-dlp自動DL |
| Tier 3（フォールバック） | OBS手動録画 → 指定フォルダ配置 → 自動解析 |

### 11.3 アーキテクチャの将来スケール

pgvector共有+楽観的ロックを現時点で採用。将来的にチーム規模・処理量が大幅に増加する場合はPub/Subモデルへの移行を検討する。

### 11.4 システム全体の死活監視

n8n自体のダウン検知・アラート機構（Hostinger上のn8nがダウンした場合の検知・自動復旧手段）を今後検討する。

---

## 変更履歴

| 日付 | 変更内容 |
|------|---------|
| 2026-03-31 | 初版作成 |
| 2026-03-31 | ai-debate討論結果反映（Opus vs Gemini 3.1 Pro 5往復） |
| 2026-03-31 | 最終確定事項を反映（全6件の変更を適用） |

### 討論反映版からの主な変更点（最終確定版）

1. Module 3-B: 半自動（手動録画+自動解析）→ **完全自動に戻す**。Tier 1はPlaywright + OBS自動録画（DRM対策組み込み）
2. Module 2: 無音閾値を2秒→**3秒**に変更。受入テスト基準も同様に変更
3. Module 2: 環境音検出・自動カット機能を追加（M2-11〜M2-14、Silero VAD + YAMNet/PANNs）
4. 未確定事項: Epidemic Sound「回答待ち」→**現時点ではなし**、優先Module「回答待ち」→**Module 6→7→3の順**、UX方式を削除（確定済み）
5. 導入ロードマップ: Phase 1をModule 6+7基盤構築、Phase 2をModule 3に変更（優先Module順に合わせて再構成）
6. Section 11: 「検討事項（未解決争点）」→「確定事項サマリー」に変更。UX方式・Module 3-Bを確定済みとして更新

### 討論反映版（refined-proposal）からの継続変更点

1. テロップフロー: PNG廃止→SRTキャプション→MOGRT→PS初期デザインの3段階に変更
2. タイムコード: 単一JSON中間フォーマットに統合
3. 代表フレーム: 複数フレームグリッド画像（4x4タイル）+CLIPクラスタリング
4. UX: 2層（Claude Code→UXP）に変更。Web中間層を削除
5. 話者分離: レギュラー声紋+ゲストLLM文脈判定のハイブリッド
6. BGM検索: 壁打ちモード+参考曲URL類似検索の2系統
7. pgvectorスキーマ設計セクション追加（テーブル定義・インデックス・楽観的ロック）
8. 受入テスト基準を各Moduleに追加
9. エラーハンドリング共通方針を追加
10. ストレージ計画を追加（月150〜600GB増加想定）
11. VFR（可変フレームレート）対策を追加
12. サービスアカウント最小権限設定を追加
13. アーキテクチャ: 共有キャッシュ型+楽観的ロック
14. YouTube検索: リファレンスチャンネル限定検索追加
