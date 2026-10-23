"""Letters drawn under a form field are named (placement plan F1, ruled 2026-10-22).

A missed case ships a form whose words or checkboxes are hidden, as the IES
certificate and AO 240 did on the Spanish corpus, so the detector is pinned here.
"""
import fitz

from pdft_engine import field_overlap

ORIGIN = (72.0, 100.0)
CORE = "Learned by completing online sessions."
SPANISH = "Aprendieron completando sesiones en linea."


def _pdf(path, text, *, field=None):
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text(ORIGIN, text, fontsize=11, fontname="helv")
    if field is not None:
        widget = fitz.Widget()
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_name = "Count"
        widget.rect = fitz.Rect(field)
        page.add_widget(widget)
    doc.save(str(path))
    doc.close()
    return path


def _gap_field(text_before):
    """A field in the source's gap, right after `text_before`."""
    x = ORIGIN[0] + fitz.get_text_length(text_before, fontname="helv", fontsize=11) + 2
    return (x, ORIGIN[1] - 11, x + 16, ORIGIN[1] + 3)


ROWS = [{"page": 0, "core": CORE, "origin": list(ORIGIN), "bbox": [72, 89, 330, 103]}]


def test_a_translation_running_under_a_field_in_its_line_is_named(tmp_path):
    field = _gap_field("Learned by completing ")
    source = _pdf(tmp_path / "source.pdf", "Learned by completing         online sessions.",
                  field=field)
    placed = _pdf(tmp_path / "placed.pdf", SPANISH, field=field)
    assert field_overlap.covering_cores(source, placed, ROWS) == ({CORE}, 0)


def test_a_field_whose_own_label_sits_inside_it_is_not_checked(tmp_path):
    # The source prints letters inside this field's rectangle itself, as a labelled
    # cell does; our letters there are the label's translation, not a covered word.
    field = (70, 88, 200, 104)
    source = _pdf(tmp_path / "source.pdf", "Learned by completing online sessions.", field=field)
    placed = _pdf(tmp_path / "placed.pdf", SPANISH, field=field)
    assert field_overlap.covering_cores(source, placed, ROWS) == (set(), 0)


def test_a_letter_that_only_grazes_a_field_is_not_under_it():
    # AO 240 prints "No" 0.2 pt inside its checkbox's rectangle.
    field = fitz.Rect(429.4, 520.1, 442.3, 533.4)
    assert not field_overlap._under(fitz.Rect(442.1, 523.7, 450.1, 531.4), field)
    assert field_overlap._under(fitz.Rect(435.0, 524.0, 442.6, 531.0), field)
