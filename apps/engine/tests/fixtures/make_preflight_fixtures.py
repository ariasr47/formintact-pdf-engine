"""Builds the preflight-declines fixtures into tests/fixtures/preflight/ (SPEC section 7).

Run from apps/engine:

    uv run --frozen --with "pyhanko>=0.25,<1" python tests/fixtures/make_preflight_fixtures.py

Options:
  --signer {pyhanko,pikepdf}  default pyhanko. pikepdf needs no --with; it writes the same signature
                              dictionaries, structurally signed but cryptographically invalid.
  --rtl-font-dir PATH         a directory holding NotoSansArabic-Regular.ttf and
                              NotoSansHebrew-Regular.ttf, used when MuPDF's built-in faces fall
                              short.

The pyHanko key is a throwaway made in a temporary directory; it is never printed, written to
the repo or kept. Every file gets <stem>.PROVENANCE.json beside it. Fixtures are committed
binaries; this script is their record.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unicodedata
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import fitz
import pikepdf

REPO = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent / "preflight"
SIZE_LIMIT = 150_000
RTL_MIN_SHARE = 0.50
SCRIPT_REL = "apps/engine/tests/fixtures/make_preflight_fixtures.py"

# Hebrew, then Arabic ranges (SPEC section 7).
RTL_RANGES = (
    (0x0590, 0x05FF), (0xFB1D, 0xFB4F), (0x0600, 0x06FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF),
)

ARABIC_LINES = [
    "استمارة طلب خدمة عامة",
    "يرجى كتابة الاسم الكامل والعنوان ورقم الهاتف.",
    "التوقيع والتاريخ",
    "Form PD-1 (Rev. 10/2026)",
]
HEBREW_LINES = [
    "טופס בקשה לשירות ציבורי",
    "נא לכתוב את השם המלא, הכתובת ומספר הטלפון.",
    "חתימה ותאריך",
    "Form PD-1 (Rev. 10/2026)",
]
SIGNED_LINES = [
    "Signed test document",
    "Applicant name and address",
    "Date of signature: 10/14/2026",
]
XFA_LINES = [
    "Please wait...",
    "If this message is not eventually replaced by the proper contents of the document,",
    "your PDF viewer may not be able to display this type of document.",
]
XDP = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<xdp:xdp xmlns:xdp="http://ns.adobe.com/xdp/">\n'
    b'<template xmlns="http://www.xfa.org/schema/xfa-template/3.3/">\n'
    b'<subform name="form1" layout="tb" locale="en_US"><pageSet><pageArea name="Page1">'
    b'<contentArea x="0.25in" y="0.25in" w="8in" h="10.5in"/></pageArea></pageSet>'
    b'<subform><field name="Name"><ui><textEdit/></ui></field></subform></subform>\n'
    b'</template>\n</xdp:xdp>\n'
)


def _version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def _base(lines: list[str]) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for i, line in enumerate(lines):
        page.insert_text(fitz.Point(72, 96 + 18 * i), line, fontsize=11)
    doc.set_metadata({})
    return doc.tobytes(garbage=3, deflate=True)


def _save(pdf: pikepdf.Pdf) -> bytes:
    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True)
    return buf.getvalue()


def _sig_widget(pdf: pikepdf.Pdf, page: pikepdf.Page, value=None):
    widget = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget, FT=pikepdf.Name.Sig,
        T=pikepdf.String("Signature1"), Rect=pikepdf.Array([72, 600, 300, 660]), F=4, P=page.obj))
    if value is not None:
        widget.V = value
    page.obj.Annots = pikepdf.Array([widget])
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([widget]))
    return widget


def build_empty_sig_field() -> bytes:
    pdf = pikepdf.open(io.BytesIO(_base(["Unsigned test document", "Sign here:"])))
    _sig_widget(pdf, pdf.pages[0])
    return _save(pdf)


def build_xfa(needs_rendering: bool) -> bytes:
    pdf = pikepdf.open(io.BytesIO(_base(XFA_LINES)))
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([]), XFA=pdf.make_stream(XDP))
    if needs_rendering:
        pdf.Root.NeedsRendering = True
    return _save(pdf)


def build_signed_pikepdf(certify: bool) -> bytes:
    pdf = pikepdf.open(io.BytesIO(_base(SIGNED_LINES)))
    sig = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Sig, Filter=pikepdf.Name("/Adobe.PPKLite"),
        SubFilter=pikepdf.Name("/adbe.pkcs7.detached"), ByteRange=pikepdf.Array([0, 0, 0, 0]),
        Contents=pikepdf.String(b"\x00" * 256)))
    if certify:
        sig.Reference = pikepdf.Array([pikepdf.Dictionary(
            Type=pikepdf.Name.SigRef, TransformMethod=pikepdf.Name.DocMDP,
            TransformParams=pikepdf.Dictionary(
                Type=pikepdf.Name.TransformParams, P=2, V=pikepdf.Name("/1.2")))])
        pdf.Root.Perms = pikepdf.Dictionary(DocMDP=sig)
    _sig_widget(pdf, pdf.pages[0], value=sig)
    return _save(pdf)


def build_signed_pyhanko(certify: bool) -> bytes:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers

    base = _base(SIGNED_LINES)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    who = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "FormIntact preflight fixture (test only)")])
    cert = (x509.CertificateBuilder().subject_name(who).issuer_name(who)
            .public_key(key.public_key())
            .serial_number(1).not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
            .not_valid_after(datetime(2036, 1, 1, tzinfo=UTC)).sign(key, hashes.SHA256()))
    with tempfile.TemporaryDirectory() as tmp:
        key_pem, cert_pem = Path(tmp) / "key.pem", Path(tmp) / "cert.pem"
        key_pem.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        cert_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        signer = signers.SimpleSigner.load(key_file=str(key_pem), cert_file=str(cert_pem))
        out = io.BytesIO()
        signers.sign_pdf(IncrementalPdfFileWriter(io.BytesIO(base)),
                         signers.PdfSignatureMetadata(field_name="Signature1", certify=certify),
                         signer=signer, output=out)
    return out.getvalue()


def build_rtl(lines: list[str], face_name: str | None, font_dir: Path | None) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    html = "".join(f'<p dir="rtl" style="font-size:16px">{line}</p>' for line in lines)
    kwargs = {}
    if font_dir is not None:
        kwargs["css"] = (
            f"@font-face {{font-family: r; src: url({face_name});}} p {{font-family: r;}}")
        kwargs["archive"] = fitz.Archive(str(font_dir))
    page.insert_htmlbox(fitz.Rect(72, 72, 540, 720), html, **kwargs)
    doc.subset_fonts()
    doc.set_metadata({})
    return doc.tobytes(garbage=3, deflate=True)


def rtl_share(data: bytes) -> tuple[int, int]:
    page = fitz.open(stream=data, filetype="pdf")[0]
    letters = rtl = 0
    for block in page.get_text("blocks"):
        for char in block[4]:
            if unicodedata.category(char).startswith("L"):
                letters += 1
                rtl += any(low <= ord(char) <= high for low, high in RTL_RANGES)
    return letters, rtl


def git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True,
                          text=True).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--signer", choices=["pyhanko", "pikepdf"], default="pyhanko")
    parser.add_argument("--rtl-font-dir", type=Path, default=None)
    args = parser.parse_args()
    font_dir = args.rtl_font_dir.resolve() if args.rtl_font_dir else None

    sign = build_signed_pyhanko if args.signer == "pyhanko" else build_signed_pikepdf
    structural = "structurally signed, cryptographically invalid"
    arabic_face = "NotoSansArabic-Regular.ttf" if font_dir else None
    hebrew_face = "NotoSansHebrew-Regular.ttf" if font_dir else None
    signed_branch = "pyhanko" if args.signer == "pyhanko" else "pikepdf-structural"
    pikepdf_mode = args.signer == "pikepdf"
    signed_note = structural if pikepdf_mode else "pyHanko detached signature, throwaway key"
    certified_note = structural if pikepdf_mode else "pyHanko certification (/Perms /DocMDP)"
    # name -> (bytes, signer, branch, note)
    files: dict[str, tuple[bytes, str | None, str, str | None]] = {
        "signed": (sign(False), args.signer, signed_branch, signed_note),
        "certified": (sign(True), args.signer, signed_branch, certified_note),
        "empty_sig_field": (build_empty_sig_field(), None, "pikepdf", "/Sig field with no /V"),
        "dynamic_xfa": (build_xfa(True), None, "pikepdf",
                        "/XFA with /NeedsRendering true, no widgets"),
        "xfa_no_widgets": (build_xfa(False), None, "pikepdf", "/XFA, empty /Fields, no widgets"),
        "rtl_arabic": (build_rtl(ARABIC_LINES, arabic_face, font_dir), None,
                       arabic_face or "builtin-face", None),
        "rtl_hebrew": (build_rtl(HEBREW_LINES, hebrew_face, font_dir), None,
                       hebrew_face or "builtin-face", None),
    }

    problems = []
    notes = {}
    for stem, (data, _signer, _branch, note) in files.items():
        if len(data) >= SIZE_LIMIT:
            problems.append(f"{stem}.pdf is {len(data)} bytes, over the {SIZE_LIMIT} limit")
        if stem.startswith("rtl_"):
            letters, rtl = rtl_share(data)
            share = rtl / letters if letters else 0.0
            notes[stem] = f"rtl share {rtl}/{letters} = {share:.3f}"
            if share < RTL_MIN_SHARE:
                problems.append(
                    f"{stem}.pdf right-to-left share is {share:.3f} ({rtl}/{letters}), "
                    f"below {RTL_MIN_SHARE}; "
                    "rerun with --rtl-font-dir pointing at NotoSansArabic-Regular.ttf and "
                    "NotoSansHebrew-Regular.ttf")
        else:
            notes[stem] = note or ""
    if problems:
        sys.exit("preflight fixtures failed:\n  " + "\n  ".join(problems))

    OUT.mkdir(parents=True, exist_ok=True)
    head = git_head()
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    tools = {name: _version(dist) for name, dist in
             (("pymupdf", "PyMuPDF"), ("pikepdf", "pikepdf"), ("pyhanko", "pyhanko"),
              ("cryptography", "cryptography"))}
    for stem, (data, signer_name, branch, _note) in files.items():
        (OUT / f"{stem}.pdf").write_bytes(data)
        record = {
            "file": f"apps/engine/tests/fixtures/preflight/{stem}.pdf",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "producer": SCRIPT_REL,
            "signer": signer_name,
            "git_head": head,
            "created_utc": created,
            "branch": branch,
            "note": notes[stem],
            "tools": tools,
        }
        with open(OUT / f"{stem}.PROVENANCE.json", "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        print(f"{stem}.pdf  {len(data)} bytes  {notes[stem]}")


if __name__ == "__main__":
    main()
