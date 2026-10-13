"""Bold inside a reflowed paragraph stays bold, and a broken answer never shows tags.

A wrapped paragraph is one model item and one reflowed block. Before 2026-10-11 it
was sent as plain text, so a bold sentence inside it ("All fields in Sections 1 and
2 are required unless marked optional.", CMS-1696) came out regular with nothing to
say so. The bold runs now travel as <b>...</b>; an answer that drops or mangles them
is drawn plain, never with literal tags on the page.
"""
from __future__ import annotations

from pathlib import Path

import fitz

from pdft_engine.skill_placement import _split_bold_answer, place_document

_FONTS = Path(__file__).parents[1] / "pdft_engine" / "assets" / "fonts"
_LATIN_ROLES = {
    "regular": _FONTS / "NotoSans-Regular.ttf",
    "bold": _FONTS / "NotoSans-Bold.ttf",
    "italic": _FONTS / "NotoSans-Italic.ttf",
    "bold_italic": _FONTS / "NotoSans-BoldItalic.ttf",
}
PLAIN = (
    "By signing this form and appointing this representative, you agree that the "
    "representative will be the main contact for your claim and this person may see "
    "your personal medical information."
)
BOLD = "All fields in this section are required unless marked optional."
SPANISH = (
    "Al firmar este formulario y designar a este representante, usted acepta que el "
    "representante será el contacto principal de su reclamación y esta persona podrá "
    "ver su información médica personal. "
)
SPANISH_BOLD = "Todos los campos de esta sección son obligatorios, salvo que se indique."


def _source(path: Path) -> None:
    """Three wrapped 11 pt lines; the paragraph's last sentence is set in bold."""
    words = [(w, False) for w in PLAIN.split()] + [(w, True) for w in BOLD.split()]
    lines, current = [], []
    for word in words:
        trial = " ".join(w for w, _ in [*current, word])
        if current and fitz.get_text_length(trial, "helv", 11) > 300:
            lines.append(current)
            current = []
        current.append(word)
    lines.append(current)
    assert len(lines) >= 3, lines
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)
        for row, line in enumerate(lines):
            x = 72.0
            for index, (word, bold) in enumerate(line):
                text = word + (" " if index < len(line) - 1 else "")
                font = "hebo" if bold else "helv"
                page.insert_text((x, 120 + 14 * row), text, fontname=font, fontsize=11)
                x += fitz.get_text_length(text, font, 11)
        doc.save(path)


def _place(tmp_path: Path, answer):
    source = tmp_path / "source.pdf"
    _source(source)
    seen: list[str] = []

    def translate(items):
        seen.extend(items)
        return {item: answer(item) for item in items}

    result = place_document(source_pdf=source, translate=translate, lang="es",
                            font=_LATIN_ROLES, workdir=tmp_path / "work")
    with fitz.open(result.pdf) as doc:
        spans = [span for block in doc[0].get_text("dict")["blocks"] if block["type"] == 0
                 for line in block["lines"] for span in line["spans"] if span["text"].strip()]
    return seen, result, spans


def test_the_bold_sentence_is_sent_tagged_and_drawn_bold(tmp_path):
    seen, result, spans = _place(
        tmp_path, lambda item: SPANISH + "<b>" + SPANISH_BOLD + "</b>")
    [item] = seen
    assert item == PLAIN + " <b>" + BOLD + "</b>"
    assert all(o.outcome in {"placed", "shrunk"} for o in result.outcomes), result.outcomes
    bold = " ".join(s["text"] for s in spans if "Bold" in s["font"])
    regular = " ".join(s["text"] for s in spans if "Bold" not in s["font"])
    assert " ".join(bold.split()) == SPANISH_BOLD
    assert "Al firmar" in regular and "Todos" not in regular
    assert not any("<" in s["text"] or ">" in s["text"] for s in spans)


