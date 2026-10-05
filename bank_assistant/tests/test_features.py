"""OCR of scanned PDFs, comprehensive answers and whole-document summaries."""
import fake_llm
import pytest
from conftest import FIXTURES, Client, create_admin, make_config

from bankrag import create_app
from bankrag.config import Config
from bankrag.ocr import TesseractOcr
from bankrag.rag import _split_parts

LONG_RULES = "\n".join(
    f"المادة {n}: يحدد هذا البند رقم {n} شرطاً خاصاً بمنح القروض الاستهلاكية للأفراد، "
    f"ومبلغه الأقصى {n * 10000} دج، ومدته {n} أشهر حسب ملف الزبون ودخله الشهري الصافي."
    for n in range(1, 80)
)


def new_app(tmp_path, llm_url, **overrides):
    app = create_app(make_config(tmp_path, llm_url, **overrides))
    app.config["TESTING"] = True
    create_admin(app)
    client = Client(app)
    client.login("admin", "admin-pass-123")
    return app, client


def wait_indexed(app):
    app.extensions["bankrag"].indexer.queue.join()


def upload(client, app, name, data):
    response = client.upload(name, data)
    assert response.status_code == 200 and not response.json["rejected"], response.data
    wait_indexed(app)
    doc_id = response.json["documents"][0]["id"]
    return next(d for d in client.api("GET", "/api/documents").json["documents"] if d["id"] == doc_id)


def chat_calls():
    return [payload for path, payload in fake_llm.Handler.calls if path.endswith("/chat/completions")]


def text_of(events):
    return "".join(e["text"] for e in events if e["type"] == "delta")


# ------------------------------------------------------------------ OCR

def test_scanned_pdf_without_ocr_is_reported(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server)
    response = client.upload("scan.pdf", (FIXTURES / "scanned_ar.pdf").read_bytes())
    wait_indexed(app)
    doc = client.api("GET", "/api/documents").json["documents"][0]
    assert response.status_code == 200
    assert doc["status"] == "error" and doc["error"] == "scanned_pdf"


@pytest.mark.skipif(not TesseractOcr(Config(OCR_ENGINE="tesseract")).available(),
                    reason="tesseract with Arabic language data is not installed")
def test_tesseract_reads_scanned_pdf(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server, OCR_ENGINE="tesseract")
    doc = upload(client, app, "قروض ممسوحة.pdf", (FIXTURES / "scanned_ar.pdf").read_bytes())
    assert doc["status"] == "ready" and doc["warning"] == "ocr"

    top = app.extensions["bankrag"].retriever.search(["ما هي المدة القصوى للقرض العقاري؟"], 3)[0]
    assert "30 سنة" in top["text"] and top["ocr"] == 1
    assert "30%" in " ".join(r["text"] for r in app.extensions["bankrag"].retriever.search(["القسط الشهري"], 5))

    _, events = client.ask("ما هي المدة القصوى للقرض العقاري؟")
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    assert sources[0]["ocr"] is True
    prompt = chat_calls()[-1]["messages"]
    assert "OCR (machine-read" in prompt[-1]["content"]
    assert 'Sources marked "OCR"' in prompt[0]["content"]


def test_vision_ocr_uses_the_model(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server, OCR_ENGINE="vision", OCR_VISION_MODEL="fake-vision")
    assert app.extensions["bankrag"].indexer.ocr.name == "vision"
    before = len(fake_llm.Handler.calls)
    doc = upload(client, app, "ادخار.pdf", (FIXTURES / "scanned_ar.pdf").read_bytes())
    assert doc["status"] == "ready" and doc["warning"] == "ocr"

    vision = [p for _, p in fake_llm.Handler.calls[before:] if p.get("model") == "fake-vision"]
    assert len(vision) == 1  # one call per scanned page
    content = vision[0]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    top = app.extensions["bankrag"].retriever.search(["نسبة العائد على حساب الادخار الذهبي"], 3)[0]
    assert "4.75%" in top["text"] and top["ocr"] == 1
    info = client.api("GET", "/api/system").json
    assert info["ocr_engine"] == "vision" and info["ocr_model"] == "fake-vision"


def test_vision_ocr_down_is_reported(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server, OCR_ENGINE="vision", OCR_VISION_MODEL="fake-vision",
                          OCR_VISION_BASE_URL="http://127.0.0.1:9/v1", LLM_TIMEOUT=2)
    client.upload("scan.pdf", (FIXTURES / "scanned_ar.pdf").read_bytes())
    wait_indexed(app)
    doc = client.api("GET", "/api/documents").json["documents"][0]
    assert doc["status"] == "error" and doc["error"] == "ocr_failed"


def test_text_pdf_pages_are_not_sent_to_ocr(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server, OCR_ENGINE="vision", OCR_VISION_MODEL="fake-vision")
    before = len(fake_llm.Handler.calls)
    doc = upload(client, app, "loans.pdf", (FIXTURES / "loans_ar.pdf").read_bytes())
    assert doc["status"] == "ready" and doc["warning"] == ""
    assert not [p for _, p in fake_llm.Handler.calls[before:] if p.get("model") == "fake-vision"]


# ------------------------------------------------------------------ comprehensive answers

