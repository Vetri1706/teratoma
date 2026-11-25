import json
from pathlib import Path
import tempfile
import unittest

from src.demo import END_TICK, ROOT, SCENARIOS, generate, record_scenario


class DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recordings = {name: record_scenario(name) for name in SCENARIOS}

    def test_every_scenario_checks_real_state_and_converges(self):
        for name, recording in self.recordings.items():
            with self.subTest(scenario=name):
                final = recording["frames"][-1]
                self.assertEqual(final["tick"], END_TICK)
                self.assertEqual(final["checks"], END_TICK + 1)
                self.assertEqual(final["pending"], 0)
                self.assertGreater(recording["result"]["progress_during_fault"], 0)
                self.assertGreater(recording["result"]["new_requests_completed_during_fault"], 0)
                self.assertEqual(len({node["digest"] for node in final["nodes"]}), 1)
                self.assertEqual(len({node["commit"] for node in final["nodes"]}), 1)
                self.assertTrue(all(node["alive"] and node["status"] == "normal" for node in final["nodes"]))

    def test_primary_is_really_offline_while_survivors_make_progress(self):
        frames = self.recordings["primary-crash"]["frames"]
        fault = next(frame for frame in frames if frame["tick"] == 300)
        later = next(frame for frame in frames if frame["tick"] == 980)
        self.assertFalse(fault["nodes"][0]["alive"])
        self.assertFalse(later["nodes"][0]["alive"])
        self.assertGreater(later["completed"], fault["completed"])
        self.assertTrue(any(node["alive"] and node["primary"] and node["view"] > 0 for node in later["nodes"]))

    def test_partition_blocks_both_directions_and_majority_advances(self):
        frames = self.recordings["partition"]["frames"]
        split = next(frame for frame in frames if frame["tick"] == 980)
        self.assertEqual(len(split["blocked"]), 12)
        self.assertTrue(all([b, a] in [list(link) for link in split["blocked"]] for a, b in split["blocked"]))
        self.assertGreater(max(node["commit"] for node in split["nodes"][2:]),
                           max(node["commit"] for node in split["nodes"][:2]))
        self.assertEqual(frames[-1]["blocked"], [])

    def test_corrupt_replica_takes_real_recovery_path(self):
        frames = self.recordings["corruption"]["frames"]
        self.assertTrue(any(not frame["nodes"][4]["alive"] for frame in frames))
        self.assertGreater(frames[-1]["nodes"][4]["recoveries"], 0)
        self.assertEqual(self.recordings["healthy"]["frames"][-1]["nodes"][4]["recoveries"], 0)

    def test_same_seed_produces_identical_observations(self):
        self.assertEqual(record_scenario("primary-crash"), self.recordings["primary-crash"])

    def test_checked_in_demo_matches_the_engine(self):
        checked_in = json.loads((ROOT / "docs/lab/traces.json").read_text(encoding="utf-8"))
        # Compare the serialized representation because JSON represents tuples
        # (the simulator's blocked links) as arrays.
        self.assertEqual(checked_in["scenarios"], json.loads(json.dumps(list(self.recordings.values()))))
        javascript = (ROOT / "docs/lab/traces.js").read_text(encoding="utf-8")
        self.assertEqual(json.loads(javascript.removeprefix("window.AEGIS_DEMOS = ").removesuffix(";\n")), checked_in)

    def test_generated_report_is_portable_and_uses_requested_seed(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "nested report"
            result = generate(output, seed=19, scenario="healthy")
            self.assertEqual(result["scenarios"][0]["seed"], 19)
            for asset in ("index.html", "app.js", "styles.css", "traces.json", "traces.js"):
                self.assertTrue((output / asset).is_file(), asset)
            data = json.loads((output / "traces.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["scenarios"]), 1)
            self.assertEqual(data["scenarios"][0]["seed"], 19)

    def test_unknown_scenario_fails_without_a_recording(self):
        with self.assertRaisesRegex(ValueError, "unknown scenario"):
            record_scenario("made-up")


if __name__ == "__main__":
    unittest.main()
