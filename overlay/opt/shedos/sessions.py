"""Multi-session manager for the brain daemon.

Each session is one persistent conversation, identified by UUID. Messages
are stored as JSONL at /var/lib/shedos/sessions/<uuid>.jsonl. Metadata
(title, created_at, updated_at) lives at the top of the file as a special
first line ({"type":"meta", ...}). Append-only.
"""
import collections
import json
import os
import time
import uuid

import config


def _now():
    return int(time.time())


class Session:
    def __init__(self, sid, path, title="New chat", created_at=None):
        self.id = sid
        self.path = path
        self.title = title
        self.created_at = created_at or _now()
        self.updated_at = self.created_at
        self.messages = []  # list of {"role": ..., "content": ...} dicts

    @classmethod
    def create(cls, sessions_dir, title="New chat"):
        sid = uuid.uuid4().hex[:12]
        path = os.path.join(sessions_dir, f"{sid}.jsonl")
        s = cls(sid, path, title=title)
        s._write_meta()
        return s

    @classmethod
    def load(cls, sessions_dir, sid):
        path = os.path.join(sessions_dir, f"{sid}.jsonl")
        if not os.path.exists(path):
            return None
        meta = {"title": "(untitled)", "created_at": _now()}
        cap = config.MAX_HISTORY_MESSAGES
        msgs = collections.deque(maxlen=cap)
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") == "meta":
                        meta.update(obj.get("data", {}))
                    else:
                        msgs.append(obj)
        except OSError:
            return None
        s = cls(sid, path, title=meta.get("title", "(untitled)"),
                created_at=meta.get("created_at", _now()))
        s.updated_at = meta.get("updated_at", s.created_at)
        s.messages = list(msgs)
        return s

    def _write_meta(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        meta = {"type": "meta", "data": {
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }}
        # Atomic-ish: write to temp then rename. Append-only after this.
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            f.write(json.dumps(meta) + "\n")
            for m in self.messages:
                f.write(json.dumps(m) + "\n")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def append(self, msg):
        self.messages.append(msg)
        self.updated_at = _now()
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(msg) + "\n")
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError:
            pass

    def set_title(self, title):
        self.title = title
        self._write_meta()

    def info(self):
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
        }

    def delete(self):
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


class SessionManager:
    def __init__(self, sessions_dir=None):
        self.dir = sessions_dir or os.path.join(config.HISTORY_DIR, "sessions")
        try:
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        self._cache = {}  # id -> Session
        self._load_existing()

    def _load_existing(self):
        try:
            for fname in os.listdir(self.dir):
                if fname.endswith(".jsonl") and not fname.endswith(".tmp"):
                    sid = fname[:-len(".jsonl")]
                    s = Session.load(self.dir, sid)
                    if s is not None:
                        self._cache[sid] = s
        except OSError:
            pass

    def list(self):
        return sorted(
            (s.info() for s in self._cache.values()),
            key=lambda x: x["updated_at"],
            reverse=True,
        )

    def get(self, sid):
        return self._cache.get(sid)

    def create(self, title="New chat"):
        s = Session.create(self.dir, title=title)
        self._cache[s.id] = s
        return s

    def delete(self, sid):
        s = self._cache.pop(sid, None)
        if s is not None:
            s.delete()
            return True
        return False

    def get_or_create_default(self):
        """Used by legacy clients that don't manage sessions explicitly."""
        for s in self._cache.values():
            if s.title == "(legacy)":
                return s
        return self.create(title="(legacy)")
