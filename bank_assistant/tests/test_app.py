import fake_llm
from conftest import FIXTURES, Client, create_admin, make_config, sample_docx_fr, sample_xlsx

from bankrag import create_app


def wait_indexed(app):
    app.extensions["bankrag"].indexer.queue.join()


def upload_samples(client, app):
    pdf = client.upload("نظام القروض العقارية.pdf", (FIXTURES / "loans_ar.pdf").read_bytes(), "القروض")
    docx = client.upload("Credit_consommation.docx", sample_docx_fr(), "Crédits")
    xlsx = client.upload("Tarifs cartes.xlsx", sample_xlsx(), "Cartes")
    for response in (pdf, docx, xlsx):
        assert response.status_code == 200, response.data
        assert response.json["rejected"] == []
    wait_indexed(app)
    docs = {d["filename"]: d for d in client.api("GET", "/api/documents").json["documents"]}
    assert all(d["status"] == "ready" for d in docs.values()), docs
    return docs


# ------------------------------------------------------------------ setup & login

def test_first_run_requires_setup_code(app):
    client = Client(app)
    assert client.page("/").headers["Location"].startswith("/login")
    assert client.page("/login").headers["Location"] == "/setup"
    client.page("/setup")
    bad = client.form("/setup", {"code": "WRONG", "username": "boss", "password": "longpassword",
                                 "confirm": "longpassword"})
    assert bad.status_code == 200
    assert "رمز التهيئة غير صحيح".encode() in bad.data
    create_admin(app)
    assert client.page("/setup").headers["Location"] == "/login"


def test_login_logout_and_lockout(app):
    username, password = create_admin(app)
    client = Client(app)
    client.page("/login")
    for _ in range(5):
        response = client.form("/login", {"username": username, "password": "wrong-password"})
        assert "غير صحيحة".encode() in response.data
    locked = client.form("/login", {"username": username, "password": password})
    assert "إيقاف".encode() in locked.data  # locked even with the right password
    other = Client(app)
    other.http.environ_base["REMOTE_ADDR"] = "10.0.0.9"
    assert other.login(username, password).status_code == 302
    assert other.page("/").status_code == 200
    other.form("/logout", {})
    assert other.page("/").status_code == 302


def test_csrf_is_required(admin_client):
    response = admin_client.http.post("/api/users", json={"username": "x", "password": "12345678"})
    assert response.status_code == 400
    assert response.json["error"] == "csrf_failed"


def test_language_switch(admin_client):
    page = admin_client.page("/").get_data(as_text=True)
    assert 'dir="rtl"' in page and "مساعد الوثائق البنكية" in page
    admin_client.http.get("/lang/fr?next=/")
    page = admin_client.page("/").get_data(as_text=True)
    assert 'dir="ltr"' in page and "Assistant documentaire bancaire" in page


def test_security_headers(admin_client):
    response = admin_client.page("/")
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"


# ------------------------------------------------------------------ users & roles

def test_user_management_and_permissions(app, admin_client):
    created = admin_client.api("POST", "/api/users", {"username": "employee1", "display_name": "موظف",
                                                       "password": "employee-pass", "role": "user"})
    assert created.status_code == 200
    assert admin_client.api("POST", "/api/users", {"username": "employee1", "password": "another-pass"}
                            ).json["error"] == "username_taken"
    assert admin_client.api("POST", "/api/users", {"username": "x y", "password": "another-pass"}
                            ).json["error"] == "username_invalid"
    assert admin_client.api("POST", "/api/users", {"username": "short", "password": "123"}
                            ).json["error"] == "password_min"

    employee = Client(app)
    employee.login("employee1", "employee-pass")
    assert employee.page("/").status_code == 200
    assert employee.page("/admin").status_code == 403
    assert employee.api("GET", "/api/users").status_code == 403
    assert employee.upload("x.txt", b"hello").status_code == 403
    assert employee.api("GET", "/api/documents").status_code == 200

    # disabling the account ends the employee's session immediately
    uid = created.json["user"]["id"]
    assert admin_client.api("PATCH", f"/api/users/{uid}", {"active": False}).status_code == 200
    assert employee.api("GET", "/api/documents").status_code == 401
    assert Client(app).login("employee1", "employee-pass").status_code == 200  # login refused (form shown)

    # re-enable + password reset
    admin_client.api("PATCH", f"/api/users/{uid}", {"active": True, "password": "brand-new-pass"})
    assert Client(app).login("employee1", "brand-new-pass").status_code == 302


