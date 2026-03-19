import sqlite3
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

DB_PATH = Path.home() / ".clipd" / "history.db"


class Database:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS clips (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                type         TEXT    NOT NULL,
                content      BLOB    NOT NULL,
                text_content TEXT,
                ocr_text     TEXT    DEFAULT '',
                hash         TEXT    NOT NULL UNIQUE,
                pinned       INTEGER DEFAULT 0,
                tags         TEXT    DEFAULT '',
                created_at   REAL    NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS clips_fts USING fts5(
                text_content,
                ocr_text,
                content=clips,
                content_rowid=id
            );

            CREATE TRIGGER IF NOT EXISTS clips_ai AFTER INSERT ON clips BEGIN
                INSERT INTO clips_fts(rowid, text_content, ocr_text)
                VALUES (new.id, new.text_content, new.ocr_text);
            END;

            CREATE TRIGGER IF NOT EXISTS clips_ad AFTER DELETE ON clips BEGIN
                INSERT INTO clips_fts(clips_fts, rowid, text_content, ocr_text)
                VALUES ('delete', old.id, old.text_content, old.ocr_text);
            END;

            CREATE TRIGGER IF NOT EXISTS clips_au AFTER UPDATE ON clips BEGIN
                INSERT INTO clips_fts(clips_fts, rowid, text_content, ocr_text)
                VALUES ('delete', old.id, old.text_content, old.ocr_text);
                INSERT INTO clips_fts(rowid, text_content, ocr_text)
                VALUES (new.id, new.text_content, new.ocr_text);
            END;
        """)
        self.conn.commit()

    def insert(self, clip_type: str, content: bytes, ocr_text: str, hash_: str) -> Optional[int]:
        text_content = content.decode("utf-8", errors="replace") if clip_type == "text" else None
        try:
            cur = self.conn.execute(
                "INSERT INTO clips (type, content, text_content, ocr_text, hash, created_at) VALUES (?,?,?,?,?,?)",
                (clip_type, content, text_content, ocr_text, hash_, time.time()),
            )
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            # Duplicate hash — just bump the timestamp so it surfaces at top
            self.conn.execute(
                "UPDATE clips SET created_at=? WHERE hash=?", (time.time(), hash_)
            )
            self.conn.commit()
            return None

    def list(
        self,
        limit: int = 20,
        clip_type: Optional[str] = None,
        tag: Optional[str] = None,
        pinned_only: bool = False,
    ) -> List[sqlite3.Row]:
        q = "SELECT id, type, text_content, ocr_text, pinned, tags, created_at FROM clips WHERE 1=1"
        params: list = []
        if clip_type:
            q += " AND type=?"
            params.append(clip_type)
        if tag:
            q += " AND (',' || tags || ',') LIKE ?"
            params.append(f"%,{tag},%")
        if pinned_only:
            q += " AND pinned=1"
        q += " ORDER BY pinned DESC, created_at DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(q, params).fetchall()

    def search(self, query: str, limit: int = 20) -> List[sqlite3.Row]:
        try:
            return self.conn.execute(
                """
                SELECT c.id, c.type, c.text_content, c.ocr_text, c.pinned, c.tags, c.created_at,
                       snippet(clips_fts, 0, '<b>', '</b>', '…', 20) as snippet_text,
                       snippet(clips_fts, 1, '<b>', '</b>', '…', 20) as snippet_ocr
                FROM clips_fts
                JOIN clips c ON c.id = clips_fts.rowid
                WHERE clips_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Fallback to LIKE search when FTS query syntax is invalid
            like = f"%{query}%"
            rows = self.conn.execute(
                """SELECT id, type, text_content, ocr_text, pinned, tags, created_at,
                          NULL as snippet_text, NULL as snippet_ocr
                   FROM clips
                   WHERE text_content LIKE ? OR ocr_text LIKE ?
                   ORDER BY created_at DESC LIMIT ?""",
                (like, like, limit),
            ).fetchall()
            return rows

    def get(self, id_: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM clips WHERE id=?", (id_,)).fetchone()

    def delete(self, id_: int) -> bool:
        cur = self.conn.execute("DELETE FROM clips WHERE id=?", (id_,))
        self.conn.commit()
        return cur.rowcount > 0

    def pin(self, id_: int, value: bool = True) -> bool:
        cur = self.conn.execute("UPDATE clips SET pinned=? WHERE id=?", (int(value), id_))
        self.conn.commit()
        return cur.rowcount > 0

    def tag(self, id_: int, tag_name: str) -> bool:
        row = self.conn.execute("SELECT tags FROM clips WHERE id=?", (id_,)).fetchone()
        if not row:
            return False
        existing = [t for t in row["tags"].split(",") if t]
        if tag_name not in existing:
            existing.append(tag_name)
        self.conn.execute("UPDATE clips SET tags=? WHERE id=?", (",".join(existing), id_))
        self.conn.commit()
        return True

    def untag(self, id_: int, tag_name: str) -> bool:
        row = self.conn.execute("SELECT tags FROM clips WHERE id=?", (id_,)).fetchone()
        if not row:
            return False
        existing = [t for t in row["tags"].split(",") if t and t != tag_name]
        self.conn.execute("UPDATE clips SET tags=? WHERE id=?", (",".join(existing), id_))
        self.conn.commit()
        return True

    def clear(self, before_ts: Optional[float] = None) -> int:
        if before_ts:
            cur = self.conn.execute(
                "DELETE FROM clips WHERE pinned=0 AND created_at<?", (before_ts,)
            )
        else:
            cur = self.conn.execute("DELETE FROM clips WHERE pinned=0")
        self.conn.commit()
        return cur.rowcount

    def latest_after(self, after_ts: float) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, type, text_content, ocr_text, created_at FROM clips WHERE created_at>? ORDER BY created_at ASC",
            (after_ts,),
        ).fetchall()

    def stats(self) -> Dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT
                COUNT(*)                                          AS total,
                SUM(CASE WHEN type='text'  THEN 1 ELSE 0 END)   AS text_count,
                SUM(CASE WHEN type='image' THEN 1 ELSE 0 END)   AS image_count,
                SUM(CASE WHEN pinned=1     THEN 1 ELSE 0 END)   AS pinned_count,
                SUM(LENGTH(content))                             AS total_size,
                MIN(created_at)                                  AS oldest,
                MAX(created_at)                                  AS newest
            FROM clips
            """
        ).fetchone()
        return dict(row)
