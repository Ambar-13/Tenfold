"""The agent must actually work, not merely stay inside the contract.

An agent that returns nothing on every turn satisfies the response contract
perfectly and would pass a purely structural test suite. These tests fail on
such an agent, so they are what stop the suite from certifying a dead system.

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
class TheAgentActuallyFindsProducts(unittest.TestCase):
    """Drive real sessions and require real hits, not merely legal responses."""

    N = 25                       # sessions; keep the suite under ~30 seconds

    @classmethod
    def setUpClass(cls):
        from starter.agent import Agent
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "kit_eval", ROOT / "kit" / "evaluator" / "local_evaluator.py")
        cls.ev = importlib.util.module_from_spec(spec)
        sys.modules["kit_eval"] = cls.ev
        spec.loader.exec_module(cls.ev)
        cls.ids, _cats, cls.products = cls.ev.catalog_index(str(CATALOG))
        cls.agent = Agent(str(CATALOG))
        with open(SESSIONS) as fh:
            cls.samples = [json.loads(l) for l in fh][:cls.N]

    def play(self, sample):
        """Run one session the way the official harness does. Returns (hit, rank, turn)."""
        ev, agent = self.ev, self.agent
        card, behavior = ev.materialize_hidden_fields(sample, self.products)
        s = dict(sample); s["intent_card"] = card; s["behavior"] = behavior
        target = s["ground_truth"]["parent_asin"]
        category = ev.coarse_category(self.products[target].get("categories") or [])
        sid = "eff-" + s["sample_id"]
        agent.reset(sid, s.get("user_profile", {}))
        disclosed, boundary_used = set(), False
        applied = s["scenario_type"] != "intent_override"
        msg = ev.initial_message(s, category, disclosed)
        for turn in range(1, 11):
            r = agent.respond(sid, msg, turn, 10)
            ranked = ev.normalize_recommendations(r.get("recommendations"), self.ids)
            if applied and target in ranked:
                return True, ranked.index(target) + 1, turn
            ov = behavior.get("override")
            if ov and not applied and turn + 1 == ov["turn"]:
                applied = True
                disclosed.add(ov["new_value"])
                msg = ov["message"]
            else:
                msg, boundary_used = ev.customer_reply(
                    s, r.get("ask_attribute"), disclosed, boundary_used)
        return False, None, None

    def test_it_recommends_at_all(self):
        """A silent agent is a broken agent."""
        emitted = 0
        for s in self.samples[:5]:
            sid = "emit-" + s["sample_id"]
            self.agent.reset(sid, {})
            for turn in range(1, 6):
                r = self.agent.respond(sid, "I need a black cotton shirt.", turn, 10)
                emitted += len(r["recommendations"])
        self.assertGreater(emitted, 0, "the agent never recommended anything")

    def test_it_finds_the_target_in_most_sessions(self):
        """The shipped agent hits 97.5% of the public set; require a wide margin."""
        results = [self.play(s) for s in self.samples]
        hits = sum(1 for hit, _, _ in results if hit)
        rate = hits / len(results)
        self.assertGreaterEqual(
            rate, 0.80, f"hit rate collapsed to {rate:.1%} on {len(results)} sessions")

    def test_it_converges_quickly(self):
        """Mean turns to conversion is 2.94 on the full set; a stall is a regression."""
        turns = [t for hit, _, t in (self.play(s) for s in self.samples) if hit]
        self.assertTrue(turns, "no session converged at all")
        self.assertLess(sum(turns) / len(turns), 5.0)

    def test_it_recovers_from_an_intent_reversal(self):
        """Selective invalidation is the headline claim; hold it to account."""
        overrides = [s for s in self.samples if s["scenario_type"] == "intent_override"]
        if not overrides:
            self.skipTest("no override session in this slice")
        hits = sum(1 for s in overrides if self.play(s)[0])
        self.assertGreaterEqual(hits / len(overrides), 0.6)


@unittest.skipUnless(STORED.exists(), "stored result not present")
class TheStoredResultIsNotDegenerate(unittest.TestCase):
    """Guard the published numbers against a silently emptied result file."""

    @classmethod
    def setUpClass(cls):
        cls.d = json.loads(STORED.read_text())

    def test_headline_numbers_are_what_we_publish(self):
        self.assertEqual(self.d["sample_count"], 200)
        self.assertAlmostEqual(self.d["recommended_technical_score"], 0.902435, places=6)
        self.assertAlmostEqual(self.d["hit_rate_at_10"], 0.975, places=6)
        self.assertAlmostEqual(self.d["mrr"], 0.845782, places=6)
        self.assertAlmostEqual(self.d["mttc"], 2.94, places=6)

    def test_most_hits_are_rank_one(self):
        hits = [r for r in self.d["sessions"] if r["hit"]]
        self.assertGreaterEqual(len(hits), 190)
        self.assertGreaterEqual(sum(1 for r in hits if r["best_rank"] == 1) / len(hits), 0.7)


if __name__ == "__main__":
    unittest.main()
