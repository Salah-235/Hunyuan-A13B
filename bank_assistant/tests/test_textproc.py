from bankrag.textproc import clean_text, detect_lang, fts_query, normalize, tokenize


def test_hamza_diacritics_and_teh_marbuta_are_unified():
    assert normalize("الأحكامُ") == normalize("الاحكام")
    assert normalize("إيداع") == normalize("ايداع")
    assert normalize("مدة") == normalize("مده")
    assert normalize("على") == normalize("علي")


def test_digits_and_accents():
    assert normalize("٢٥٠") == "250"
    assert normalize("Crédit Échéance") == "credit echeance"


def test_light_stemming_matches_article_and_prefix_variants():
    assert tokenize("للقرض")[0] == tokenize("القرض")[0] == tokenize("قرض")[0]
    assert tokenize("والقروض") == tokenize("القروض")
    assert tokenize("العقارية") == tokenize("عقاري")


def test_stopwords_removed():
    assert tokenize("ما هي شروط القرض في البنك") == tokenize("شروط القرض البنك")
    assert "le" not in tokenize("le taux du crédit")


def test_fts_query_is_quoted_or_expression():
    query = fts_query('taux "crédit" القرض')
    assert query.count(" OR ") == 2
    assert '"credit"' in query


def test_clean_text_folds_presentation_forms():
    assert clean_text("ﻟﻠﻘﺮﺽ") == "للقرض"


def test_detect_lang():
    assert detect_lang("ما هي شروط القرض؟") == "ar"
    assert detect_lang("Quel est le taux ?") == "fr"
