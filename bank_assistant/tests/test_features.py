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
    text, ocr, _, kind = ext.summarizer.document_text(row)
    assert kind == "none"
    assert not ocr
    for n in range(1, 80):
        assert text.count(f"المادة {n}:") == 1


def test_split_parts():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(100))
    parts = _split_parts(text, 1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "\n".join(parts) == text
    assert _split_parts("y" * 2500, 1000) == ["y" * 1000, "y" * 1000, "y" * 500]


# ------------------------------------------------------------------ review fixes

def test_summary_page_markers_follow_real_pages(tmp_path, llm_server):
    from bankrag.ingest import Unit, chunk_units

    app, client = new_app(tmp_path, llm_server)
    doc = upload(client, app, "pages.txt", "المادة 1: نص مؤقت يستبدل بالمقاطع التالية للاختبار.".encode("utf-8"))
    ext = app.extensions["bankrag"]
    # three pages of body text: chunks cross the page breaks
    units = [Unit(f"P{page}-L{i:02d} " + "نص عادي عن شروط القرض ومبلغه ومدته " * 2, page)
             for page in (1, 2, 3) for i in range(12)]
    chunks = chunk_units(units, 1200, 200)
    assert any(c.page_start != c.page_end for c in chunks) and any(c.page_map for c in chunks)
    conn = ext.db.connect()
    with conn:
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc["id"],))
        for idx, c in enumerate(chunks):
            conn.execute("INSERT INTO chunks (doc_id, idx, page_start, page_end, heading, text, ocr, page_map) "
                         "VALUES (?, ?, ?, ?, ?, ?, 0, ?)", (doc["id"], idx, c.page_start, c.page_end, c.heading,
                                                            c.text, c.page_map))
    row = ext.db.query_one("SELECT * FROM documents WHERE id = ?", (doc["id"],))
    text, _, _, kind = ext.summarizer.document_text(row)
    assert kind == "page"
    page, seen = None, []
    for line in text.split("\n"):
        if line.startswith("[p. "):
            page = int(line[4:-1])
        else:
            assert line.startswith(f"P{page}-"), (page, line)
            seen.append(line[:6])
    assert seen == [f"P{p}-L{i:02d}" for p in (1, 2, 3) for i in range(12)]  # every line once, in order

    # chunks indexed before page maps existed fall back to the page range
    with conn:
        conn.execute("UPDATE chunks SET page_map = '' WHERE doc_id = ?", (doc["id"],))
    text, _, _, _ = ext.summarizer.document_text(row)
    assert "[p. 1-2]" in text


def test_unpaged_document_summary_does_not_ask_for_pages(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server)
    doc = upload(client, app, "notes.txt", "المادة 1: نص قصير عن القروض والضمانات المطلوبة.".encode("utf-8"))
    client.summary(doc["id"])
    system = chat_calls()[-1]["messages"][0]["content"]
    assert "never cite pages" in system and "(p. 3)" not in system


def test_truncated_summary_is_shown_with_warning_but_not_cached(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server)
    doc = upload(client, app, "long.txt", ("TRUNCATE-ME\n" + LONG_RULES).encode("utf-8"))
    _, events = client.summary(doc["id"])
    assert {"type": "warning", "code": "summary_truncated"} in events and events[-1]["type"] == "done"
    assert text_of(events)
    assert app.extensions["bankrag"].db.query_one("SELECT COUNT(*) AS n FROM summaries")["n"] == 0


def test_empty_part_notes_fail_without_caching(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server, SUMMARY_PART_CHARS=2000)
    doc = upload(client, app, "long.txt", ("EMPTY-NOTES\n" + LONG_RULES).encode("utf-8"))
    calls = len(chat_calls())
    _, events = client.summary(doc["id"])
    assert events[-1] == {"type": "error", "code": "summary_failed"}
    assert not any("You summarise" in c["messages"][0]["content"] for c in chat_calls()[calls:])
    assert app.extensions["bankrag"].db.query_one("SELECT COUNT(*) AS n FROM summaries")["n"] == 0


def test_concurrent_requests_share_one_summary_job(tmp_path, llm_server, monkeypatch):
    import threading

    monkeypatch.setenv("FAKE_LLM_SUMMARY_DELAY", "1.5")
    app, client = new_app(tmp_path, llm_server, SUMMARY_MAX_JOBS=1)
    a = upload(client, app, "a.txt", LONG_RULES.encode("utf-8"))
    b = upload(client, app, "b.txt", ("ملحق\n" + LONG_RULES).encode("utf-8"))
    before = sum("You summarise" in c["messages"][0]["content"] for c in chat_calls())
    results = {}

    def run(name, doc_id):
        other = Client(app)
        other.http = client.http.application.test_client()
        other.http.set_cookie("bankrag_session", client.http.get_cookie("bankrag_session").value)
        other.csrf = client.csrf
        results[name] = other.summary(doc_id)[1]

    threads = [threading.Thread(target=run, args=("first", a["id"]))]
    threads[0].start()
    import time
    time.sleep(0.5)
    threads.append(threading.Thread(target=run, args=("second", a["id"])))
    threads.append(threading.Thread(target=run, args=("other", b["id"])))
    for t in threads[1:]:
        t.start()
    for t in threads:
        t.join(30)
    after = sum("You summarise" in c["messages"][0]["content"] for c in chat_calls())
    assert after - before == 1                                  # one model run for doc a
    # the second request does not wait on the server: it is told to ask again
    assert results["second"] == [{"type": "status", "stage": "waiting"}, {"type": "pending"}]
    assert results["other"] == [{"type": "error", "code": "summary_busy"}]
    _, again = client.summary(doc_id=a["id"], follow=True)
    assert again[0].get("cached") is True and text_of(again) == text_of(results["first"]).strip()


