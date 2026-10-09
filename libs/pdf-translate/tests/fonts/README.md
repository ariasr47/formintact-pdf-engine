# Test fonts

Empty on purpose. The shaping tests (Arabic joining, Devanagari conjuncts)
need a font that actually carries GSUB tables for those scripts; without one
they SKIP, which is how the shaping gates went unexercised on Linux.

Populate it with:

```bash
python3 tools/fetch_test_fonts.py
```

That downloads twenty SIL Open Font License 1.1 font files. The original eleven are: Noto Sans, Noto Naskh
Arabic, Noto Sans Hebrew, Noto Sans Devanagari, (added 15 September
2026, because `_SHAPING_RANGES` already routed these scripts through the
Story engine while every test for them skipped) Noto Sans Thai, Khmer,
Tamil, Bengali and Myanmar, and (added 16 September 2026, for the CJK
gates) the google/fonts variable TTFs of Noto Sans JP and Noto Sans SC,
saved as `NotoSansJP-VF.ttf` and `NotoSansSC-VF.ttf`. No single Noto face
carries both Arabic and Hebrew (Arial does, which is why these tests only
ever passed on macOS and Windows), so each test asks for a font covering
the characters it actually needs. Nothing here is committed: the repository
carries no font binaries, and the licence stays with the upstream project.

`tests/test_pipeline.py` prefers anything in this directory over the host's
own fonts, so a populated `tests/fonts/` makes the suite behave the same on
every machine.


## Typography fixtures added 20 September 2026

Seven static Latin faces add Noto Sans bold/italic/bold-italic and all four
Noto Serif roles. Two genuine variable glyf faces add Noto Serif JP and SC.
Together with the original Sans JP/SC variables, instantiate 400/700 using
`prepare_font --instance wght=400` (or 700) with the typography class/role
selectors. The instancer applies actual variation outlines and STAT-derived
style metadata. Do not relabel a regular face as bold or manufacture italics.
CJK italic/bold-italic positive acceptance is unmeasured: these fixtures do not
provide genuine italic CJK faces, and requesting that role must refuse.

The nine URLs in the fetcher answered 200 and their downloaded bytes were
inspected. Each font's name records 13/14 identify SIL Open Font License 1.1
and link to its licence. Primary source pins:

- [Noto static builds](https://github.com/notofonts/notofonts.github.io/tree/d9b11dadb3d9d5cb562d753b0cb59b19ce805afb/fonts)
- [Google Fonts OFL directories](https://github.com/google/fonts/tree/e44c4b011a820c2cbe2fd2cfa8052037d7edb571/ofl)
- [OFL 1.1 text](https://openfontlicense.org/open-font-license-official-text/)

These are observed SHA-256 values, not hashes inferred from filenames. Existing
fixture URLs are not silently repinned by this feature. The fetcher's `--check`
checks presence; it does not claim to revalidate content hashes.

| Font | SHA-256 |
| --- | --- |
| NotoSans-Bold.ttf | `1df075a380fc7cb898acf64c1f7b3b4dd780de3caa860178bf929de35817a913` |
| NotoSans-Italic.ttf | `467e3f89eeca4108bb8710a2b9e0cf2281ac56d5b0609211a83776d0505eecb5` |
| NotoSans-BoldItalic.ttf | `1b602a9d6353be42c91df097a4857b69fa2696f26703d7a33b54a15d87c2622c` |
| NotoSerif-Regular.ttf | `19e72cd8d595fae5bd74a5206f5d938512e1183d4fed7abb1ec1be1d7efa5f88` |
| NotoSerif-Bold.ttf | `96656aa5cec8f1d6fd0e804c1fad397e1a1cfa082e6642124e0bda68cd8363ce` |
| NotoSerif-Italic.ttf | `749e80e313ef711f9373c6cce17c72297ef05490b3dcda7967d1d5d90bf1183f` |
| NotoSerif-BoldItalic.ttf | `c710c5b9cf354ae46e7a10472a08019b28220fafeb5887a482a76856f8f6fc0b` |
| NotoSerifJP-VF.ttf | `2fd527ba12b6a44ec30d796d633360da0aeba6c5d4af1304ce12bb4dc15a7dfc` |
| NotoSerifSC-VF.ttf | `050080d9255a86808f2945bffac582b31ef32bc36411ce29563b4961670c66f9` |
