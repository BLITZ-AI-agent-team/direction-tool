-- Direction - pgvector Database Schema
-- Target: Neon Serverless PostgreSQL + pgvector
-- Version: 1.0.0

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 共通: 素材メタデータ
-- ============================================================
CREATE TABLE IF NOT EXISTS media_assets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  file_path TEXT NOT NULL,
  file_name TEXT NOT NULL,
  drive_id TEXT,
  drive_folder_path TEXT,
  source_type TEXT NOT NULL CHECK (source_type IN ('original', 'reference', 'download')),
  duration_sec FLOAT,
  resolution TEXT,
  fps FLOAT,
  codec TEXT,
  file_size_bytes BIGINT,
  cfr_converted BOOLEAN DEFAULT FALSE,
  recorded_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  version INTEGER DEFAULT 1
);

-- ============================================================
-- シーン分割結果
-- ============================================================
CREATE TABLE IF NOT EXISTS scenes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id UUID NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
  scene_index INTEGER NOT NULL,
  start_tc TEXT NOT NULL,
  end_tc TEXT NOT NULL,
  start_sec FLOAT NOT NULL,
  end_sec FLOAT NOT NULL,
  duration_sec FLOAT,
  grid_image_path TEXT,
  clip_embedding vector(512),
  classification TEXT,
  auto_name TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  version INTEGER DEFAULT 1,
  UNIQUE(asset_id, scene_index)
);

-- ============================================================
-- 文字起こし
-- ============================================================
CREATE TABLE IF NOT EXISTS transcripts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id UUID NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
  scene_id UUID REFERENCES scenes(id) ON DELETE SET NULL,
  text TEXT NOT NULL,
  text_embedding vector(1536),
  start_tc TEXT NOT NULL,
  end_tc TEXT NOT NULL,
  start_sec FLOAT NOT NULL,
  end_sec FLOAT NOT NULL,
  speaker_id TEXT,
  speaker_role TEXT,
  identification_method TEXT,
  confidence FLOAT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- タグ
-- ============================================================
CREATE TABLE IF NOT EXISTS scene_tags (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scene_id UUID NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
  category TEXT NOT NULL,
  tag_name TEXT NOT NULL,
  source TEXT DEFAULT 'auto',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(scene_id, category, tag_name)
);

-- ============================================================
-- 参考動画メタデータ
-- ============================================================
CREATE TABLE IF NOT EXISTS reference_metadata (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id UUID NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
  program_name TEXT,
  broadcast_date DATE,
  channel TEXT,
  source_url TEXT,
  tier TEXT CHECK (tier IN ('auto_obs', 'youtube', 'manual_obs')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 処理キャッシュ（重複処理排除用）
-- ============================================================
CREATE TABLE IF NOT EXISTS processing_cache (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  asset_id UUID NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
  process_type TEXT NOT NULL,
  module_id TEXT NOT NULL,
  status TEXT DEFAULT 'completed' CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
  result_ref TEXT,
  error_message TEXT,
  completed_at TIMESTAMPTZ DEFAULT NOW(),
  version INTEGER DEFAULT 1,
  UNIQUE(asset_id, process_type, module_id)
);

-- ============================================================
-- インデックス定義
-- ============================================================

-- ベクトル検索用（IVFFlat）
CREATE INDEX IF NOT EXISTS idx_scenes_clip_embedding ON scenes
  USING ivfflat (clip_embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_transcripts_text_embedding ON transcripts
  USING ivfflat (text_embedding vector_cosine_ops) WITH (lists = 100);

-- メタデータ検索用
CREATE INDEX IF NOT EXISTS idx_media_assets_source_type ON media_assets(source_type);
CREATE INDEX IF NOT EXISTS idx_media_assets_drive_id ON media_assets(drive_id);
CREATE INDEX IF NOT EXISTS idx_media_assets_file_name ON media_assets(file_name);
CREATE INDEX IF NOT EXISTS idx_scenes_asset_id ON scenes(asset_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_asset_id ON transcripts(asset_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_scene_id ON transcripts(scene_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_speaker_id ON transcripts(speaker_id);
CREATE INDEX IF NOT EXISTS idx_scene_tags_category ON scene_tags(category);
CREATE INDEX IF NOT EXISTS idx_scene_tags_tag_name ON scene_tags(tag_name);
CREATE INDEX IF NOT EXISTS idx_processing_cache_asset_process ON processing_cache(asset_id, process_type);

-- 全文検索用（日本語）
CREATE INDEX IF NOT EXISTS idx_transcripts_text_gin ON transcripts USING gin(to_tsvector('simple', text));