def test_ocr_percent_normalisation_only_touches_signs_before_numbers():
    from bankrag.ingest import fix_ocr_percent

    assert fix_ocr_percent("القسط الشهري %30 من الدخل") == "القسط الشهري 30% من الدخل"
    assert fix_ocr_percent("Taux : 6,5 % 12 mois") == "Taux : 6,5 % 12 mois"
    assert fix_ocr_percent("taux de 5 %10 ans") == "taux de 5 %10 ans"
    assert fix_ocr_percent("نسبة الفائدة %5 %5.5 %6 %6.5") == "نسبة الفائدة 5% 5.5% 6% 6.5%"
    assert fix_ocr_percent("%30 %40 %50") == "30% 40% 50%"
    assert fix_ocr_percent("12 %5 %6") == "12 %5 %6"          # a row follows the reading of its first sign
    assert fix_ocr_percent("1. %5 et 2024, %3") == "1. 5% et 2024, 3%"


def test_giant_pages_are_rendered_within_the_pixel_cap():
    import io

    from PIL import Image

    from bankrag.ocr import MAX_RENDER_SIDE, PageRenderer

    buffer = io.BytesIO()
    Image.new("RGB", (120, 90), "white").save(buffer, "PDF", resolution=1)  # 8640 x 6480 pt page
    renderer = PageRenderer(buffer.getvalue())
    image = renderer.render(1, 300, grayscale=True)
    renderer.close()
    assert max(image.size) <= MAX_RENDER_SIDE and image.mode == "L"


@pytest.mark.skipif(not TesseractOcr(Config(OCR_ENGINE="tesseract")).available(),
                    reason="tesseract with Arabic language data is not installed")
def test_tesseract_reads_ruled_tables_and_percentages():
    from bankrag.ingest import extract_pdf
    from bankrag.ocr import PageRenderer

    engine = TesseractOcr(Config(OCR_ENGINE="tesseract", OCR_LANGS="ara+fra"))
    assert engine.available()
    renderer = PageRenderer((FIXTURES / "table_ar.pdf").read_bytes())
    table = engine.page_text(renderer.render(1, 300, grayscale=True))
    renderer.close()
    assert "6.5%" in table and "8%" in table and "قرض السيارة" in table
    scanned = "\n".join(u.text for u in extract_pdf((FIXTURES / "scanned_ar.pdf").read_bytes(), engine).units)
    assert "30%" in scanned and "90%" in scanned


def test_document_that_crashed_the_server_twice_is_not_retried(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server)
    doc = upload(client, app, "a.txt", "المادة 1: نص قصير عن القروض والضمانات المطلوبة.".encode("utf-8"))
    ext = app.extensions["bankrag"]
    ext.db.execute("UPDATE documents SET status = 'processing', attempts = 2 WHERE id = ?", (doc["id"],))
    restarted = create_app(make_config(tmp_path, llm_server))
    restarted.extensions["bankrag"].indexer.queue.join()
    row = ext.db.query_one("SELECT status, error FROM documents WHERE id = ?", (doc["id"],))
    assert (row["status"], row["error"]) == ("error", "processing_failed")
    # an administrator can still ask for it to be processed again
    assert client.api("POST", f"/api/documents/{doc['id']}/reindex").status_code == 200


def test_embedding_failure_keeps_the_ocr_warning(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server)
    doc = upload(client, app, "a.txt", "المادة 1: نص قصير عن القروض والضمانات المطلوبة.".encode("utf-8"))
    ext = app.extensions["bankrag"]
    ext.db.execute("UPDATE documents SET warning = 'ocr' WHERE id = ?", (doc["id"],))
    ext.indexer._add_warning(doc["id"], "embedding_failed")
    ext.indexer._add_warning(doc["id"], "embedding_failed")
    assert ext.db.query_one("SELECT warning FROM documents WHERE id = ?", (doc["id"],))["warning"] == "ocr,embedding_failed"


