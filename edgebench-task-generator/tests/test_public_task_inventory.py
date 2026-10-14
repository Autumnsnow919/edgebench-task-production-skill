import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import topify_public_tasks


class PublicTaskInventoryTests(unittest.TestCase):
    def test_inventory_is_parent_analysis_only_and_hashes_definition(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "tasks.jsonl"
            source.write_text(json.dumps({
                "task_id": "public_fixture_task", "name": "Fixture", "category": "Systems",
                "description": "analysis only", "language": "Python", "metric": "score", "internet": False,
            }) + "\n")
            output = root / "inventory.json"
            value, code = topify_public_tasks.inventory(source, output, "fixture-revision")
            self.assertEqual(code, 0)
            self.assertEqual(value["status"], "parent-analysis-only; no Seed/task/rollout authorization")
            self.assertEqual(value["task_count"], 1)
            self.assertRegex(value["tasks"][0]["parent_definition_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
