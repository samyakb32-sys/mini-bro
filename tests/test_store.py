import os
import tempfile
import unittest

from minibro.store import Store


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "shelf")
        self.src = os.path.join(self.tmp.name, "hello.txt")
        with open(self.src, "w") as f:
            f.write("hi")
        self.clock = FakeClock()

    def make(self, **kw):
        return Store(self.root, clock=self.clock, **kw)

    def test_add_copies_file(self):
        s = self.make()
        item = s.add(self.src, 60)
        self.assertTrue(item["stored"])
        self.assertNotEqual(item["path"], self.src)
        os.remove(self.src)
        with open(item["path"]) as f:
            self.assertEqual(f.read(), "hi")

    def test_add_folder(self):
        d = os.path.join(self.tmp.name, "dir")
        os.makedirs(os.path.join(d, "sub"))
        with open(os.path.join(d, "sub", "a.txt"), "w") as f:
            f.write("a")
        item = self.make().add(d, 60)
        self.assertTrue(os.path.isfile(os.path.join(item["path"], "sub", "a.txt")))

    def test_large_item_is_referenced(self):
        s = self.make(max_copy_bytes=1)
        item = s.add(self.src, 60)
        self.assertFalse(item["stored"])
        self.assertEqual(item["path"], self.src)

    def test_purge_removes_expired_only(self):
        s = self.make()
        short = s.add(self.src, 10)
        long = s.add(self.src, 100)
        self.clock.now += 50
        self.assertEqual(s.purge_expired(), 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, short["id"])))
        self.assertIn(long["id"], s.items)

    def test_purge_keeps_referenced_source(self):
        s = self.make(max_copy_bytes=1)
        s.add(self.src, 10)
        self.clock.now += 50
        s.purge_expired()
        self.assertTrue(os.path.exists(self.src))

    def test_index_round_trip_and_startup_purge(self):
        s = self.make()
        s.add(self.src, 10)
        keep = s.add(self.src, 1000)
        self.clock.now += 50
        s2 = self.make()
        self.assertEqual([i["id"] for i in s2.list()], [keep["id"]])

    def test_remove_and_clear(self):
        s = self.make()
        a = s.add(self.src, 60)
        s.add(self.src, 60)
        s.remove(a["id"])
        self.assertEqual(len(s.list()), 1)
        s.clear()
        self.assertEqual(s.list(), [])
        self.assertEqual([n for n in os.listdir(self.root) if n != "index.json"], [])

    def test_missing_source(self):
        with self.assertRaises(FileNotFoundError):
            self.make().add(os.path.join(self.tmp.name, "nope"), 60)


if __name__ == "__main__":
    unittest.main()
