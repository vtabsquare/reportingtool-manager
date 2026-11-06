from __future__ import annotations

import unittest
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock
from manager.github_service import discover_tags, discover_commits
from manager.database import Database
from manager import main

class TestGitTagDiscovery(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).resolve().parent / "_runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        self.database_path = runtime / f"manager-tags-{uuid.uuid4().hex}.db"
        self.orig_db = main.db
        main.db = Database(self.database_path)
        main.db.initialize()

    def tearDown(self):
        main.db = self.orig_db
        if self.database_path.exists():
            try:
                self.database_path.unlink()
            except Exception:
                pass

    @patch("subprocess.run")
    def test_discover_tags_from_ls_remote(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="""5a1f5bfb5179f9e465dfa97c560a09f6b88f0953\trefs/tags/v6.1\n37edd1e2acbc797e8933ba2fe005cb31df7eac1e\trefs/tags/v6.1^{}\n"""
        )
        tags = discover_tags("https://github.com/vtabsquare/Reportingtool-webservice.git")
        self.assertEqual(tags.get("37edd1e2acbc797e8933ba2fe005cb31df7eac1e"), "v6.1")
        self.assertEqual(tags.get("5a1f5bfb5179f9e465dfa97c560a09f6b88f0953"), "v6.1")

    @patch("subprocess.run")
    @patch("manager.github_service.github_request")
    def test_discover_tags_rest_fallback(self, mock_gh_request, mock_run):
        mock_run.side_effect = Exception("git not found")
        mock_gh_request.return_value = [
            {"name": "v6.2", "commit": {"sha": "aabbccddeeff0011223344556677889900aabbcc"}}
        ]
        tags = discover_tags("https://github.com/vtabsquare/Reportingtool-webservice.git")
        self.assertEqual(tags.get("aabbccddeeff0011223344556677889900aabbcc"), "v6.2")

    def test_register_release_candidate_upgrades_tag_version(self):
        db = main.db
        sha = "1122334455667788990011223344556677889900"
        repo = "https://github.com/vtabsquare/Reportingtool-webservice.git"
        
        # 1. Register untagged commit
        candidate1 = {
            "repository_url": repo,
            "git_commit_sha": sha,
            "version": f"commit-{sha[:12]}",
            "message": "feat: test tag upgrade",
            "author": "tester",
            "artifact_url": f"{repo}/commit/{sha}"
        }
        rel1 = main.register_release_candidate("services", candidate1)
        self.assertEqual(rel1["version"], f"commit-{sha[:12]}")
        
        # 2. Re-register after tag is pushed
        candidate2 = dict(candidate1)
        candidate2["version"] = "v6.3"
        rel2 = main.register_release_candidate("services", candidate2)
        self.assertEqual(rel2["version"], "v6.3")
        
        # 3. Verify in database
        row = db.one("SELECT version FROM releases WHERE id=?", (rel1["id"],))
        self.assertEqual(row["version"], "v6.3")

if __name__ == "__main__":
    unittest.main()
