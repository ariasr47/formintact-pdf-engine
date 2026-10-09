#!/usr/bin/env python3
"""Measure B1 candidates using the existing library, without changing its layout.

The seed corpus has source PDFs, not translation pairs. Its deliberately fixed
diagnostic payloads are a stress sample, never a customer recovery estimate.
Authored rectangles are frozen in b1_cases.json; no obstacle discovery occurs.
One-member merges exercise the existing reflow route. We measure actual origin,
font size, text presence, page/field parity and bounds, not just build success.

Run with the repo venv: python dev/probes/b1_recovery_probe.py --work runs/b1-probe
Use a fresh work directory. The two optional historical jobs are read only.
"""
import argparse
import copy
import hashlib
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pdf-translate"))
import pymupdf
import pdf_translate as lib
from pdf_translate.verify import kinsoku_report

FONTS = ROOT / "pdf-translate/tests/fonts"
CORPUS = ROOT / "pdf-translate/corpus"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def font_path(kind):
    return FONTS / ("NotoSansJP-VF-wght400.ttf" if kind == "cjk"
                    else "NotoSans-Regular.ttf")


def spans(page):
    return [s for b in page.get_text("dict")["blocks"]
            for line in b.get("lines", []) for s in line["spans"]]


def snapshot(path):
    with pymupdf.open(path) as doc:
        return {
            "pages": len(doc),
            "rects": [list(p.rect) for p in doc],
            "fields": [[p.number, w.field_name, w.field_type, list(w.rect)]
                       for p in doc for w in p.widgets()],
        }


def compact(text):
    return re.sub(r"\s+", "", text)


def isolated_ink(out, pno, target):
    """Rasterize just the final merge stream on a temporary in-memory copy.

    Span bboxes include unused font ascent: they are not painted ink bounds.
    Assert the stream's text before using alpha pixels as independent bounds.
    The output PDF is not saved or changed. Pixel rounding is explicit.
    """
    zoom = 3
    with pymupdf.open(out) as doc:
        page = doc[pno]
        page.set_contents(page.get_contents()[-1])
        if compact(page.get_text()) != compact(target):
            raise ValueError("final stream is not exactly the selected target")
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True, annots=False)
        raw = pix.samples
        rows = []
        for y in range(pix.height):
            alpha = raw[y * pix.stride + pix.n - 1:(y + 1) * pix.stride:pix.n]
            if alpha.strip(b"\x00"):
                rows.append((y, len(alpha) - len(alpha.lstrip(b"\x00")),
                             len(alpha.rstrip(b"\x00"))))
        if not rows:
            return None
        return [min(r[1] for r in rows) / zoom, rows[0][0] / zoom,
                max(r[2] for r in rows) / zoom, (rows[-1][0] + 1) / zoom]


def build(work, source, segments, mapping, mode):
    folder = work / mode
    folder.mkdir()
    mp = folder / "translations.json"
    out = folder / "out.pdf"
    write(mp, mapping)
    try:
        r = lib.run_retypeset(str(work / "stripped.pdf"), str(segments),
                             str(mp), str(out))
        result = {"status": "built", "scaled": list(r.scaled)}
    except lib.PdfTranslateError as exc:
        result = {"status": "refused", "error": exc.to_dict(),
                  "output_exists": out.exists()}
    if result["status"] == "built":
        result["shape"] = snapshot(out)
    write(folder / "build.json", result)
    return result, out


def make_source(path, full=False, repeated=False):
    """Construct controls here; no imports from another repository."""
    doc = pymupdf.open()
    p = doc.new_page(width=300, height=300)
    if repeated:
        p.insert_text((40, 70), "Same label", fontsize=12)
        p.insert_text((40, 190), "Same label", fontsize=12)
    else:
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


def setup(work, source):
    work.mkdir(parents=True)
    ext = lib.run_extract(str(source), str(work))
    lib.run_strip(str(source), str(work / "stripped.pdf"))
    return Path(ext.segments_path), read(ext.segments_path)