def test_full_mode_reads_more_passages(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server, TOP_K=2, TOP_K_FULL=6, CHUNK_SIZE=300, CHUNK_OVERLAP=0)
    upload(client, app, "قروض.txt", LONG_RULES.encode("utf-8"))

    _, normal = client.ask("ما هي شروط القروض الاستهلاكية؟")
    assert len(next(e for e in normal if e["type"] == "sources")["sources"]) == 2
    assert "COMPLETE ANSWER MODE" not in chat_calls()[-1]["messages"][0]["content"]

    _, full = client.ask("ما هي شروط القروض الاستهلاكية؟", mode="full")
    assert len(next(e for e in full if e["type"] == "sources")["sources"]) == 6
    assert full[-1]["type"] == "done"
    assert "COMPLETE ANSWER MODE" in chat_calls()[-1]["messages"][0]["content"]
    details = [e["detail"] for e in client.api("GET", "/api/audit").json["events"] if e["action"] == "ask"]
    assert details[0].startswith("[full] ")


# ------------------------------------------------------------------ summaries

def test_summary_is_streamed_cached_and_permissioned(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server)
    doc = upload(client, app, "نظام القروض العقارية.pdf", (FIXTURES / "loans_ar.pdf").read_bytes())

    calls = len(chat_calls())
    response, events = client.summary(doc["id"])
    assert response.status_code == 200 and response.mimetype == "application/x-ndjson"
    assert {"type": "status", "stage": "writing"} in events and events[-1]["type"] == "done"
    summary = text_of(events)
    assert summary.startswith("نظرة عامة: نظام القروض العقارية للأفراد")
    assert len(chat_calls()) == calls + 1  # short document: a single call
    request = chat_calls()[-1]
    assert "Write in Arabic" in request["messages"][0]["content"]
    assert "[p. 1]" in request["messages"][1]["content"] and "[p. 2]" in request["messages"][1]["content"]

    # second request: served from the cache, no model call
    _, cached = client.summary(doc["id"])
    assert cached[0]["type"] == "summary" and cached[0]["cached"] is True
    assert text_of(cached) == summary.strip() and len(chat_calls()) == calls + 1

    # French is a separate summary
    client.summary(doc["id"], lang="fr")
    assert "Write in French" in chat_calls()[-1]["messages"][0]["content"]

    # employees get the saved summary even when they ask for a new one; administrators can refresh
    client.api("POST", "/api/users", {"username": "employee1", "password": "employee-pass", "role": "user"})
    employee = Client(app)
    employee.login("employee1", "employee-pass")
    before = len(chat_calls())
    _, events = employee.summary(doc["id"], refresh=True)
    assert events[0].get("cached") is True and len(chat_calls()) == before
    _, events = client.summary(doc["id"], refresh=True)
    assert not any(e.get("cached") for e in events) and len(chat_calls()) == before + 1

    assert app.test_client().post(f"/api/documents/{doc['id']}/summary", json={}).status_code in (302, 400, 401)  # CSRF / login
    assert "summary" in [e["action"] for e in client.api("GET", "/api/audit").json["events"]]

    # re-processing the document drops its saved summaries; unknown documents are 404
    db = app.extensions["bankrag"].db
    assert db.query_one("SELECT COUNT(*) AS n FROM summaries")["n"] == 2
    client.api("POST", f"/api/documents/{doc['id']}/reindex")
    wait_indexed(app)
    assert db.query_one("SELECT COUNT(*) AS n FROM summaries")["n"] == 0
    assert client.api("POST", "/api/documents/nope/summary", {}).status_code == 404


def test_long_document_summary_uses_map_reduce(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server, SUMMARY_PART_CHARS=2000)
    doc = upload(client, app, "قروض.txt", LONG_RULES.encode("utf-8"))
    calls = len(chat_calls())
    _, events = client.summary(doc["id"], lang="fr")
    reading = [e for e in events if e.get("stage") == "reading"]
    parts = reading[0]["parts"]
    assert parts >= 4 and [e["part"] for e in reading] == list(range(1, parts + 1))
    assert events[-1]["type"] == "done"
    new = chat_calls()[calls:]
    assert len(new) == parts + 1
    assert all("You take notes on part" in c["messages"][0]["content"] and not c["stream"] for c in new[:-1])
    assert all(c["messages"][-1]["content"].startswith("/no_think") for c in new[:-1])
    final = new[-1]
    assert final["stream"] and "Notes taken from all parts" in final["messages"][1]["content"]
    assert "ملاحظات الجزء 1" in final["messages"][1]["content"]


def test_summary_text_drops_chunk_overlap(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server, CHUNK_SIZE=300, CHUNK_OVERLAP=150)
    doc = upload(client, app, "قروض.txt", LONG_RULES.encode("utf-8"))
    ext = app.extensions["bankrag"]
    assert ext.db.query_one("SELECT COUNT(*) AS n FROM chunks")["n"] > 10
    row = ext.db.query_one("SELECT * FROM documents WHERE id = ?", (doc["id"],))
    text, ocr, _ = ext.summarizer.document_text(row)
    assert not ocr
    for n in range(1, 80):
        assert text.count(f"المادة {n}:") == 1


def test_split_parts():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(100))
    parts = _split_parts(text, 1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "\n".join(parts) == text
    assert _split_parts("y" * 2500, 1000) == ["y" * 1000, "y" * 1000, "y" * 500]
