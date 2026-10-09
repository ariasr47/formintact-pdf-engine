#!/usr/bin/env python3
"""Can a wrapped B1 occurrence keep the SOURCE first baseline? Measure it.

The 19 Sep recovery probe found all six successful candidates shifted the
first baseline (-2.481 .. +0.502 pt) and recorded that as blocker 1: "baseline
anchoring is not supplied by an authored box alone". It did not establish that
anchoring is impossible. This probe tests one specific mechanism.

Mechanism under test. `insert_htmlbox` fills a rect from its top, so the first
baseline lands wherever the authored box top happens to be. The library already
anchors a SINGLE Story line elsewhere (`place_story_line`: top = baseline -
SHAPED_BASELINE * fs, SHAPED_BASELINE = 0.8, measured at line-height 1). The
merge path uses line-height lh instead, so the predicted first-baseline offset
below a rect top is fs * (0.8 + (lh - 1) / 2) -- the 0.8 ascent plus CSS
half-leading.

Two parts:

  MODEL      drive insert_htmlbox directly over a grid of font size, line
             height, line count and font, with a rect large enough that the
             engine does not shrink, and compare the measured offset against
             the closed form.

  ANCHORED   re-run the six real recovery cases through the real library
             (`run_retypeset` with a one-member merge), then translate the
             authored box vertically by the measured residual, keeping its
             WIDTH AND HEIGHT identical so line breaking and engine scale
             cannot change, and measure the baseline delta again.

The library is not modified. The corrective translation is something a caller
can compute today, which is the point: if it lands, blocker 1 is a box-authoring
question, not a layout impossibility.

Measures only. Eight constructed/seed occurrences are a stress sample and say
nothing about a customer recovery rate.

Run:  py -3 dev/probes/b1_anchor_probe.py --work runs/b1-anchor
"""
import argparse
import copy
import html
import json
import re
import sys
from pathlib import Path, PurePath

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pdf-translate"))
import pymupdf
import pdf_translate as lib
from pdf_translate.retypeset import SHAPED_BASELINE, merge_css_size

FONTS = ROOT / "pdf-translate/tests/fonts"
CORPUS = ROOT / "pdf-translate/corpus"
# A one-member merge has no second baseline to measure leading from, so
# retypeset falls back to this. Read from the source, not assumed.
FALLBACK_LH = 1.18


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def font_path(kind):
    return FONTS / ("NotoSansJP-VF-wght400.ttf" if kind == "cjk"
                    else "NotoSans-Regular.ttf")


def spans(page):
    return [s for b in page.get_text("dict")["blocks"]
            for line in b.get("lines", []) for s in line["spans"]]


def predicted_offset(fs, lh):
    """First baseline below the rect top: ascent plus CSS half-leading."""
    return fs * (SHAPED_BASELINE + (lh - 1) / 2)


# ---------------------------------------------------------------- MODEL


def story_css(kind):
    """The library's own CSS, so the model measures the real line box.

    retypeset quotes a full posix path and resets the body box; omitting
    either changes the measured offset, and omitting the quoted path makes
    MuPDF silently substitute a default face.
    """
    url = '"' + PurePath(font_path(kind)).as_posix() + '"'
    return (f"@font-face {{font-family: tr; src: url({url});}}"
            "body {font-family: tr; margin: 0; padding: 0;}")


