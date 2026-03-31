"""Direction - Database Client for pgvector (Neon Serverless)"""

import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from pgvector.psycopg2 import register_vector
from dotenv import load_dotenv

load_dotenv()


class DirectionDB:
    def __init__(self, database_url=None):
        self.database_url = database_url or os.getenv("DATABASE_URL")
        self.conn = None

    def connect(self):
        self.conn = psycopg2.connect(self.database_url)
        register_vector(self.conn)
        return self

    def close(self):
        if self.conn:
            self.conn.close()

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        self.close()

    # ---- media_assets ----

    def upsert_media_asset(self, file_path, file_name, drive_id=None,
                           drive_folder_path=None, source_type="original",
                           duration_sec=None, resolution=None, fps=None,
                           codec=None, file_size_bytes=None,
                           cfr_converted=False, recorded_at=None):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO media_assets
                    (file_path, file_name, drive_id, drive_folder_path,
                     source_type, duration_sec, resolution, fps, codec,
                     file_size_bytes, cfr_converted, recorded_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                RETURNING *
            """, (file_path, file_name, drive_id, drive_folder_path,
                  source_type, duration_sec, resolution, fps, codec,
                  file_size_bytes, cfr_converted, recorded_at))
            self.conn.commit()
            return cur.fetchone()

    def find_asset_by_drive_id(self, drive_id):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM media_assets WHERE drive_id = %s", (drive_id,))
            return cur.fetchone()

    def find_asset_by_path(self, file_path):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM media_assets WHERE file_path = %s", (file_path,))
            return cur.fetchone()

    # ---- processing_cache ----

    def is_processed(self, asset_id, process_type, module_id="module_6"):
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM processing_cache
                WHERE asset_id = %s AND process_type = %s
                  AND module_id = %s AND status = 'completed'
            """, (asset_id, process_type, module_id))
            return cur.fetchone() is not None

    def set_processing(self, asset_id, process_type, module_id="module_6"):
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processing_cache
                    (asset_id, process_type, module_id, status, completed_at)
                VALUES (%s, %s, %s, 'processing', NOW())
                ON CONFLICT (asset_id, process_type, module_id)
                DO UPDATE SET status = 'processing', completed_at = NOW()
            """, (asset_id, process_type, module_id))
            self.conn.commit()

    def set_completed(self, asset_id, process_type, module_id="module_6", result_ref=None):
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processing_cache
                    (asset_id, process_type, module_id, status, result_ref, completed_at)
                VALUES (%s, %s, %s, 'completed', %s, NOW())
                ON CONFLICT (asset_id, process_type, module_id)
                DO UPDATE SET status = 'completed', result_ref = %s, completed_at = NOW()
            """, (asset_id, process_type, module_id, result_ref, result_ref))
            self.conn.commit()

    def set_failed(self, asset_id, process_type, module_id="module_6", error_message=None):
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processing_cache
                    (asset_id, process_type, module_id, status, error_message, completed_at)
                VALUES (%s, %s, %s, 'failed', %s, NOW())
                ON CONFLICT (asset_id, process_type, module_id)
                DO UPDATE SET status = 'failed', error_message = %s, completed_at = NOW()
            """, (asset_id, process_type, module_id, error_message, error_message))
            self.conn.commit()

    # ---- transcripts ----

    def insert_transcript(self, asset_id, text, start_tc, end_tc,
                          start_sec, end_sec, text_embedding=None,
                          scene_id=None, speaker_id=None, speaker_role=None):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO transcripts
                    (asset_id, scene_id, text, text_embedding,
                     start_tc, end_tc, start_sec, end_sec,
                     speaker_id, speaker_role)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (asset_id, scene_id, text, text_embedding,
                  start_tc, end_tc, start_sec, end_sec,
                  speaker_id, speaker_role))
            self.conn.commit()
            return cur.fetchone()["id"]

    def bulk_insert_transcripts(self, records):
        with self.conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO transcripts
                    (asset_id, text, text_embedding, start_tc, end_tc,
                     start_sec, end_sec, speaker_id)
                VALUES %s
            """, records)
            self.conn.commit()

    # ---- scenes ----

    def insert_scene(self, asset_id, scene_index, start_tc, end_tc,
                     start_sec, end_sec, duration_sec=None,
                     grid_image_path=None, clip_embedding=None,
                     classification=None, auto_name=None):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO scenes
                    (asset_id, scene_index, start_tc, end_tc,
                     start_sec, end_sec, duration_sec,
                     grid_image_path, clip_embedding,
                     classification, auto_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (asset_id, scene_index) DO UPDATE SET
                    grid_image_path = EXCLUDED.grid_image_path,
                    clip_embedding = EXCLUDED.clip_embedding,
                    classification = EXCLUDED.classification,
                    auto_name = EXCLUDED.auto_name
                RETURNING id
            """, (asset_id, scene_index, start_tc, end_tc,
                  start_sec, end_sec, duration_sec,
                  grid_image_path, clip_embedding,
                  classification, auto_name))
            self.conn.commit()
            return cur.fetchone()["id"]

    # ---- semantic search ----

    def search_by_text(self, query_embedding, limit=10, source_type=None):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            if source_type:
                cur.execute("""
                    SELECT t.*, ma.file_path, ma.file_name, ma.drive_folder_path,
                           1 - (t.text_embedding <=> %s::vector) AS similarity
                    FROM transcripts t
                    JOIN media_assets ma ON t.asset_id = ma.id
                    WHERE t.text_embedding IS NOT NULL
                      AND ma.source_type = %s
                    ORDER BY t.text_embedding <=> %s::vector
                    LIMIT %s
                """, (query_embedding, source_type, query_embedding, limit))
            else:
                cur.execute("""
                    SELECT t.*, ma.file_path, ma.file_name, ma.drive_folder_path,
                           1 - (t.text_embedding <=> %s::vector) AS similarity
                    FROM transcripts t
                    JOIN media_assets ma ON t.asset_id = ma.id
                    WHERE t.text_embedding IS NOT NULL
                    ORDER BY t.text_embedding <=> %s::vector
                    LIMIT %s
                """, (query_embedding, query_embedding, limit))
            return cur.fetchall()

    def search_by_keyword(self, keyword, limit=10):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT t.*, ma.file_path, ma.file_name, ma.drive_folder_path
                FROM transcripts t
                JOIN media_assets ma ON t.asset_id = ma.id
                WHERE t.text ILIKE %s
                ORDER BY t.start_sec
                LIMIT %s
            """, (f"%{keyword}%", limit))
            return cur.fetchall()

    def search_by_clip(self, clip_embedding, limit=10):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT s.*, ma.file_path, ma.file_name, ma.drive_folder_path,
                       1 - (s.clip_embedding <=> %s::vector) AS similarity
                FROM scenes s
                JOIN media_assets ma ON s.asset_id = ma.id
                WHERE s.clip_embedding IS NOT NULL
                ORDER BY s.clip_embedding <=> %s::vector
                LIMIT %s
            """, (clip_embedding, clip_embedding, limit))
            return cur.fetchall()

    def init_schema(self, schema_path=None):
        if schema_path is None:
            schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        with open(schema_path, "r", encoding="utf-8") as f:
            sql = f.read()
        with self.conn.cursor() as cur:
            cur.execute(sql)
        self.conn.commit()
