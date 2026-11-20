from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from designer_checkpoint import CheckpointError, save


class CheckpointTests(unittest.TestCase):
    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True).stdout

    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name)
        self.a=self.base/"a"
        self.a.mkdir()
        self.git(self.base, "init", "--bare", "-b", "main", "remote.git")
        self.git(self.a, "init", "-b", "main")
        self.git(self.a, "remote", "add", "origin", str(self.base/"remote.git"))
        (self.a/"data").mkdir()
        (self.a/"data/designer_state.json").write_text("{}")
        self.git(self.a, "add", ".")
        self.commit(self.a)
        self.git(self.a, "push", "-u", "origin", "main")
        self.git(self.base, "clone", "remote.git", "b")
        self.b=self.base/"b"

    def commit(self, root):
        self.git(root, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")

    def remote(self, key):
        return self.git(self.base/"remote.git","show",f"main:{key}").decode()

    def test_preserves_parallel_files_and_saves_each_checkpoint(self):
        (self.b/"unrelated").write_text("remote")
        self.git(self.b,"add",".")
        self.commit(self.b)
        self.git(self.b,"push")
        (self.a/"unrelated_private").write_text("local")
        sub=self.a/"data/designer_card_submissions/C-TEST"
        sub.mkdir(parents=True)
        (sub/"sample").write_text("asset")
        (self.a/"data/designer_card_tasks.json").write_text("prepared")
        save(self.a)
        (self.a/"data/designer_card_tasks.json").write_text("delivered")
        save(self.a)
        save(self.a)
        self.assertEqual(self.remote("data/designer_card_tasks.json"),"delivered")
        self.assertEqual(self.remote("data/designer_card_submissions/C-TEST/sample"),"asset")
        self.assertEqual(self.remote("unrelated"),"remote")
        self.assertNotIn(b"unrelated_private",self.git(self.base/"remote.git","ls-tree","-r","--name-only","main"))

    def test_changed_remote_state_blocks_even_clean_worker(self):
        (self.b/"data/designer_state.json").write_text("new")
        self.git(self.b,"add",".")
        self.commit(self.b)
        self.git(self.b,"push")
        with self.assertRaises(CheckpointError): save(self.a)
        self.assertEqual(self.remote("data/designer_state.json"),"new")
