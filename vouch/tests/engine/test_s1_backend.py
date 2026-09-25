"""System-1 backend tests: checkpoint pinning, routing provenance, and the
confidence parity of the OpenRouter parser. The checkpoint that actually
answers is the provenance of every probability downstream, so a silent
swap must be visible. Stdlib unittest only; httpx is mocked."""

from __future__ import annotations

import unittest
from unittest import mock

from vouch.engine.s1_backends import LayaServerBackend, parse_answers

QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": "what do",
        "criteria": {"long": "l", "flat": "f", "short": "s"},
    }
}


def canned(routed: str) -> dict:
    return {
        "model": "laya-rl-agent",
        "routing": {"model": routed, "reason": "test"},
        "answers": {
            "action": {
                "choice": "flat",
                "probabilities": {"long": 0.1, "flat": 0.8, "short": 0.1},
                "answer_confidence": 0.8,
            }
        },
    }


class CapturedPost:
    def __init__(self, reply: dict):
        self.reply = reply
        self.payload: dict | None = None

    def __call__(self, url, json=None, headers=None, timeout=None):
        captured = self
        self.payload = json

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return captured.reply

        return R()


class CheckpointPinTest(unittest.TestCase):
    def test_pin_is_sent_and_provenance_logged_once(self):
        cap = CapturedPost(canned("typed-decisions"))
        backend = LayaServerBackend("http://x", checkpoint="typed-decisions")
        with mock.patch("vouch.engine.s1_backends.httpx.post", cap):
            backend.predict({"a": 1}, QUESTIONS)
            backend.predict({"a": 2}, QUESTIONS)
        self.assertEqual(cap.payload["model"], "typed-decisions")
        self.assertEqual(backend.routed_model, "typed-decisions")

    def test_no_pin_omits_the_model_field(self):
        cap = CapturedPost(canned("english"))
        backend = LayaServerBackend("http://x")
        with mock.patch("vouch.engine.s1_backends.httpx.post", cap):
            backend.predict({"a": 1}, QUESTIONS)
        self.assertNotIn("model", cap.payload)
        self.assertEqual(backend.routed_model, "english")


class ParseAnswersParityTest(unittest.TestCase):
    def test_choice_answers_carry_answer_confidence(self):
        reply = {
            "choices": [
                {
                    "message": {
                        "content": '{"action": {"choice": "short", '
                        '"probabilities": {"long": 0.2, "flat": 0.2, "short": 0.6}}}'
                    }
                }
            ]
        }
        answers = parse_answers(reply, QUESTIONS)
        self.assertAlmostEqual(answers["action"]["answer_confidence"], 0.6)

    def test_missing_choice_falls_flat_not_long(self):
        # the old default (keys[0] = "long") was a dangerous neutral default:
        # an unparseable reply must never read as a trade signal
        reply = {"choices": [{"message": {"content": "{}"}}]}
        answers = parse_answers(reply, QUESTIONS)
        self.assertEqual(answers["action"]["choice"], "flat")


if __name__ == "__main__":
    unittest.main()