def test_llm_reports_finish_reason_and_strips_unclosed_thinking(tmp_path, llm_server):
    from bankrag.llm import LLMClient, strip_thinking

    assert strip_thinking("<think>still reasoning when cut off") == ""
    llm = LLMClient(make_config(tmp_path, llm_server))
    info = {}
    llm.chat([{"role": "user", "content": "TRUNCATE-ME"}], info=info)
    assert info["finish"] == "length"
    info = {}
    list(llm.stream_chat([{"role": "user", "content": "hello"}], info=info))
    assert info["finish"] == "stop"


def test_followers_get_the_result_of_an_uncached_job(tmp_path, llm_server):
    app, client = new_app(tmp_path, llm_server)
    doc = upload(client, app, "long.txt", ("TRUNCATE-ME\n" + LONG_RULES).encode("utf-8"))
    _, first = client.summary(doc["id"])
    assert {"type": "warning", "code": "summary_truncated"} in first
    # someone who was waiting for that job asks again: same text and warning, no new model run
    calls = len(chat_calls())
    _, follow = client.summary(doc["id"], follow=True)
    assert text_of(follow) == text_of(first) and {"type": "warning", "code": "summary_truncated"} in follow
    assert len(chat_calls()) == calls
    # a new request (not following) tries again
    client.summary(doc["id"])
    assert len(chat_calls()) == calls + 1


def test_scanned_page_in_text_pdf_is_flagged(tmp_path):
    import io

    from PIL import Image
    from pypdf import PdfReader, PdfWriter

    from bankrag.ingest import extract_pdf

    scan = io.BytesIO()
    Image.new("L", (400, 560), 255).save(scan, "PDF", resolution=50)
    writer = PdfWriter()
    for page in PdfReader(FIXTURES / "loans_ar.pdf").pages:
        writer.add_page(page)
    writer.add_page(PdfReader(scan).pages[0])
    writer.add_blank_page(width=595, height=842)
    out = io.BytesIO()
    writer.write(out)
    data = out.getvalue()

    class Broken:
        name, dpi, grayscale = "broken", 100, True

        def page_text(self, image):
            raise RuntimeError("vision server down")

    assert extract_pdf(data).warning == "scanned_pages"            # OCR off: one image page (blank page ignored)
    assert extract_pdf(data, Broken()).warning == "ocr_partial"    # OCR failed on the scanned pages


def test_tesseract_strips_stay_under_the_size_limit():
    from PIL import Image

    engine = TesseractOcr(Config(OCR_ENGINE="tesseract"))
    engine.number_lang = "eng"
    seen = []
    engine._words = lambda image, langs, config: seen.append(image.height) or []
    image = Image.new("L", (2480, 3508), 255)
    words = [{"text": str(1000 + i), "line": (1, 1, i), "n": 1, "box": (100, 10 + (i % 60) * 55, 220, 60 + (i % 60) * 55)}
             for i in range(700)]
    engine._fix_percentages(image, words)
    assert len(seen) > 1 and max(seen) <= engine.STRIP_MAX_HEIGHT + 100



def test_small_logo_on_a_short_page_is_not_a_scanned_page(tmp_path):
    import io

    from PIL import Image
    from pypdf import PdfReader, PdfWriter

    from bankrag.ingest import extract_pdf

    logo = io.BytesIO()
    Image.new("L", (60, 60), 0).save(logo, "PDF", resolution=72)  # 60 x 60 pt image page
    cover = PdfReader(logo).pages[0]
    cover.mediabox.upper_right = (595, 842)                        # the logo is now a corner of an A4 page
    writer = PdfWriter()
    writer.add_page(cover)
    for page in PdfReader(FIXTURES / "loans_ar.pdf").pages:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    assert extract_pdf(out.getvalue()).warning == ""


def test_summary_polls_are_audited_once_and_wait_for_a_refresh(tmp_path, llm_server, monkeypatch):
    import threading
    import time

    app, client = new_app(tmp_path, llm_server)
    doc = upload(client, app, "a.txt", LONG_RULES.encode("utf-8"))
    audits = lambda: [e for e in client.api("GET", "/api/audit").json["events"] if e["action"] == "summary"]
    # a "follow" request with nothing running is a normal request: audited
    _, events = client.summary(doc["id"], follow=True)
    assert events[-1]["type"] == "done" and len(audits()) == 1
    # while an administrator re-makes the summary, polls wait for the new one (not the old cached copy)
    monkeypatch.setenv("FAKE_LLM_SUMMARY_DELAY", "1.5")
    worker = threading.Thread(target=lambda: client.summary(doc["id"], refresh=True))
    worker.start()
    time.sleep(0.5)
    poll = Client(app)
    poll.http.set_cookie("bankrag_session", client.http.get_cookie("bankrag_session").value)
    poll.csrf = client.csrf
    _, events = poll.summary(doc["id"], follow=True)
    assert events == [{"type": "status", "stage": "waiting"}, {"type": "pending"}]
    worker.join(30)
    assert len(audits()) == 2  # the refresh; the poll was not logged again
