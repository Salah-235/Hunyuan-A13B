"""Client for OpenAI-compatible chat / embedding APIs (vLLM, SGLang, Ollama, ...)."""
import json
import re

import requests


class LLMError(Exception):
    pass


_TAGS = ("<think>", "</think>", "<answer>", "</answer>")


class ThinkFilter:
    """Streams only the final answer: hides <think>…</think>, drops <answer> tags.

    Hunyuan-A13B replies as "<think>…</think><answer>…</answer>"; other
    reasoning models use "<think>…</think>answer". Tags may arrive split
    across stream chunks, so a possible partial tag is held back.
    """

    def __init__(self):
        self.pending = ""
        self.in_think = False
        self.started = False

    def _holdback(self, text):
        for size in range(min(len(text), 9), 0, -1):
            tail = text[-size:]
            if any(tag.startswith(tail) for tag in _TAGS):
                return size
        return 0

    def feed(self, text):
        """Returns (visible_text, is_thinking)."""
        self.pending += text
        out = []
        thinking = False
        while self.pending:
            if self.in_think:
                thinking = True
                end = self.pending.find("</think>")
                if end < 0:
                    self.pending = self.pending[-8:]
                    break
                self.pending = self.pending[end + len("</think>"):]
                self.in_think = False
                continue
            start = self.pending.find("<think>")
            if start >= 0:
                out.append(self.pending[:start])
                self.pending = self.pending[start + len("<think>"):]
                self.in_think = True
                continue
            hold = self._holdback(self.pending)
            emit = self.pending[: len(self.pending) - hold] if hold else self.pending
            self.pending = self.pending[len(emit):]
            out.append(emit)
            break
        visible = "".join(out).replace("<answer>", "").replace("</answer>", "")
        if not self.started:
            visible = visible.lstrip()
            if visible:
                self.started = True
        return visible, thinking

    def finish(self):
        if self.in_think:
            return ""
        rest = self.pending.replace("<answer>", "").replace("</answer>", "")
        self.pending = ""
        return rest.lstrip() if not self.started else rest


def strip_thinking(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL)  # cut off while still reasoning
    match = re.search(r"<answer>(.*?)(</answer>|$)", text, flags=re.DOTALL)
    if match:
        text = match.group(1)
    return text.strip()


class LLMClient:
    def __init__(self, cfg):
        self.cfg = cfg

    def _headers(self, key):
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _payload(self, messages, max_tokens, temperature, thinking, stream):
        messages = [dict(m) for m in messages]
        if not thinking and self.cfg.LLM_NO_THINK_PREFIX:
            for message in reversed(messages):
                if message["role"] == "user":
                    message["content"] = self.cfg.LLM_NO_THINK_PREFIX + message["content"]
                    break
        payload = {
            "model": self.cfg.LLM_MODEL,
            "messages": messages,
            "max_tokens": max_tokens or self.cfg.LLM_MAX_TOKENS,
            "temperature": self.cfg.LLM_TEMPERATURE if temperature is None else temperature,
            "stream": stream,
        }
        payload.update(self.cfg.LLM_EXTRA_BODY or {})
        return payload

    def _post(self, url, payload, key, stream):
        try:
            response = requests.post(
                url,
                headers=self._headers(key),
                data=json.dumps(payload),
                stream=stream,
                timeout=(15, self.cfg.LLM_TIMEOUT),
            )
        except requests.RequestException as exc:
            raise LLMError(f"connection: {exc}") from exc
        if response.status_code != 200:
            detail = response.text[:500]
            response.close()
            raise LLMError(f"HTTP {response.status_code}: {detail}")
        return response

    def chat(self, messages, max_tokens=None, temperature=None, thinking=None, info=None):
        """The answer text. `info`, if given, receives {"finish": finish_reason} ("length" = cut off)."""
        thinking = self.cfg.LLM_THINKING if thinking is None else thinking
        payload = self._payload(messages, max_tokens, temperature, thinking, stream=False)
        response = self._post(self.cfg.LLM_BASE_URL + "/chat/completions", payload,
                              self.cfg.LLM_API_KEY, stream=False)
        try:
            choice = response.json()["choices"][0]
            content = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("bad response") from exc
        if info is not None:
            info["finish"] = choice.get("finish_reason")
        return strip_thinking(content)

    def stream_chat(self, messages, max_tokens=None, temperature=None, thinking=None, info=None):
        """Yields ("thinking", None) while the model reasons, then ("delta", text) pieces.

        `info`, if given, receives {"finish": finish_reason} once the stream ends."""
        thinking = self.cfg.LLM_THINKING if thinking is None else thinking
        payload = self._payload(messages, max_tokens, temperature, thinking, stream=True)
        response = self._post(self.cfg.LLM_BASE_URL + "/chat/completions", payload,
                              self.cfg.LLM_API_KEY, stream=True)
        filt = ThinkFilter()
        announced = False
        try:
            for raw in response.iter_lines(decode_unicode=False):
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    choice = json.loads(data)["choices"][0]
                except (ValueError, KeyError, IndexError):
                    continue
                if choice.get("finish_reason") and info is not None:
                    info["finish"] = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if (delta.get("reasoning_content") or delta.get("reasoning")) and not announced:
                    announced = True
                    yield "thinking", None
                text = delta.get("content")
                if not text:
                    continue
                visible, is_thinking = filt.feed(text)
                if is_thinking and not announced:
                    announced = True
                    yield "thinking", None
                if visible:
                    yield "delta", visible
            rest = filt.finish()
            if rest:
                yield "delta", rest
        except requests.RequestException as exc:
            raise LLMError(f"stream: {exc}") from exc
        finally:
            response.close()

    # ------------------------------------------------------------------ embeddings

    @property
    def embeddings_enabled(self):
        return bool(self.cfg.EMBEDDING_MODEL)

    def embed(self, texts, batch_size=32):
        vectors = []
        for i in range(0, len(texts), batch_size):
            payload = {"model": self.cfg.EMBEDDING_MODEL, "input": texts[i:i + batch_size]}
            response = self._post(self.cfg.EMBEDDING_BASE_URL + "/embeddings", payload,
                                  self.cfg.EMBEDDING_API_KEY, stream=False)
            try:
                data = sorted(response.json()["data"], key=lambda d: d.get("index", 0))
                vectors.extend(d["embedding"] for d in data)
            except (ValueError, KeyError) as exc:
                raise LLMError("bad embeddings response") from exc
        return vectors
