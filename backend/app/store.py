"""Document store: Firestore in production, in-memory for local tests (STORE=memory)."""
import copy, threading
from . import config


class MemoryStore:
    def __init__(self):
        self.d, self.lock = {}, threading.Lock()

    def get(self, col, id_):
        with self.lock:
            v = self.d.get((col, id_))
            return copy.deepcopy(v) if v is not None else None

    def set(self, col, id_, data):
        with self.lock:
            self.d[(col, id_)] = copy.deepcopy(data)

    def update(self, col, id_, fields):
        with self.lock:
            self.d.setdefault((col, id_), {}).update(copy.deepcopy(fields))

    def incr(self, col, id_, field, n=1):
        with self.lock:
            doc = self.d.setdefault((col, id_), {})
            doc[field] = doc.get(field, 0) + n
            return doc[field]


class FirestoreStore:
    def __init__(self):
        from google.cloud import firestore
        self._fs = firestore
        self.db = firestore.Client()

    def _ref(self, col, id_):
        return self.db.collection(col).document(id_)

    def get(self, col, id_):
        s = self._ref(col, id_).get()
        return s.to_dict() if s.exists else None

    def set(self, col, id_, data):
        self._ref(col, id_).set(data)

    def update(self, col, id_, fields):
        self._ref(col, id_).set(fields, merge=True)

    def incr(self, col, id_, field, n=1):
        ref = self._ref(col, id_)
        ref.set({field: self._fs.Increment(n)}, merge=True)
        return (ref.get().to_dict() or {}).get(field, n)


_store = None


def store():
    global _store
    if _store is None:
        _store = MemoryStore() if config.STORE == "memory" else FirestoreStore()
    return _store
