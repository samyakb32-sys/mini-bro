"""Temporary shelf storage. No UI, stdlib only."""
import json
import os
import shutil
import time
import uuid

from .config import write_json_atomic

MAX_COPY_BYTES = 200 * 1024 * 1024


def _size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for dirpath, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


class Store:
    def __init__(self, root, clock=time.time, max_copy_bytes=MAX_COPY_BYTES):
        self.root = root
        self.clock = clock
        self.max_copy_bytes = max_copy_bytes
        self.index_path = os.path.join(root, "index.json")
        os.makedirs(root, exist_ok=True)
        self.items = {}
        try:
            with open(self.index_path, encoding="utf-8") as f:
                self.items = {i["id"]: i for i in json.load(f)}
        except (OSError, ValueError, KeyError):
            pass
        self.purge_expired()

    def _save(self):
        write_json_atomic(self.index_path, list(self.items.values()))

    def add(self, path, ttl_seconds):
        """Hold a file or folder. Small items are copied, large ones referenced."""
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        size = _size(path)
        name = os.path.basename(path.rstrip("/\\")) or path
        item_id = uuid.uuid4().hex[:12]
        stored = size <= self.max_copy_bytes
        target = path
        if stored:
            folder = os.path.join(self.root, item_id)
            os.makedirs(folder)
            target = os.path.join(folder, name)
            if os.path.isdir(path):
                shutil.copytree(path, target)
            else:
                shutil.copy2(path, target)
        item = {
            "id": item_id,
            "name": name,
            "path": target,
            "stored": stored,
            "size": size,
            "expires": self.clock() + ttl_seconds,
        }
        self.items[item_id] = item
        self._save()
        return item

    def _delete_files(self, item):
        if item["stored"]:
            shutil.rmtree(os.path.join(self.root, item["id"]), ignore_errors=True)

    def remove(self, item_id):
        item = self.items.pop(item_id, None)
        if item:
            self._delete_files(item)
            self._save()

    def clear(self):
        for item in self.items.values():
            self._delete_files(item)
        self.items = {}
        self._save()

    def purge_expired(self):
        now = self.clock()
        expired = [i for i in self.items.values() if i["expires"] <= now]
        for item in expired:
            self._delete_files(item)
            del self.items[item["id"]]
        if expired:
            self._save()
        return len(expired)

    def list(self):
        return sorted(self.items.values(), key=lambda i: i["expires"])
