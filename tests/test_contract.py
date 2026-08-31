"""The response contract, checked independently of the evaluator.

Every rule here comes from docs/agent_api_contract.json in the organizer kit:
the response shape, the allowed clarification attributes, and the requirement
that recommendations be unique, catalog-valid and at most ten.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "run"))          # resolves `starter` -> agent/

ASK_ENUM = {"category", "material", "color", "size", "style", "brand",
            "budget", "feature", "use_case", "other"}
CATALOG = ROOT / "kit" / "data" / "catalog.jsonl"
SESSIONS = ROOT / "kit" / "data" / "public_set.jsonl"

needs_kit = unittest.skipUnless(
    CATALOG.exists() and SESSIONS.exists(),
    "organizer kit not present; see the setup steps in README.md")


@needs_kit
class ResponseContract(unittest.TestCase):
    """Drive the agent over real sessions and check every response it returns."""

    N_SESSIONS = 12
    N_TURNS = 10

    @classmethod
    def setUpClass(cls):
        from starter.agent import Agent
        cls.agent = Agent(str(CATALOG))
        cls.catalog_ids = {json.loads(l)["parent_asin"] for l in open(CATALOG)}
        with open(SESSIONS) as fh:
            cls.samples = [json.loads(l) for l in fh][:cls.N_SESSIONS]

    def responses(self):
        for s in self.samples:
            sid = "test-" + s["sample_id"]
            self.agent.reset(sid, s.get("user_profile", {}))
            for turn in range(1, self.N_TURNS + 1):
                yield sid, turn, self.agent.respond(
                    sid, "I want something in black; budget around $40.", turn, 10)

    def test_shape_is_exactly_the_contract(self):
        for sid, turn, r in self.responses():
            with self.subTest(session=sid, turn=turn):
                self.assertIsInstance(r, dict)
                self.assertLessEqual(set(r), {"message", "ask_attribute",
                                              "recommendations", "usage"})
                self.assertIsInstance(r["message"], str)
                self.assertIsInstance(r["recommendations"], list)

    def test_ask_attribute_is_allowed_or_null(self):
        for sid, turn, r in self.responses():
            with self.subTest(session=sid, turn=turn):
                ask = r.get("ask_attribute")
                self.assertTrue(ask is None or ask in ASK_ENUM, f"bad ask {ask!r}")

    def test_recommendations_are_ten_unique_catalog_products(self):
        for sid, turn, r in self.responses():
            recs = r["recommendations"]
            if not recs:
                continue                      # holding is legal; empty list is fine
            with self.subTest(session=sid, turn=turn):
                asins = [x["parent_asin"] for x in recs]
                self.assertEqual(len(asins), 10)
                self.assertEqual(len(set(asins)), 10, "duplicate recommendation")
                unknown = [a for a in asins if a not in self.catalog_ids]
                self.assertFalse(unknown, f"not in catalog: {unknown[:3]}")
                for item in recs:
                    self.assertEqual(set(item), {"parent_asin"})

    def test_token_usage_is_non_negative(self):
        for sid, turn, r in self.responses():
            usage = r.get("usage")
            if usage is None:
                continue
            with self.subTest(session=sid, turn=turn):
                for key in ("prompt_tokens", "completion_tokens"):
                    self.assertIsInstance(usage[key], int)
                    self.assertGreaterEqual(usage[key], 0)

    def test_no_exception_escapes_respond(self):
        """Hostile input must degrade to a safe response, never raise."""
        from starter.agent import Agent
        agent = Agent(str(CATALOG))
        agent.reset("hostile", {})
        for turn, message in enumerate([
            "", "   ", "\x00\x01", "a" * 5000, "SELECT * FROM products; --",
            "🙂🙂🙂", '{"not": "a sentence"}', "colour: ", "under $", "NULL",
        ], start=1):
            with self.subTest(message=message[:20]):
                r = agent.respond("hostile", message, turn, 10)
                self.assertIsInstance(r, dict)
                self.assertIsInstance(r["message"], str)


if __name__ == "__main__":
    unittest.main()
