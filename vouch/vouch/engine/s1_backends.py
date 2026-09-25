"""System-1 backends: where the typed decisions come from.

Three interchangeable providers, chosen from the dashboard (or env):

- "local":      laya Router in-process (the original mode; needs laya installed
                and loads the model weights, slow cold start).
- "server":     a laya HTTP server over the /v1/systemone protocol
                (what `laya.serve` speaks): POST {state, questions} ->
                {model, answers, usage, routing}. Set VOUCH_S1_URL, e.g.
                http://127.0.0.1:9989. Optional bearer key (VOUCH_S1_API_KEY)
                for servers started with LAYA_API_KEY. A cold server can
                take a while on its first predict; that is normal.
- "openrouter": any OpenAI-compatible chat endpoint via OpenRouter with an
                API key (VOUCH_S1_API_KEY, VOUCH_S1_MODEL). The typed questions
                are serialized into one prompt and the JSON reply is parsed
                back into the same answers shape, so everything downstream
                (rails, analysis, SFT logs) is provider-agnostic.

All backends return the Router.predict shape:
    {"model": str, "answers": {q: {"choice"/"score"/"noul"/"probabilities"}}, ...}

Layering: engine. May use venues and attest; must not import server.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from vouch.config import load_settings
from vouch.exceptions import BackendError
from vouch.protocols import System1Backend

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class LocalLayaBackend:
    """laya Router in-process. Import is lazy so the other providers work
    on machines without laya/torch installed."""

    name = "local"

    def __init__(self) -> None:
        # Quiet the huggingface download bars and laya's calibration
        # RuntimeWarning (its checkpoint clamps temperatures itself; the
        # warning repeats on every cold start and reads like an error).
        import os
        import warnings

        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        warnings.filterwarnings(
            "ignore", message="laya: this checkpoint ships invalid temperatures"
        )
        try:
            from laya import Router
        except ImportError as e:
            raise BackendError(
                "provider 'local' needs the laya package installed; "
                "choose the 'server' or 'openrouter' provider instead"
            ) from e
        self.router = Router(preload=True)

    def predict(self, state: dict, questions: dict) -> dict:
        result: dict = self.router.predict(state, questions)
        return result


class LayaServerBackend:
    """Client for a laya HTTP server (laya.serve): POST /v1/systemone.

    `checkpoint` pins the served checkpoint (english | multilingual |
    typed-decisions). Auto-routing routes by text language, which silently
    serves the base `english` checkpoint to vouch - near-chance zero-shot on
    typed decisions. Provenance: the first reply's routing.model is checked
    against the pin and logged once; a mismatch after that is a per-call
    warning, because every probability downstream was produced by whatever
    checkpoint actually answered.
    """

    name = "server"

    def __init__(
        self,
        url: str,
        api_key: str = "",
        timeout: float = 120.0,
        checkpoint: str = "",
    ) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.checkpoint = checkpoint
        self.routed_model: str | None = None
        self._provenance_logged = False

    def health(self) -> dict | None:
        try:
            r = httpx.get(f"{self.url}/health", timeout=5)
            r.raise_for_status()
            health: dict = r.json()
            return health
        except Exception:  # noqa: BLE001 - probing is best-effort
            return None

    def predict(self, state: dict, questions: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload: dict = {"state": state, "questions": questions}
        if self.checkpoint:
            payload["model"] = self.checkpoint
        try:
            r = httpx.post(
                f"{self.url}/v1/systemone",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            raise BackendError(f"laya server at {self.url} unreachable: {e}") from e
        if r.status_code == 401:
            raise BackendError("laya server rejected the API key (401)")
        if r.status_code == 422:
            raise BackendError(f"laya server rejected the question set: {r.text[:200]}")
        if r.status_code >= 400:
            raise BackendError(f"laya server error {r.status_code}: {r.text[:200]}")
        res: dict = r.json()
        if "answers" not in res:
            raise BackendError(f"laya server reply missing 'answers': {str(res)[:200]}")
        self._check_provenance(res)
        return res

    def _check_provenance(self, res: dict) -> None:
        from vouch.logging import get_logger

        routed = (res.get("routing") or {}).get("model")
        if routed:
            self.routed_model = routed
        if not self._provenance_logged:
            self._provenance_logged = True
            expected = self.checkpoint or "auto-route"
            get_logger(__name__).info(
                "laya checkpoint answering System-1: %s (requested: %s)",
                routed or "unknown",
                expected,
            )
        if self.checkpoint and routed and routed != self.checkpoint:
            get_logger(__name__).warning(
                "laya served checkpoint %s but %s was pinned; "
                "these probabilities are from a different model",
                routed,
                self.checkpoint,
            )


# --- OpenRouter (OpenAI-compatible chat) ------------------------------------

_ANSWER_SCHEMA: dict[str, str] = {
    "choice": "one of the criteria keys",
    "probabilities": "object mapping every criteria key to a 0..1 probability, all summing to 1",
    "score": "integer 0..4 on the criteria legend",
    "noul": "number 0..1",
}


def _question_prompt(qname: str, q: dict) -> str:
    criteria = q.get("criteria")
    crit = ""
    if isinstance(criteria, dict):
        crit = " Valid choices: " + ", ".join(criteria.keys()) + "."
    elif isinstance(criteria, list):
        crit = (
            " Score legend (0-indexed): "
            + "; ".join(f"{i}={c}" for i, c in enumerate(criteria))
            + "."
        )
    return f"- {qname}: {q.get('instructions', '')}{crit}"


def _answers_schema_prompt(questions: dict) -> str:
    lines = []
    for qname, q in questions.items():
        keys = (
            ["choice", "probabilities"]
            if q.get("type") == "choice"
            else ["score"]
            if q.get("type") == "score"
            else ["noul"]
        )
        detail = "; ".join(f'"{k}": {_ANSWER_SCHEMA[k]}' for k in keys)
        lines.append(f'  "{qname}": {{{detail}}}')
    return "\n".join(lines)


def _extract_json(text: str) -> str:
    if "<think>" in text:
        text = text.split("</think>")[-1]
    if "```" in text:  # tolerate fenced JSON
        parts = text.split("```")
        for part in parts[1::2]:
            if "{" in part:
                text = part
                break
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if (start != -1 and end > start) else text


def _clamp01(x: Any) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def parse_answers(reply: dict, questions: dict) -> dict:
    """Turn a chat model's JSON answer into Router-shaped `answers`.
    Tolerates missing fields (neutral defaults), out-of-range values, and
    probabilities that do not sum to 1 (renormalized)."""
    content = (reply.get("choices") or [{}])[0].get("message", {}).get("content", "")
    try:
        data = json.loads(_extract_json(content))
    except (ValueError, TypeError):
        raise BackendError(f"provider returned unparseable answer: {content[:200]}") from None

    answers: dict = {}
    for qname, q in questions.items():
        given = data.get(qname) if isinstance(data.get(qname), dict) else {}
        qtype = q.get("type")
        criteria = q.get("criteria")
        if qtype == "choice":
            keys = (
                list(criteria.keys()) if isinstance(criteria, dict) else ["long", "flat", "short"]
            )
            # Neutral default is "flat" when the question has one: an
            # unparseable or missing answer must never read as a trade signal.
            default = "flat" if "flat" in keys else keys[0]
            choice = given.get("choice", default)
            if choice not in keys:
                choice = default
            probs = {k: _clamp01(given.get("probabilities", {}).get(k)) for k in keys}
            total = sum(probs.values())
            if total <= 0:
                probs = {k: 1.0 / len(keys) for k in keys}
            else:
                probs = {k: v / total for k, v in probs.items()}
            # answer_confidence parity with the laya backends: max option p is
            # the calibrated confidence downstream analysis expects.
            answers[qname] = {
                "choice": choice,
                "probabilities": probs,
                "answer_confidence": max(probs.values()),
            }
        elif qtype == "score":
            n = len(criteria) if isinstance(criteria, list) else 5
            try:
                score = max(0, min(n - 1, int(round(float(given.get("score", 0))))))
            except (TypeError, ValueError):
                score = 0
            answers[qname] = {"score": score}
        else:  # noul
            answers[qname] = {"noul": _clamp01(given.get("noul", 0.5))}
    return answers


class OpenRouterBackend:
    """Any OpenAI-compatible chat endpoint (default: OpenRouter) asked to
    fill in the typed answers as one JSON object."""

    name = "openrouter"

    def __init__(
        self, api_key: str, model: str, base_url: str = OPENROUTER_BASE, timeout: float = 60.0
    ) -> None:
        if not api_key:
            raise BackendError("openrouter provider needs an API key (VOUCH_S1_API_KEY)")
        if not model:
            raise BackendError("openrouter provider needs a model id (VOUCH_S1_MODEL)")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def predict(self, state: dict, questions: dict) -> dict:
        state_text = "\n".join(f"{k}: {v}" for k, v in state.items())
        system = (
            "You are System-1, the fast decision head of a trading bot. "
            "You answer typed questions about market state with calibrated judgment. "
            "Reply with ONLY one JSON object, no prose, in exactly this shape:\n"
            "{\n" + _answers_schema_prompt(questions) + "\n}"
        )
        user = f"STATE\n{state_text}\n\nANSWER THESE QUESTIONS\n" + "\n".join(
            _question_prompt(name, qq) for name, qq in questions.items()
        )
        try:
            r = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.2,
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            raise BackendError(f"provider endpoint unreachable: {e}") from e
        if r.status_code == 401:
            raise BackendError("provider rejected the API key (401)")
        if r.status_code >= 400:
            raise BackendError(f"provider error {r.status_code}: {r.text[:200]}")
        reply: dict = r.json()
        return {"model": self.model, "answers": parse_answers(reply, questions)}


# --- selection ---------------------------------------------------------------


def make_s1_backend() -> System1Backend:
    """Build the System-1 backend from env (set by the dashboard or by hand):
    VOUCH_S1_PROVIDER=local|server|openrouter (default: local)
    VOUCH_S1_URL (server), VOUCH_S1_API_KEY (server bearer / openrouter key),
    VOUCH_S1_MODEL (openrouter), VOUCH_S1_BASE_URL (openrouter-compatible override).
    """
    s1 = load_settings().s1
    provider = s1.provider.lower()
    if provider == "local":
        return LocalLayaBackend()
    if provider == "server":
        if not s1.url:
            raise BackendError("provider 'server' needs VOUCH_S1_URL, e.g. http://127.0.0.1:9989")
        return LayaServerBackend(s1.url, api_key=s1.api_key, checkpoint=s1.checkpoint)
    if provider == "openrouter":
        return OpenRouterBackend(
            api_key=s1.api_key,
            model=s1.model,
            base_url=s1.base_url,
        )
    raise BackendError(f"unknown VOUCH_S1_PROVIDER: {provider}")