def test_admin_cannot_lock_themselves_out(admin_client):
    me = admin_client.api("GET", "/api/users").json["me"]
    assert admin_client.api("DELETE", f"/api/users/{me}").json["error"] == "cannot_change_self"
    assert admin_client.api("PATCH", f"/api/users/{me}", {"role": "user"}).json["error"] == "cannot_change_self"


def test_change_own_password_keeps_session(app, admin_client):
    bad = admin_client.api("POST", "/api/account/password", {"current": "nope", "new": "x" * 10, "confirm": "x" * 10})
    assert bad.json["error"] == "wrong_password"
    ok = admin_client.api("POST", "/api/account/password",
                          {"current": "admin-pass-123", "new": "new-admin-pass", "confirm": "new-admin-pass"})
    assert ok.status_code == 200
    assert admin_client.api("GET", "/api/users").status_code == 200
    assert Client(app).login("admin", "new-admin-pass").status_code == 302


# ------------------------------------------------------------------ documents, search, answers

def test_upload_index_and_search(app, admin_client):
    docs = upload_samples(admin_client, app)
    pdf = docs["نظام القروض العقارية.pdf"]
    assert pdf["pages"] == 2 and pdf["chunks"] >= 3 and pdf["category"] == "القروض"

    retriever = app.extensions["bankrag"].retriever
    top = retriever.search(["ما هي المدة القصوى للقرض العقاري؟"], 3)[0]
    assert "المادة 4" in top["text"] and top["title"] == "نظام القروض العقارية"
    top = retriever.search(["كم نسبة الفائدة على القرض العقاري"], 3)[0]
    assert "6.5%" in top["text"] and top["page_start"] == 2
    top = retriever.search(["Quel est le taux d'intérêt du crédit à la consommation ?"], 3)[0]
    assert "8,25 %" in top["text"]
    top = retriever.search(["plafond de retrait carte CIB Gold"], 3)[0]
    assert "100 000 DA" in top["text"]

    # unsupported files are rejected, nothing is stored
    rejected = admin_client.upload("virus.exe", b"MZ")
    assert rejected.json["rejected"][0]["name"] == "virus.exe"


def test_ask_streams_answer_with_sources(app, admin_client):
    upload_samples(admin_client, app)
    response, events = admin_client.ask("ما هي المدة القصوى للقرض العقاري؟")
    assert response.status_code == 200
    assert response.mimetype == "application/x-ndjson"
    types = [e["type"] for e in events]
    assert types[0] == "status" and "sources" in types and types[-1] == "done"
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    assert sources[0]["title"] == "نظام القروض العقارية"
    answer = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "[1]" in answer
    assert "<think>" not in answer and "أحلل" not in answer and "<answer>" not in answer
    # the model receives numbered sources and the thinking-free rewrite request
    paths = [p for p, _ in fake_llm.Handler.calls if p.endswith("/chat/completions")]
    assert len(paths) >= 2
    rewrite = next(payload for _, payload in reversed(fake_llm.Handler.calls)
                   if "search queries" in payload["messages"][0]["content"])
    assert rewrite["messages"][-1]["content"].startswith("/no_think")


def test_ask_respects_selected_sources(app, admin_client):
    docs = upload_samples(admin_client, app)
    only_fr = [docs["Credit_consommation.docx"]["id"]]
    _, events = admin_client.ask("القرض العقاري", doc_ids=only_fr)
    sources = next(e for e in events if e["type"] == "sources")["sources"]
    assert {s["doc_id"] for s in sources} <= set(only_fr)
    response, _ = admin_client.ask("سؤال", doc_ids=[])
    assert response.status_code == 400


