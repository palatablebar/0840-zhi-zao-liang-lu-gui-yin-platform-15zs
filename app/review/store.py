import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.review.codec import dumps, loads


def _now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self):
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS runs(
              id TEXT PRIMARY KEY,
              request_key TEXT UNIQUE NOT NULL,
              created_at TEXT NOT NULL,
              profile TEXT NOT NULL,
              status TEXT NOT NULL,
              revision INTEGER NOT NULL,
              payload TEXT NOT NULL,
              comment TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS evidence(
              run_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              status TEXT NOT NULL,
              artifact TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(run_id, revision)
            );
            ''')

    def find_by_request(self, request_key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE request_key=?", (request_key,)).fetchone()
        return dict(row) if row else None

    def create(self, request_key, profile, payload):
        existing = self.find_by_request(request_key)
        if existing:
            return existing
        run_id = uuid.uuid4().hex[:12]
        with self.connect() as db:
            db.execute("INSERT INTO runs(id,request_key,created_at,profile,status,revision,payload) VALUES(?,?,?,?,?,?,?)",
                       (run_id, request_key, _now(), profile, "DRAFT", 0, dumps(payload)))
        return self.get(run_id)

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def review(self, run_id, decision, comment, expected_revision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None, "missing"
            if row["revision"] != expected_revision:
                return None, "stale"
            db.execute("UPDATE runs SET status=?, revision=revision+1, comment=? WHERE id=?",
                       (decision, comment, run_id))
        return self.get(run_id), None

    def apply_supplement(self, run_id, digest, expected_revision, plan):
        """Atomically append a supplement and replace the stored payload.

        ``plan`` receives the current raw payload and returns
        ``(new_payload, entry)``; it may raise ``DomainIssue`` to abort the
        whole update. A previously applied digest returns ``duplicate`` so the
        same report can never be counted twice. Only a signed batch accepts
        supplements.
        """
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                db.rollback()
                return None, "missing"
            if row["status"] != "APPROVED":
                db.rollback()
                return None, "not_approved"
            payload = loads(row["payload"])
            applied = payload.get("supplements") or []
            # 幂等优先：同一份补报重试（即便携带旧版本号）都返回当前结果，
            # 不重复计损，也不因客户端重试顺序不同而报陈旧。
            if any(item.get("digest") == digest for item in applied):
                db.rollback()
                return self.get(run_id), "duplicate"
            if expected_revision is not None and row["revision"] != expected_revision:
                db.rollback()
                return None, "stale"
            new_payload, entry = plan(payload)
            entry = {**entry, "digest": digest, "revision": row["revision"] + 1, "created_at": _now()}
            new_payload["supplements"] = list(applied) + [entry]
            db.execute("UPDATE runs SET payload=?, revision=revision+1 WHERE id=?",
                       (dumps(new_payload), run_id))
        return self.get(run_id), None

    def save_evidence(self, run_id, revision, status, artifact):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO evidence(run_id,revision,status,artifact,created_at) VALUES(?,?,?,?,?)",
                       (run_id, revision, status, dumps(artifact), _now()))

    def get_evidence(self, run_id, revision=None):
        with self.connect() as db:
            if revision is None:
                row = db.execute("SELECT * FROM evidence WHERE run_id=? ORDER BY revision DESC LIMIT 1",
                                 (run_id,)).fetchone()
            else:
                row = db.execute("SELECT * FROM evidence WHERE run_id=? AND revision=?",
                                 (run_id, revision)).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["artifact"] = loads(record["artifact"])
        return record

    def count_evidence(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT COUNT(*) AS total FROM evidence WHERE run_id=?", (run_id,)).fetchone()
        return int(row["total"])