def model_point(fs, lh, lines, kind):
    """Place one div in a deliberately roomy rect; report the real offset."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    arch = pymupdf.Archive(str(FONTS))
    css = story_css(kind)
    word = "あいうえ" if kind == "cjk" else "Wrapping sample text"
    body = " ".join([word] * (6 * lines))
    top = 40.0
    # Tall and wide enough that the engine has no reason to shrink; the
    # returned scale is asserted below, so roominess is checked, not assumed.
    rect = pymupdf.Rect(30, top, 370, top + (lines + 4) * fs * lh + 40)
    h = (f'<div style="font-family:tr; font-size:{fs:.1f}px; '
         f'line-height:{lh}; color:#000000; text-align:left;">{body}</div>')
    _, scale = page.insert_htmlbox(rect, h, css=css, archive=arch, scale_low=0)
    placed = spans(page)
    origins = sorted({round(s["origin"][1], 4) for s in placed})
    result = {
        "font_size_px": fs, "line_height": lh, "requested_lines": lines,
        "font": kind, "engine_scale": round(scale, 6),
        "unshrunk": abs(scale - 1.0) < 1e-9,
        "measured_lines": len(origins),
        "first_baseline_offset_pt": (round(origins[0] - top, 4) if origins else None),
        "predicted_offset_pt": round(predicted_offset(fs, lh), 4),
        "measured_leading_pt": (round(origins[1] - origins[0], 4)
                                if len(origins) > 1 else None),
        "predicted_leading_pt": round(fs * lh, 4),
    }
    if result["first_baseline_offset_pt"] is not None:
        result["offset_residual_pt"] = round(
            result["first_baseline_offset_pt"] - result["predicted_offset_pt"], 4)
    if result["measured_leading_pt"] is not None:
        result["leading_residual_pt"] = round(
            result["measured_leading_pt"] - result["predicted_leading_pt"], 4)
    doc.close()
    return result


def run_model():
    points = []
    for kind in ("latin", "cjk"):
        for fs in (8.0, 9.0, 10.0, 11.0, 12.0, 14.0, 18.0):
            for lh in (1.0, 1.18, 1.3, 1.5):
                for lines in (1, 3):
                    points.append(model_point(fs, lh, lines, kind))
    return points


# ------------------------------------------------------------- ANCHORED


def setup(work, source):
    work.mkdir(parents=True)
    ext = lib.run_extract(str(source), str(work))
    lib.run_strip(str(source), str(work / "stripped.pdf"))
    return Path(ext.segments_path), read(ext.segments_path)


def make_source(path, full=False):
    doc = pymupdf.open()
    p = doc.new_page(width=300, height=300)
    p.insert_text((40, 70), "First page unchanged", fontsize=12)
    p = doc.new_page(width=300, height=300)
    y = 260 if full else 70
    p.insert_text((40, y), "Selected note", fontsize=12)
    w = pymupdf.Widget()
    w.field_name = "neighbor"
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    wy = y if full else 235
    w.rect = pymupdf.Rect(140, wy - 14, 270, wy + 6)
    p.add_widget(w)
    doc.save(path)
    doc.close()


def build_merge(folder, tag, stripped, segments, mapping):
    sub = folder / tag
    sub.mkdir(parents=True)
    mp = sub / "translations.json"
    out = sub / "out.pdf"
    write(mp, mapping)
    try:
        r = lib.run_retypeset(str(stripped), str(segments), str(mp), str(out))
        return {"status": "built", "scaled": list(r.scaled)}, out
    except lib.PdfTranslateError as exc:
        return {"status": "refused", "error": exc.to_dict()}, out


def observe(out, pno, box, target, source_origin):
    """First-line origin, line count and containment for the placed merge."""
    rect = pymupdf.Rect(box)
    probe_rect = rect + (-.001, -.001, .001, .001)
    with pymupdf.open(out) as doc:
        page = doc[pno]
        placed = [s for s in spans(page)
                  if probe_rect.contains(pymupdf.Point(s["origin"]))]
        text_ok = re.sub(r"\s+", "", target) in re.sub(r"\s+", "", page.get_text())
        origins = sorted({round(s["origin"][1], 4) for s in placed})
        first = [s for s in placed if round(s["origin"][1], 4) == origins[0]] if origins else []
        sizes = sorted({round(s["size"], 6) for s in placed})
        return {
            "target_on_page": text_ok,
            "lines_in_box": len(origins),
            "line_baselines": origins,
            "first_origin": (list(first[0]["origin"]) if first else None),
            "first_baseline_delta_pt": (round(origins[0] - source_origin[1], 4)
                                        if origins else None),
            "x_delta_pt": (round(first[0]["origin"][0] - source_origin[0], 4)
                           if first else None),
            "output_sizes_pt": sizes,
            "all_spans_in_box": bool(placed) and all(
                rect.contains(pymupdf.Rect(s["bbox"])) for s in placed),
            "pages": len(doc),
        }


def anchored_case(case, manifest, work):
    folder = work / "anchored" / case["id"]
    if case["source"].startswith("@"):
        source = work / (case["id"] + "-source.pdf")
        source.parent.mkdir(parents=True, exist_ok=True)
        make_source(source, full=case["source"] == "@constructed-full")
    else:
        source = CORPUS / case["source"]
    segments, data = setup(folder, source)
    picked = [s for s in data["segments"]
              if s["page"] == case["page"] and s["text"].strip() == case["text"]]
    if len(picked) != 1:
        raise ValueError(f"ambiguous selector {case['id']}: {len(picked)}")
    picked = picked[0]
    target = manifest[case["payload"] + "_payload"]
    base_map = {
        "lang": "ja" if case["payload"] == "cjk" else "fr",
        "fonts": {r: str(font_path(case["payload"]))
                  for r in ("regular", "bold", "italic", "bold_italic")},
        "translations": {c["text"]: c["text"]
                         for c in read(folder / "to_translate.json")["cores"]},
        "merges": [],
    }
    base_map["translations"][picked["core"]] = None
    stripped = folder / "stripped.pdf"

    def with_box(box):
        m = copy.deepcopy(base_map)
        m["merges"] = [{"page": case["page"], "lines": [picked["text"]],
                        "html": html.escape(target), "box": list(box),
                        "align": "left"}]
        return m

    css_size, fit_ratio = merge_css_size(picked["size"])
    record = {
        "case": case["id"], "page": case["page"],
        "source_origin": picked["origin"], "source_size_pt": picked["size"],
        "merge_css_size_px": css_size, "fit_ratio": fit_ratio,
        "line_height_used": FALLBACK_LH,
        "closed_form_offset_pt": round(predicted_offset(css_size, FALLBACK_LH), 4),
        "authored_box": case["box"],
    }

    # Pass 1: the authored box exactly as the 19 Sep probe used it.
    as_is, out = build_merge(folder, "as-authored", stripped, segments,
                             with_box(case["box"]))
    record["as_authored"] = as_is
    if as_is["status"] != "built":
        record["anchored"] = {"status": "not attempted: as-authored refused"}
        write(folder / "result.json", record)
        return record
    obs = observe(out, case["page"], case["box"], target, picked["origin"])
    record["as_authored_observation"] = obs
    record["measured_offset_pt"] = (
        round(obs["line_baselines"][0] - case["box"][1], 4)
        if obs["line_baselines"] else None)
    if record["measured_offset_pt"] is not None:
        record["offset_residual_pt"] = round(
            record["measured_offset_pt"] - record["closed_form_offset_pt"], 4)

    # Pass 2: translate the SAME box vertically by the observed residual.
    # Width and height are unchanged, so line breaking and engine scale are
    # unchanged; only the anchor moves.
    shift = obs["first_baseline_delta_pt"]
    x0, y0, x1, y1 = case["box"]
    moved = [x0, round(y0 - shift, 4), x1, round(y1 - shift, 4)]
    record["shift_applied_pt"] = round(-shift, 4)
    record["anchored_box"] = moved
    anc, out2 = build_merge(folder, "anchored", stripped, segments, with_box(moved))
    record["anchored"] = anc
    if anc["status"] == "built":
        obs2 = observe(out2, case["page"], moved, target, picked["origin"])
        record["anchored_observation"] = obs2
        record["anchored_delta_pt"] = obs2["first_baseline_delta_pt"]
        record["baseline_preserved_to_0_01pt"] = (
            obs2["first_baseline_delta_pt"] is not None
            and abs(obs2["first_baseline_delta_pt"]) <= .01)
        record["line_count_unchanged"] = obs["lines_in_box"] == obs2["lines_in_box"]
        record["sizes_unchanged"] = obs["output_sizes_pt"] == obs2["output_sizes_pt"]
        # A closed-form box needs no trial build; does it land as well?
        predicted_top = round(picked["origin"][1] - record["closed_form_offset_pt"], 4)
        cf_box = [x0, predicted_top, x1, round(predicted_top + (y1 - y0), 4)]
        record["closed_form_box"] = cf_box
        cf, out3 = build_merge(folder, "closed-form", stripped, segments,
                               with_box(cf_box))
        record["closed_form"] = cf
        if cf["status"] == "built":
            obs3 = observe(out3, case["page"], cf_box, target, picked["origin"])
            record["closed_form_observation"] = obs3
            record["closed_form_delta_pt"] = obs3["first_baseline_delta_pt"]
    write(folder / "result.json", record)
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", type=Path, required=True)
    args = ap.parse_args()
    work = args.work.resolve()
    if work.exists():
        raise SystemExit("Use a fresh --work directory; the probe never overwrites prior evidence.")
    work.mkdir(parents=True)
    manifest_path = Path(__file__).with_name("b1_cases.json")
    manifest = read(manifest_path)
    report = {
        "runtime": {"python": sys.version.split()[0], "pymupdf": pymupdf.version,
                    "library": lib.__version__, "library_file": lib.__file__},
        "mechanism": {"SHAPED_BASELINE": SHAPED_BASELINE,
                      "line_height_for_one_member_merge": FALLBACK_LH,
                      "closed_form": "fs * (SHAPED_BASELINE + (lh - 1) / 2)"},
        "scope": manifest["scope"],
    }
    print("model grid ...", flush=True)
    report["model"] = run_model()
    write(work / "results.json", report)
    report["anchored"] = []
    for case in manifest["cases"]:
        r = anchored_case(case, manifest, work)
        report["anchored"].append(r)
        print(f"{case['id']}: as-authored {r['as_authored']['status']}"
              f" delta={r.get('as_authored_observation', {}).get('first_baseline_delta_pt')}"
              f" -> anchored delta={r.get('anchored_delta_pt')}", flush=True)
        write(work / "results.json", report)

    usable = [p for p in report["model"] if p["unshrunk"]]
    offs = [abs(p["offset_residual_pt"]) for p in usable
            if p.get("offset_residual_pt") is not None]
    leads = [abs(p["leading_residual_pt"]) for p in usable
             if p.get("leading_residual_pt") is not None]
    built = [r for r in report["anchored"] if r["as_authored"]["status"] == "built"]
    anchored_ok = [r for r in built if r.get("baseline_preserved_to_0_01pt")]
    cf_ok = [r for r in built
             if r.get("closed_form_delta_pt") is not None
             and abs(r["closed_form_delta_pt"]) <= .01]
    report["summary"] = {
        "model_points": len(report["model"]),
        "model_points_unshrunk": len(usable),
        "max_offset_residual_pt": max(offs) if offs else None,
        "max_leading_residual_pt": max(leads) if leads else None,
        "cases_built_as_authored": len(built),
        "cases_anchored_to_0_01pt": len(anchored_ok),
        "cases_closed_form_to_0_01pt": len(cf_ok),
        "shifts_applied_pt": {r["case"]: r.get("shift_applied_pt") for r in built},
        "warning": "Stress sample. Not a customer recovery rate.",
    }
    write(work / "results.json", report)
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