def test_ask_without_documents_says_not_found(admin_client):
    _, events = admin_client.ask("ما هي شروط القرض؟")
    answer = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "لم أجد" in answer


def test_files_require_login_and_are_audited(app, admin_client):
    docs = upload_samples(admin_client, app)
    doc_id = docs["نظام القروض العقارية.pdf"]["id"]
    response = admin_client.http.get(f"/files/{doc_id}")
    assert response.status_code == 200 and response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
    anonymous = app.test_client().get(f"/files/{doc_id}")
    assert anonymous.status_code == 302
    actions = [e["action"] for e in admin_client.api("GET", "/api/audit").json["events"]]
    assert {"setup", "login_ok", "upload", "view_file"} <= set(actions)


def test_edit_reindex_and_delete_document(app, admin_client):
    docs = upload_samples(admin_client, app)
    doc_id = docs["Tarifs cartes.xlsx"]["id"]
    edited = admin_client.api("PATCH", f"/api/documents/{doc_id}", {"title": "Grille tarifaire", "category": "Tarifs"})
    assert edited.json["document"]["title"] == "Grille tarifaire"
    assert admin_client.api("POST", f"/api/documents/{doc_id}/reindex").status_code == 200
    wait_indexed(app)
    assert admin_client.api("DELETE", f"/api/documents/{doc_id}").status_code == 200
    ctx = app.extensions["bankrag"]
    assert ctx.db.query_one("SELECT COUNT(*) AS n FROM chunks WHERE doc_id = ?", (doc_id,))["n"] == 0
    assert ctx.db.query_one("SELECT COUNT(*) AS n FROM chunks_fts WHERE doc_id = ?", (doc_id,))["n"] == 0
    assert not list(ctx.cfg.FILES_DIR.glob(f"{doc_id}*"))


def test_llm_down_reports_error(tmp_path):
    app = create_app(make_config(tmp_path, "http://127.0.0.1:9/v1", LLM_TIMEOUT=2))
    create_admin(app)
    client = Client(app)
    client.login("admin", "admin-pass-123")
    client.upload("loans.pdf", (FIXTURES / "loans_ar.pdf").read_bytes())
    wait_indexed(app)
    _, events = client.ask("ما هي نسبة الفائدة؟")
    assert events[-1] == {"type": "error", "code": "llm_unavailable", "detail": events[-1]["detail"]}
    assert any(e["type"] == "sources" and e["sources"] for e in events)  # search still worked


def test_embeddings_enable_semantic_search(tmp_path, llm_server):
    app = create_app(make_config(tmp_path, llm_server, EMBEDDING_MODEL="fake-embed"))
    create_admin(app)
    client = Client(app)
    client.login("admin", "admin-pass-123")
    client.upload("fr.docx", sample_docx_fr())
    wait_indexed(app)
    doc = client.api("GET", "/api/documents").json["documents"][0]
    assert doc["embedded"] == 1
    ctx = app.extensions["bankrag"]
    hits = ctx.retriever.dense_search("durée maximale du crédit", 3)
    assert hits and hits[0][1] > 0


def test_idle_session_expires(admin_client):
    with admin_client.http.session_transaction() as sess:
        sess["seen"] = 0
    response = admin_client.api("POST", "/api/users", {"username": "late", "password": "12345678"})
    assert response.status_code == 401
    assert admin_client.page("/").headers["Location"].startswith("/login")


def test_model_returning_nothing_is_an_error(app, admin_client):
    upload_samples(admin_client, app)
    app.extensions["bankrag"].llm.stream_chat = lambda *args, **kwargs: iter([("thinking", None)])
    _, events = admin_client.ask("ما هي نسبة الفائدة؟")
    assert {"type": "status", "stage": "thinking"} in events
    assert events[-1] == {"type": "error", "code": "error_empty"}