def test_an_answer_with_broken_tags_is_drawn_plain_without_tags(tmp_path):
    for broken in (SPANISH + SPANISH_BOLD,                      # tags dropped
                   SPANISH + "<b>" + SPANISH_BOLD,              # never closed
                   SPANISH + "<b>" + SPANISH_BOLD + "</b",      # mangled close
                   SPANISH + "<B>" + SPANISH_BOLD + "</B>",     # not our tag
                   SPANISH + "<b><b>" + SPANISH_BOLD + "</b></b>"):  # nested
        work = tmp_path / str(abs(hash(broken)))
        work.mkdir()
        _seen, result, spans = _place(work, lambda item, broken=broken: broken)
        text = " ".join(" ".join(s["text"] for s in spans).split())
        assert "<" not in text and ">" not in text and "/b" not in text, (broken, text)
        assert "Al firmar" in text and "salvo que se indique" in text, (broken, text)
        assert not any("Bold" in s["font"] for s in spans), broken
        assert all(o.outcome in {"placed", "shrunk"} for o in result.outcomes), broken


def test_split_bold_answer_accepts_only_whole_runs():
    assert _split_bold_answer("a <b>b</b> c <b>d</b>") == ("a b c d", "a <b>b</b> c <b>d</b>")
    for broken, plain in (("a <b> </b> c", "a   c"), ("a </b>b<b> c", "a b c"),
                          ("a <strong>b</strong>", "a b"), ("a <b>b</b> <i>c</i>", "a b c")):
        assert _split_bold_answer(broken) == (plain, None), broken


def test_a_single_line_with_a_bold_lead_keeps_only_the_lead_bold(tmp_path):
    """Drawn whole in its heavier weight, "Caution: To claim..." came out all bold;
    the line now reads regular (its majority) with the lead bold."""
    lead, rest = "Caution:", " To claim certain credits you need a valid number."
    source = tmp_path / "source.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 120), lead, fontname="hebo", fontsize=10)
        page.insert_text((72 + fitz.get_text_length(lead, "hebo", 10), 120), rest,
                         fontname="helv", fontsize=10)
        doc.save(source)
    for answer, bold_text in (
        ("<b>Precaución:</b> Para reclamar ciertos créditos necesita un número válido.",
         "Precaución:"),
        ("<b>Precaución: Para reclamar ciertos créditos necesita un número válido.", ""),
    ):
        work = tmp_path / str(len(bold_text))
        seen: list[str] = []

        def translate(items, answer=answer, seen=seen):
            seen.extend(items)
            return {item: answer for item in items}

        result = place_document(source_pdf=source, translate=translate, lang="es",
                                font=_LATIN_ROLES, workdir=work)
        assert seen == ["<b>" + lead + "</b>" + rest]
        [outcome] = result.outcomes
        assert outcome.outcome in {"placed", "shrunk"} and "<" not in outcome.translation
        with fitz.open(result.pdf) as doc:
            spans = [s for b in doc[0].get_text("dict")["blocks"] if b["type"] == 0
                     for line in b["lines"] for s in line["spans"] if s["text"].strip()]
        drawn = " ".join(s["text"] for s in spans)
        assert "<" not in drawn and ">" not in drawn, drawn
        assert " ".join(s["text"] for s in spans if "Bold" in s["font"]).strip() == bold_text


def test_a_mostly_bold_line_keeps_its_regular_words_regular(tmp_path):
    """"Influenza vaccine can prevent influenza (flu)." is mostly bold; its line weight
    is bold, and without the regular base its "can prevent" came out bold too."""
    parts = [("Influenza vaccine ", "hebo"), ("can prevent ", "helv"),
             ("influenza (flu)", "hebo"), (".", "helv")]
    source = tmp_path / "source.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)
        x = 72.0
        for text, font in parts:
            page.insert_text((x, 120), text, fontname=font, fontsize=12)
            x += fitz.get_text_length(text, font, 12)
        doc.save(source)
    answer = "<b>La vacuna antigripal</b> puede prevenir <b>la influenza (gripe)</b>."
    result = place_document(source_pdf=source, translate=lambda items: {i: answer for i in items},
                            lang="es", font=_LATIN_ROLES, workdir=tmp_path / "work")
    assert [o.outcome for o in result.outcomes] in (["placed"], ["shrunk"])
    with fitz.open(result.pdf) as doc:
        spans = [s for b in doc[0].get_text("dict")["blocks"] if b["type"] == 0
                 for line in b["lines"] for s in line["spans"] if s["text"].strip()]
    regular = " ".join(s["text"] for s in spans if "Bold" not in s["font"])
    assert "puede prevenir" in regular and "vacuna" not in regular, spans
