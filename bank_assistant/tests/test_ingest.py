from conftest import FIXTURES, sample_docx_fr, sample_xlsx

from bankrag.ingest import Unit, chunk_units, extract, visual_to_logical


def test_arabic_pdf_lines_are_in_reading_order():
    result = extract((FIXTURES / "loans_ar.pdf").read_bytes(), ".pdf")
    lines = [u.text for u in result.units]
    assert result.pages == 2
    assert result.warning == ""
    assert "المادة 3: يجب ألا يتجاوز مبلغ القسط الشهري 30% من الدخل الشهري الصافي للمقترض." in lines
    assert "المادة 4: تبلغ المدة القصوى للقرض العقاري 30 سنة، ولا تقل عن 5 سنوات." in lines
    assert "المادة 6: تحدد نسبة الفائدة السنوية للقرض العقاري ب 6.5% ثابتة طوال مدة القرض." in lines
    page_of = {u.text: u.page for u in result.units}
    assert page_of["المادة 9: يدرس الملف في أجل أقصاه 15 يوم عمل من تاريخ إيداعه كاملاً."] == 2


def test_visual_to_logical_cases():
    def visual(on_screen):
        """Glyphs as they appear on screen, read left to right."""
        return list(on_screen)

    assert visual_to_logical(visual("ةنس 30 ىصقلا ةدملا")) == "المدة القصى 30 سنة"
    assert visual_to_logical(visual("(ةتباث) %6.5")) == "6.5% (ثابتة)"
    assert visual_to_logical(visual("CIB Classic ةقاطب"), rtl_hint=True) == "بطاقة CIB Classic"
    # French line with an Arabic phrase inside
    assert visual_to_logical(visual("le compte ريفوتلا باسح ici")) == "le compte حساب التوفير ici"
    assert visual_to_logical(visual("Taux 2,5 %")) == "Taux 2,5 %"
    # both ends Arabic -> right-to-left whatever the hint
    assert visual_to_logical(visual("ةيونس 1200 مث CIB Gold ةقاطب"), rtl_hint=False) == "بطاقة CIB Gold ثم 1200 سنوية"


def test_chunks_track_headings_and_pages():
    result = extract((FIXTURES / "loans_ar.pdf").read_bytes(), ".pdf")
    chunks = chunk_units(result.units, size=400, overlap=80)
    assert len(chunks) >= 3
    rate = next(c for c in chunks if "6.5%" in c.text)
    assert rate.page_start == 2
    assert "الفصل الثالث" in rate.heading
    for chunk in chunks:
        assert len(chunk.text) <= 400 + 200


def test_long_paragraph_is_split():
    units = [Unit("جملة طويلة جداً. " * 300, 1, 0)]
    chunks = chunk_units(units, size=500, overlap=50)
    assert len(chunks) > 5
    assert all(len(c.text) <= 560 for c in chunks)


def test_docx_paragraphs_headings_and_tables():
    result = extract(sample_docx_fr(), ".docx")
    texts = [u.text for u in result.units]
    assert any(u.level == 1 for u in result.units)
    assert "Article 2 : La durée maximale du crédit à la consommation est de 60 mois." in texts
    assert "Jusqu'à 500 000 DA | 8,25 %" in texts


def test_xlsx_rows_keep_column_names():
    result = extract(sample_xlsx(), ".xlsx")
    texts = [u.text for u in result.units]
    assert "Produit: Carte CIB Gold | Frais mensuels: 250 DA | Plafond retrait: 100 000 DA" in texts
    assert result.units[0].level == 1  # sheet name acts as a heading


def test_text_and_csv():
    text = extract("المادة 1: نص تجريبي.\n\nسطر ثانٍ".encode("cp1256"), ".txt")
    assert text.units[0].text == "المادة 1: نص تجريبي."
    csv = extract("Produit;Taux\nCrédit auto;7 %\n".encode("utf-8"), ".csv")
    assert csv.units[1].text == "Produit: Crédit auto | Taux: 7 %"
