"""
Database layer for tracking conversion state and results.

Uses SQLite to persist:
- Conversion jobs (each screenshot → drawio conversion)
- Classification results
- C4 model metadata
- Quality scores and review status
"""

import os
import json
import sqlite3
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class ConversionDB:
    """SQLite database for tracking the conversion pipeline."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self):
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                source_name TEXT NOT NULL,
                space_key TEXT DEFAULT '',
                page_title TEXT DEFAULT '',
                page_id TEXT DEFAULT '',
                confluence_url TEXT DEFAULT '',

                -- Classification
                diagram_type TEXT DEFAULT 'unknown',
                classification_confidence REAL DEFAULT 0.0,
                is_system_diagram INTEGER DEFAULT 0,
                c4_convertible INTEGER DEFAULT 0,
                description TEXT DEFAULT '',
                key_elements TEXT DEFAULT '[]',

                -- DrawIO conversion
                drawio_path TEXT DEFAULT '',
                drawio_status TEXT DEFAULT 'pending',
                drawio_confidence REAL DEFAULT 0.0,
                shape_count INTEGER DEFAULT 0,
                connection_count INTEGER DEFAULT 0,
                text_elements TEXT DEFAULT '[]',
                drawio_error TEXT DEFAULT '',

                -- C4 conversion
                c4_path TEXT DEFAULT '',
                c4_status TEXT DEFAULT 'pending',
                c4_level TEXT DEFAULT '',
                c4_system_count INTEGER DEFAULT 0,
                c4_relationship_count INTEGER DEFAULT 0,
                c4_error TEXT DEFAULT '',

                -- Quality & review
                quality_score REAL DEFAULT 0.0,
                review_status TEXT DEFAULT 'pending',
                review_notes TEXT DEFAULT '',

                -- Tracking
                tokens_used INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),

                UNIQUE(source_path)
            );

            CREATE INDEX IF NOT EXISTS idx_conv_status ON conversions(drawio_status);
            CREATE INDEX IF NOT EXISTS idx_conv_type ON conversions(diagram_type);
            CREATE INDEX IF NOT EXISTS idx_conv_space ON conversions(space_key);
            CREATE INDEX IF NOT EXISTS idx_conv_review ON conversions(review_status);
            CREATE INDEX IF NOT EXISTS idx_conv_c4 ON conversions(c4_convertible, c4_status);

            CREATE TABLE IF NOT EXISTS c4_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversion_id INTEGER NOT NULL,
                c4_level TEXT NOT NULL,
                title TEXT DEFAULT '',
                description TEXT DEFAULT '',
                model_json TEXT NOT NULL,
                drawio_c4_path TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (conversion_id) REFERENCES conversions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_c4_level ON c4_models(c4_level);

            CREATE TABLE IF NOT EXISTS c4_systems (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                system_id TEXT NOT NULL,
                name TEXT NOT NULL,
                type TEXT DEFAULT 'system',
                description TEXT DEFAULT '',
                technology TEXT DEFAULT '',
                is_external INTEGER DEFAULT 0,
                tags TEXT DEFAULT '[]',
                FOREIGN KEY (model_id) REFERENCES c4_models(id)
            );

            CREATE INDEX IF NOT EXISTS idx_c4sys_name ON c4_systems(name);
            CREATE INDEX IF NOT EXISTS idx_c4sys_type ON c4_systems(type);

            CREATE TABLE IF NOT EXISTS c4_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                source_system_id TEXT NOT NULL,
                target_system_id TEXT NOT NULL,
                description TEXT DEFAULT '',
                technology TEXT DEFAULT '',
                FOREIGN KEY (model_id) REFERENCES c4_models(id)
            );

            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_type TEXT NOT NULL,
                started_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT,
                total_items INTEGER DEFAULT 0,
                processed INTEGER DEFAULT 0,
                succeeded INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                skipped INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                error TEXT DEFAULT ''
            );
        """)
        conn.commit()
        conn.close()

    # ── Conversion records ──────────────────────────────────────────

    def upsert_conversion(self, source_path: str, **kwargs) -> int:
        """Insert or update a conversion record. Returns the record ID."""
        conn = self._connect()

        existing = conn.execute(
            "SELECT id FROM conversions WHERE source_path = ?",
            (source_path,)
        ).fetchone()

        if existing:
            record_id = existing["id"]
            if kwargs:
                sets = ", ".join(f"{k} = ?" for k in kwargs)
                sets += ", updated_at = datetime('now')"
                vals = list(kwargs.values()) + [record_id]
                conn.execute(f"UPDATE conversions SET {sets} WHERE id = ?", vals)
                conn.commit()
        else:
            source_name = os.path.splitext(os.path.basename(source_path))[0]
            kwargs.setdefault("source_name", source_name)
            kwargs["source_path"] = source_path

            cols = ", ".join(kwargs.keys())
            placeholders = ", ".join("?" for _ in kwargs)
            conn.execute(
                f"INSERT INTO conversions ({cols}) VALUES ({placeholders})",
                list(kwargs.values()),
            )
            conn.commit()
            record_id = conn.execute(
                "SELECT id FROM conversions WHERE source_path = ?",
                (source_path,)
            ).fetchone()["id"]

        conn.close()
        return record_id

    def get_conversion(self, source_path: str) -> Optional[dict]:
        """Get a conversion record by source path."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM conversions WHERE source_path = ?",
            (source_path,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_conversion_by_id(self, record_id: int) -> Optional[dict]:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM conversions WHERE id = ?", (record_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_pending_conversions(self, limit: int = 100) -> list[dict]:
        """Get conversions that haven't been processed yet."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM conversions WHERE drawio_status = 'pending' "
            "ORDER BY id LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_c4_candidates(self, limit: int = 100) -> list[dict]:
        """Get conversions ready for C4 conversion."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM conversions "
            "WHERE c4_convertible = 1 AND drawio_status = 'success' "
            "AND c4_status = 'pending' "
            "ORDER BY drawio_confidence DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_review_queue(self, limit: int = 100) -> list[dict]:
        """Get conversions needing manual review."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM conversions "
            "WHERE review_status = 'needs_review' "
            "ORDER BY quality_score ASC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── C4 models ───────────────────────────────────────────────────

    def save_c4_model(self, conversion_id: int, model_dict: dict,
                      drawio_c4_path: str = "") -> int:
        """Save a C4 model and its systems/relationships."""
        conn = self._connect()

        conn.execute(
            "INSERT INTO c4_models (conversion_id, c4_level, title, description, "
            "model_json, drawio_c4_path) VALUES (?, ?, ?, ?, ?, ?)",
            (
                conversion_id,
                model_dict.get("c4_level", ""),
                model_dict.get("title", ""),
                model_dict.get("description", ""),
                json.dumps(model_dict),
                drawio_c4_path,
            ),
        )
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        for sys in model_dict.get("systems", []):
            conn.execute(
                "INSERT INTO c4_systems (model_id, system_id, name, type, "
                "description, technology, is_external, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    model_id,
                    sys.get("id", ""),
                    sys.get("name", ""),
                    sys.get("type", "system"),
                    sys.get("description", ""),
                    sys.get("technology", ""),
                    1 if sys.get("is_external") else 0,
                    json.dumps(sys.get("tags", [])),
                ),
            )

        for rel in model_dict.get("relationships", []):
            conn.execute(
                "INSERT INTO c4_relationships (model_id, source_system_id, "
                "target_system_id, description, technology) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    model_id,
                    rel.get("source_id", ""),
                    rel.get("target_id", ""),
                    rel.get("description", ""),
                    rel.get("technology", ""),
                ),
            )

        conn.commit()
        conn.close()
        return model_id

    def get_all_c4_models(self) -> list[dict]:
        """Get all C4 models with metadata."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT m.*, c.source_name, c.space_key, c.page_title "
            "FROM c4_models m "
            "JOIN conversions c ON m.conversion_id = c.id "
            "ORDER BY m.title"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_c4_model(self, model_id: int) -> Optional[dict]:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM c4_models WHERE id = ?", (model_id,)
        ).fetchone()
        conn.close()
        if row:
            result = dict(row)
            result["model"] = json.loads(result["model_json"])
            return result
        return None

    # ── Pipeline runs ───────────────────────────────────────────────

    def start_pipeline_run(self, run_type: str, total_items: int) -> int:
        conn = self._connect()
        conn.execute(
            "INSERT INTO pipeline_runs (run_type, total_items) VALUES (?, ?)",
            (run_type, total_items),
        )
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return run_id

    def update_pipeline_run(self, run_id: int, **kwargs):
        conn = self._connect()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(
            f"UPDATE pipeline_runs SET {sets} WHERE id = ?",
            list(kwargs.values()) + [run_id],
        )
        conn.commit()
        conn.close()

    def complete_pipeline_run(self, run_id: int, **kwargs):
        kwargs["completed_at"] = datetime.now().isoformat()
        kwargs["status"] = kwargs.get("status", "completed")
        self.update_pipeline_run(run_id, **kwargs)

    # ── Statistics ──────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get overall pipeline statistics."""
        conn = self._connect()

        total = conn.execute("SELECT COUNT(*) FROM conversions").fetchone()[0]
        by_status = conn.execute(
            "SELECT drawio_status, COUNT(*) as cnt FROM conversions GROUP BY drawio_status"
        ).fetchall()
        by_type = conn.execute(
            "SELECT diagram_type, COUNT(*) as cnt FROM conversions GROUP BY diagram_type"
        ).fetchall()
        by_review = conn.execute(
            "SELECT review_status, COUNT(*) as cnt FROM conversions GROUP BY review_status"
        ).fetchall()

        c4_total = conn.execute("SELECT COUNT(*) FROM c4_models").fetchone()[0]
        c4_by_level = conn.execute(
            "SELECT c4_level, COUNT(*) as cnt FROM c4_models GROUP BY c4_level"
        ).fetchall()

        total_tokens = conn.execute(
            "SELECT COALESCE(SUM(tokens_used), 0) FROM conversions"
        ).fetchone()[0]

        avg_confidence = conn.execute(
            "SELECT COALESCE(AVG(drawio_confidence), 0) FROM conversions "
            "WHERE drawio_status = 'success'"
        ).fetchone()[0]

        unique_systems = conn.execute(
            "SELECT COUNT(DISTINCT name) FROM c4_systems"
        ).fetchone()[0]
        unique_techs = conn.execute(
            "SELECT COUNT(DISTINCT technology) FROM c4_systems "
            "WHERE technology != ''"
        ).fetchone()[0]

        conn.close()

        return {
            "total_conversions": total,
            "by_status": {r["drawio_status"]: r["cnt"] for r in by_status},
            "by_type": {r["diagram_type"]: r["cnt"] for r in by_type},
            "by_review": {r["review_status"]: r["cnt"] for r in by_review},
            "c4_models": c4_total,
            "c4_by_level": {r["c4_level"]: r["cnt"] for r in c4_by_level},
            "total_tokens": total_tokens,
            "avg_confidence": round(avg_confidence, 3),
            "unique_systems": unique_systems,
            "unique_technologies": unique_techs,
        }

    def search_conversions(self, query: str, limit: int = 50) -> list[dict]:
        """Search conversions by name, type, or page title."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM conversions WHERE "
            "source_name LIKE ? OR page_title LIKE ? OR description LIKE ? "
            "ORDER BY quality_score DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_conversions(self, space_key: str = "",
                            diagram_type: str = "",
                            status: str = "",
                            limit: int = 500,
                            offset: int = 0) -> list[dict]:
        """Get conversions with optional filters."""
        conn = self._connect()
        conditions = []
        params = []

        if space_key:
            conditions.append("space_key = ?")
            params.append(space_key)
        if diagram_type:
            conditions.append("diagram_type = ?")
            params.append(diagram_type)
        if status:
            conditions.append("drawio_status = ?")
            params.append(status)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        rows = conn.execute(
            f"SELECT * FROM conversions WHERE {where} "
            f"ORDER BY source_name LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
