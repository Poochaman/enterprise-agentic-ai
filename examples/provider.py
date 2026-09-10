"""Explicit, bounded OpenAI Responses calls. Secrets stay on the local server."""
import json
import os
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODELS = ("gpt-4.1-mini", "gpt-4.1-nano")


class ProviderError(Exception):
    pass


def load_key(root):
    value = os.environ.get("OPENAI_API_KEY", "").strip()
    if not value:
        path = Path(root) / ".env.local"
        if path.is_file():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                name, sep, candidate = line.partition("=")
                if sep and name.strip() == "OPENAI_API_KEY":
                    value = candidate.strip().strip("\"'")
                    break
    return value


class Provider:
    def __init__(self, key="", allowed=False, max_calls=8, opener=urlopen):
        self._key = key
        self.allowed = allowed and bool(key)
        self.limit, self.calls = max_calls, 0
        self._lock, self._open = threading.Lock(), opener

    def status(self):
        with self._lock:
            return {"enabled": self.allowed, "remainingCalls": max(0, self.limit-self.calls), "models": list(MODELS)}

    def request(self, model, instruction, data, schema, name):
        if model not in MODELS:
            raise ProviderError("Choose a supported model")
        with self._lock:
            if not self.allowed:
                raise ProviderError("Live AI is disabled. Start with --allow-live and a configured key.")
            if self.calls >= self.limit:
                raise ProviderError("This server session has reached its live-call limit.")
            self.calls += 1  # Failed requests also count; no automatic retries.
        payload = {"model": model, "instructions": instruction, "input": json.dumps(data), "store": False,
                   "temperature": 0, "max_output_tokens": 1600,
                   "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}}}
        req = Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode(),
                      headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"}, method="POST")
        started = time.perf_counter()
        try:
            with self._open(req, timeout=40) as response:
                raw = response.read(300001)
            if len(raw) > 300000:
                raise ProviderError("Model response exceeded the local size limit")
            result = json.loads(raw)
        except HTTPError as error:
            raise ProviderError(f"OpenAI request failed (HTTP {error.code}). Check project access, billing or rate limits.") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ProviderError("OpenAI request did not complete. No automatic retry was made.") from None
        if result.get("status") != "completed":
            raise ProviderError("Model response was incomplete or refused")
        text = "".join(part.get("text", "") for out in result.get("output", []) if out.get("type") == "message"
                       for part in out.get("content", []) if part.get("type") == "output_text")
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            raise ProviderError("Model did not return the expected JSON") from None
        return value, {"mode": "live", "model": result.get("model", model), "responseId": result.get("id"),
                       "latencyMs": round((time.perf_counter()-started)*1000, 2),
                       "usage": result.get("usage", {}), "providerCalls": 1}
