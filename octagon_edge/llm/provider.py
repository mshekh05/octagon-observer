"""Pluggable LLM analyst: Ollama (local) or Claude. Returns structured JSON.

The LLM never sets the probability. It writes the breakdown and may suggest a
small adjustment for news (injury, weight miss, short notice), which the
engine clamps to +/-3 points.
"""
from __future__ import annotations

import json
import logging

import httpx

from ..config import Settings

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_factors": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "news_adjustment_a": {"type": "number"},
        "adjustment_reason": {"type": "string"},
    },
    "required": ["summary", "key_factors", "risks", "news_adjustment_a", "adjustment_reason"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are an MMA analyst helping a bettor review a fight. You get a statistical model's "
    "win probability, the betting market's de-vigged probability, both fighters' UFC stats, "
    "and recent news headlines. Write a short, concrete breakdown: stylistic matchup, what "
    "the numbers say, and where the stats may mislead (small samples, old fights, level of "
    "competition). Use only the data provided plus well-established public facts about the "
    "fighters; say so when you are unsure. news_adjustment_a is a probability adjustment for "
    "fighter A between -0.03 and 0.03, non-zero only when the provided news gives a concrete "
    "reason (injury, missed weight, short-notice replacement, illness); otherwise 0. "
    "Keep summary under 90 words and each list item under 20 words."
)


class Analyst:
    def __init__(self, settings: Settings):
        self.s = settings

    @property
    def enabled(self) -> bool:
        return self.s.llm_provider in ("ollama", "claude")

    def analyze(self, context: dict) -> dict | None:
        if not self.enabled:
            return None
        prompt = "Fight data (JSON):\n" + json.dumps(context, default=str, indent=1)
        try:
            if self.s.llm_provider == "ollama":
                return self._ollama(prompt)
            return self._claude(prompt)
        except Exception as exc:  # LLM is advisory; never break the pipeline
            log.warning("LLM analysis failed (%s): %s", self.s.llm_provider, exc)
            return None

    def _ollama(self, prompt: str) -> dict:
        resp = httpx.post(f"{self.s.ollama_url}/api/chat", timeout=180, json={
            "model": self.s.ollama_model, "stream": False, "format": SCHEMA,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        })
        resp.raise_for_status()
        return json.loads(resp.json()["message"]["content"])

    def _claude(self, prompt: str) -> dict:
        import anthropic

        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=self.s.claude_model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude declined: {response.stop_details}")
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)
