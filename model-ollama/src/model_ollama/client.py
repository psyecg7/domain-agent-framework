from __future__ import annotations

from json import dumps, loads
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaClient(Protocol):
    def generate(self, prompt: str) -> str:
        ...


class HttpOllamaClient:
    def __init__(self, *, model: str, base_url: str = "http://localhost:11434", timeout: float = 30.0) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(self, prompt: str) -> str:
        request = Request(
            f"{self.base_url}/api/generate",
            data=dumps({"model": self.model, "prompt": prompt, "stream": False}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            raise OllamaClientError("Ollama request failed") from exc
        generated = payload.get("response")
        if not isinstance(generated, str):
            raise OllamaClientError("Ollama response did not contain text")
        return generated


class OllamaClientError(RuntimeError):
    pass


__all__ = ["OllamaClient", "HttpOllamaClient", "OllamaClientError"]
