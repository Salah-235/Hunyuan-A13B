from bankrag.llm import ThinkFilter, strip_thinking

RAW = "<think>\nreasoning about [1] and <tags>\n</think>\n<answer>\nLa réponse est **60 mois** [1].\n</answer>"


def run_filter(text, size):
    filt = ThinkFilter()
    out, thinking = [], False
    for i in range(0, len(text), size):
        visible, is_thinking = filt.feed(text[i:i + size])
        thinking = thinking or is_thinking
        out.append(visible)
    out.append(filt.finish())
    return "".join(out), thinking


def test_think_filter_any_chunking():
    for size in range(1, 15):
        text, thinking = run_filter(RAW, size)
        assert text.strip() == "La réponse est **60 mois** [1]."
        assert thinking


def test_think_filter_plain_text_passthrough():
    text, thinking = run_filter("Réponse < 5 % sans balises", 3)
    assert text == "Réponse < 5 % sans balises"
    assert not thinking


def test_strip_thinking():
    assert strip_thinking(RAW) == "La réponse est **60 mois** [1]."
    assert strip_thinking("<think></think>بدون وسوم") == "بدون وسوم"