def inspect_candidate(out, source, selected, target, box, mapping, segments, kind):
    with pymupdf.open(out) as doc:
        page = doc[selected["page"]]
        rect = pymupdf.Rect(box)
        # 0.001pt covers float roundoff (e.g. 49.99998 vs x0=50), not a layout allowance.
        selection_rect = rect + (-.001, -.001, .001, .001)
        placed = [s for s in spans(page) if selection_rect.contains(pymupdf.Point(s["origin"]))]
        # This sample authorizes no other text inside the rectangle. Also read
        # the complete page, so clipped text cannot masquerade as recovery.
        target_present = compact(target) in compact(page.get_text())
        placed_text = "".join(s["text"] for s in placed)
        inside_text_present = compact(target) in compact(placed_text)
        contained = all(rect.contains(pymupdf.Rect(s["bbox"])) for s in placed)
        actual_sizes = sorted({round(s["size"], 6) for s in placed})
        origins = [list(s["origin"]) for s in placed]
        delta = ([round(origins[0][i] - selected["origin"][i], 6) for i in (0, 1)]
                 if origins else None)
        scales = [round(x / selected["size"], 6) for x in actual_sizes]
        font_names = sorted({s["font"] for s in placed})
        drawn_color = sorted({s["color"] for s in placed})
        embedded = [list(f[:4]) for f in page.get_fonts(full=True)
                    if "NotoSans" in f[3].replace(" ", "")]
        embedded_hashes = {str(f[0]): hashlib.sha256(doc.extract_font(f[0])[3]).hexdigest()
                           for f in embedded}
        expected_hash = sha(font_path(kind))
        # Assert both a named drawing face and the actual embedded program.
        font_attested = bool(placed and embedded and all("NotoSans" in f for f in font_names)
                             and expected_hash in embedded_hashes.values())
        ink = isolated_ink(out, selected["page"], target)
        # At 216dpi pixel footprints may straddle the boundary by one pixel.
        raster_rect = rect + (-1 / 3, -1 / 3, 1 / 3, 1 / 3)
        k_lines, k_status, k_findings = kinsoku_report(doc)
        record = {
            "target_present_on_source_page": target_present,
            "target_present_in_box": inside_text_present,
            "span_bounds_contained": bool(placed) and contained,
            "ink_bounds_pt_at_216dpi": ink,
            "ink_bounds_contained_at_216dpi": ink is not None and raster_rect.contains(pymupdf.Rect(ink)),
            "ink_tolerance_pt": 1 / 3,
            "ink_intersects_widget": ink is not None and any(pymupdf.Rect(ink).intersects(w.rect) for w in page.widgets()),
            "first_origin_delta_pt": delta,
            "baseline_preserved_to_0_01pt": delta is not None and all(abs(x) <= .01 for x in delta),
            "output_sizes_pt": actual_sizes,
            "effective_scales": scales,
            "line_origins": origins,
            "fonts": font_names,
            "embedded_font_records": embedded,
            "embedded_font_sha256": embedded_hashes,
            "expected_font_sha256": expected_hash,
            "font_attested": font_attested,
            "colors": drawn_color,
            "source_color": selected.get("color", 0),
            "color_preserved": drawn_color == [selected.get("color", 0)],
            "kinsoku": {"status": k_status, "lines": k_lines,
                        "findings": len(k_findings)},
        }
        page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(out.with_name("render.png"))
        # Review overlay only; the evaluated out.pdf stays unmodified.
        page.draw_rect(rect, color=(0, .6, .5), width=.7, dashes="[3 2]")
        y = selected["origin"][1]
        page.draw_line((box[0], y), (box[2], y), color=(.8, .2, .1), width=.4)
        page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(out.with_name("review-overlay.png"))
    verdict = lib.run_verify(str(source), str(out), translations=str(mapping),
                             source_words_from=str(segments),
                             reference_fonts=str(FONTS))
    write(out.with_name("verify.json"), verdict.to_dict())
    record["verify_exit_code"] = verdict.exit_code
    record["verify_nonpass"] = [g.to_dict() for g in verdict.gates
                                 if g.status not in ("PASS", "SKIP")]
    return record


def stress_case(case, manifest, work):
    folder = work / "stress" / case["id"]
    if case["source"].startswith("@"):
        source = work / (case["id"] + "-source.pdf")
        make_source(source, full=case["source"] == "@constructed-full")
    else:
        source = CORPUS / case["source"]
    segments, data = setup(folder, source)
    selected = [s for s in data["segments"]
                if s["page"] == case["page"] and s["text"].strip() == case["text"]]
    if len(selected) != 1:
        raise ValueError(f"ambiguous fixture selector: {case['id']}: {len(selected)}")
    selected = selected[0]
    target = manifest[case["payload"] + "_payload"]
    mapping = {"lang": "ja" if case["payload"] == "cjk" else "fr",
               "fonts": {r: str(font_path(case["payload"]))
                         for r in ("regular", "bold", "italic", "bold_italic")},
               "translations": {c["text"]: c["text"] for c in read(folder / "to_translate.json")["cores"]},
               "merges": []}
    mapping["translations"][selected["core"]] = target
    baseline, _ = build(folder, source, segments, mapping, "single-line")
    wrapped_map = copy.deepcopy(mapping)
    wrapped_map["translations"][selected["core"]] = None
    wrapped_map["merges"] = [{"page": case["page"], "lines": [selected["text"]],
                              "html": html.escape(target), "box": case["box"],
                              "align": "left"}]
    wrapped, out = build(folder, source, segments, wrapped_map, "authored-box")
    record = {"case": case, "source_sha256": sha(source), "source_shape": snapshot(source),
              "selected": selected, "baseline": baseline, "wrapped": wrapped}
    if wrapped["status"] == "built":
        observation = inspect_candidate(out, source, selected, target, case["box"],
                                        out.with_name("translations.json"), segments, case["payload"])
        observation["shape_preserved"] = snapshot(source) == snapshot(out)
        observation["actual_shrink_unreported"] = (
            any(s < .999 for s in observation["effective_scales"]) and not wrapped["scaled"])
        record["observation"] = observation
    else:
        record["observation"] = None
    write(folder / "result.json", record)
    return record


