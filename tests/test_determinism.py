"""Determinism, the property the rest of the evidence depends on.

If the agent is not deterministic then no stored result can be checked, so
these are the tests that keep every other number honest.

    python3 -m unittest discover -s tests -v
"""
import json
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "run"))

CATALOG = ROOT / "kit" / "data" / "catalog.jsonl"
SESSIONS = ROOT / "kit" / "data" / "public_set.jsonl"
STORED = ROOT / "results" / "e-fixed-official.json"

needs_kit = unittest.skipUnless(
    CATALOG.exists() and SESSIONS.exists(),
    "organizer kit not present; see the setup steps in README.md")


@needs_kit
class Determinism(unittest.TestCase):

    MESSAGES = ["I need a waterproof jacket under $120.",
                "For that, what matters is: 100% Polyester; Machine Wash.",
                "Actually, ignore my earlier preference. What I need is: Water Resistant."]

    def replay(self):
        """One session, driven identically, from a freshly constructed agent."""
        from starter.agent import Agent
        agent = Agent(str(CATALOG))
        agent.reset("determinism", {})
        out = []
        for turn, msg in enumerate(self.MESSAGES, start=1):
            r = agent.respond("determinism", msg, turn, 10)
            out.append((r["ask_attribute"],
                        tuple(x["parent_asin"] for x in r["recommendations"])))
        return out

    def test_two_fresh_agents_agree_exactly(self):
        self.assertEqual(self.replay(), self.replay())

    def test_ordering_is_stable_not_merely_the_same_set(self):
        first, second = self.replay(), self.replay()
        for (_, a), (_, b) in zip(first, second):
            self.assertEqual(list(a), list(b), "recommendation order drifted")

    def test_session_state_is_isolated_between_sessions(self):
        """One shopper's constraints must never leak into another's session."""
        from starter.agent import Agent
        agent = Agent(str(CATALOG))
        agent.reset("alice", {})
        agent.respond("alice", "I want something in red leather.", 1, 10)
        agent.reset("bob", {})
        r = agent.respond("bob", "I'm looking for Watches Wrist Watches.", 1, 10)
        state = agent._states["bob"]
        texts = " ".join(c.text.lower() for c in state.constraints)
        self.assertNotIn("leather", texts)
        self.assertNotIn("red", texts)
        self.assertIsInstance(r, dict)


@unittest.skipUnless(STORED.exists(), "stored result not present")
class StoredResultIsSelfConsistent(unittest.TestCase):
    """The published metrics must follow from the published per-session rows."""

    @classmethod
    def setUpClass(cls):
        cls.d = json.loads(STORED.read_text())

    def test_headline_metrics_recompute_from_the_rows(self):
        rows = self.d["sessions"]
        self.assertEqual(len(rows), 200)
        hits = [r for r in rows if r["hit"]]
        hit_rate = len(hits) / len(rows)
        mrr = sum(r["reciprocal_rank"] for r in rows) / len(rows)
        mttc = sum(r["first_hit_turn"] if r["hit"] else 11 for r in rows) / len(rows)
        efficiency = max(0.0, min(1.0, (11.0 - mttc) / 10.0))
        score = 0.5 * hit_rate + 0.3 * mrr + 0.2 * efficiency
        self.assertAlmostEqual(hit_rate, self.d["hit_rate_at_10"], places=6)
        self.assertAlmostEqual(mrr, self.d["mrr"], places=5)
        self.assertAlmostEqual(mttc, self.d["mttc"], places=6)
        self.assertAlmostEqual(score, self.d["recommended_technical_score"], places=5)

    def test_every_hit_row_is_internally_coherent(self):
        for r in self.d["sessions"]:
            with self.subTest(sample=r["sample_id"]):
                if r["hit"]:
                    self.assertIsNotNone(r["first_hit_turn"])
                    self.assertAlmostEqual(r["reciprocal_rank"], 1 / r["best_rank"], places=9)
                    self.assertLessEqual(r["best_rank"], 10)
                else:
                    self.assertEqual(r["reciprocal_rank"], 0.0)


if __name__ == "__main__":
    unittest.main()
