"""A tiny OpenAI-compatible server used for tests and offline demos.

It imitates Hunyuan-A13B's output format (<think>…</think><answer>…</answer>),
answers by quoting the first source it receives, and returns deterministic
bag-of-words embeddings.  Run standalone with:  python tests/fake_llm.py 8001
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 256


def fake_embedding(text):
    vector = [0.0] * DIM
    for token in re.findall(r"\w+", text.lower()):
        digest = hashlib.md5(token.encode("utf-8")).digest()
        vector[digest[0] % DIM] += 1.0
    return vector


# what the fake vision model "reads" on any scanned page image
VISION_TEXT = ("بطاقة الادخار الذهبية\n"
               "المادة 1: نسبة العائد السنوي على حساب الادخار الذهبي هي 4.75% تدفع كل ثلاثة أشهر.\n"
               "المادة 2: الحد الأدنى للإيداع الأول هو 20 000 دج.")


def answer_for(messages):
    system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
    user = messages[-1]["content"]
    if isinstance(user, list):  # vision request: transcribe the page image
        if any(part.get("type") == "image_url" for part in user):
            return VISION_TEXT
        user = " ".join(part.get("text", "") for part in user)
    if "You take notes on part" in system:
        part = re.search(r"part (\d+) of (\d+)", system)
        first = next((line for line in user.split("\n\n", 1)[-1].splitlines()
                      if line.strip() and not line.startswith("[")), "")
        return f"- ملاحظات الجزء {part.group(1)}: {first.strip()[:80]} (p. 1)"
    if "You summarise one internal bank document" in system:
        body = user.split(":\n", 1)[-1]
        lines = [line for line in body.splitlines() if line.strip() and not line.startswith("[")]
        note = " OCR-NOTE" if "machine-read (OCR)" in system else ""
        return "نظرة عامة: " + (lines[0].strip() if lines else "") + f" (p. 1){note}\n\n- عدد الأسطر: {len(lines)}"
    if "search queries" in system:
        question = user.split("Latest question:", 1)[-1].strip()
        return f"{question}\n{question} (traduction)\nmots clés"
    match = re.search(r"\[1\][^\n]*\n(.+?)(?:\n\n\[2\]|\n\nQuestion:)", user, re.S)
    if not match:
        return "لم أجد إجابة لهذا السؤال في الوثائق المتوفرة."
    first_line = next((line for line in match.group(1).splitlines() if line.strip()), "")
    return f"وفقاً للوثائق: **{first_line.strip()}** [1]\n\n- تفصيل إضافي من المصدر [1]"


class Handler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._json({"object": "list", "data": [{"id": "fake-hunyuan", "object": "model"}]})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        Handler.calls.append((self.path, payload))
        if self.path.endswith("/embeddings"):
            inputs = payload["input"] if isinstance(payload["input"], list) else [payload["input"]]
            return self._json({"data": [{"index": i, "embedding": fake_embedding(t)} for i, t in enumerate(inputs)]})
        if not self.path.endswith("/chat/completions"):
            return self._json({"error": "not found"}, 404)
        text = answer_for(payload["messages"])
        full = f"<think>\nأحلل المصادر المتوفرة...\n</think>\n<answer>\n{text}\n</answer>"
        if not payload.get("stream"):
            return self._json({"choices": [{"message": {"role": "assistant", "content": full}}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(full), 7):  # small pieces so tags get split across chunks
            chunk = {"choices": [{"delta": {"content": full[i:i + 7]}, "index": 0}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(float(os.environ.get("FAKE_LLM_DELAY", "0.002")))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def start(port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    srv = start(int(sys.argv[1]) if len(sys.argv) > 1 else 8001)
    print(f"fake LLM on http://127.0.0.1:{srv.server_address[1]}/v1", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