def historical_job(relative, work):
    old = ROOT / relative
    if not (old / "original.pdf").exists():
        return {"job": relative, "status": "unavailable"}
    folder = work / "historical" / old.parent.name / old.name
    source = old / "original.pdf"
    segments, _ = setup(folder, source)
    mapping = read(old / "translations.json")
    for role, path in mapping["fonts"].items():
        if path:
            mapping["fonts"][role] = str((old / path).resolve())
    result, _ = build(folder, source, segments, mapping, "as-authored")
    return {"job": relative, "source_sha256": sha(source),
            "mapping_sha256": sha(old / "translations.json"),
            "note": "Saved final mapping with existing merges; not an early refused draft.",
            "result": result}


def census(work):
    result = []
    for name, expected in sorted(read(CORPUS / "verdicts.json").items()):
        out = work / "census" / Path(name).stem
        out.mkdir(parents=True)
        try:
            r = lib.run_extract(str(CORPUS / name), str(out))
            count = len(read(r.segments_path)["segments"])
            status = ("refuse+OCR" if r.unextractable_pages else
                      "refuse+ocr-layer" if r.invisible_text_pages else
                      "empty" if not count else "eligible")
            result.append({"source": name, "expected": expected,
                           "status": status, "segments": count,
                           "unextractable_pages": list(r.unextractable_pages),
                           "invisible_text_pages": list(r.invisible_text_pages)})
        except lib.PdfTranslateError as exc:
            result.append({"source": name, "expected": expected,
                           "status": "refused", "error": exc.to_dict()})
    return result


def duplicate_control(work, payload):
    source = work / "duplicate-source.pdf"
    make_source(source, repeated=True)
    folder = work / "duplicate-control"
    segments, _ = setup(folder, source)
    mapping = {"lang": "fr", "fonts": {"regular": str(font_path("latin")),
                                          "bold": str(font_path("latin"))},
               "translations": {"Same label": "Short label"},
               "merges": [{"page": 0, "lines": ["Same label"],
                           "html": html.escape(payload), "box": [40, 180, 270, 290]}]}
    result, out = build(folder, source, segments, mapping, "second-occurrence-request")
    observation = None
    if result["status"] == "built":
        with pymupdf.open(out) as doc:
            observation = {"short_label_origins": [list(s["origin"]) for s in spans(doc[0])
                                                    if "Short label" in s["text"]],
                           "expected_untouched_origin": [40, 70],
                           "text": doc[0].get_text()}
            doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).save(out.with_name("render.png"))
    return {"intent": "Wrap only the second equal label; keep the first short.",
            "result": result, "observation": observation}


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
    report = {"runtime": {"python": sys.version, "pymupdf": pymupdf.version,
                          "library": lib.__version__},
              "scope": manifest["scope"], "manifest_sha256": sha(manifest_path),
              "font_sha256": {p.name: sha(p) for p in (font_path("latin"), font_path("cjk"))}}
    report["census"] = census(work)
    report["historical"] = [historical_job(p, work) for p in
                             ("runs/gpt-6-2026-09-04", "runs/fresh-canary-2026-09-05/job")]
    report["stress"] = []
    for case in manifest["cases"]:
        r = stress_case(case, manifest, work)
        report["stress"].append(r)
        print(f"{case['id']}: {r['baseline']['status']} -> {r['wrapped']['status']}", flush=True)
        write(work / "results.json", report)
    report["duplicate_control"] = duplicate_control(work, manifest["latin_payload"])
    refused = [r for r in report["stress"] if r["baseline"]["status"] == "refused"]
    built = [r for r in refused if r["wrapped"]["status"] == "built"]
    bounded = [r for r in built if all(r["observation"][k] for k in
               ("shape_preserved", "target_present_on_source_page", "target_present_in_box",
                "ink_bounds_contained_at_216dpi", "font_attested", "color_preserved"))
               and not r["observation"]["ink_intersects_widget"]
               and r["observation"]["effective_scales"]
               and min(r["observation"]["effective_scales"]) >= .7]
    anchored = [r for r in bounded if r["observation"]["baseline_preserved_to_0_01pt"]]
    report["summary"] = {"stress_cases": len(report["stress"]),
                         "baseline_refused": len(refused), "box_builds": len(built),
                         "bounded_text_recoveries": len(bounded),
                         "also_preserve_baseline_to_0_01pt": len(anchored),
                         "warning": "Stress sample only. These counts do not estimate production recovery."}
    write(work / "results.json", report)
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
