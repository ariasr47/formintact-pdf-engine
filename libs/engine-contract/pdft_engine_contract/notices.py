"""Page-notice codes and detail strings shared by the engine's per-page messages and the app's
notice composers, so 'one page's notice equals its per-page message' holds by construction."""
PAGE_RETURNED_UNTRANSLATED = "page_returned_untranslated"                 # pipeline.py:729
PAGE_TEXT_LEFT_IN_SOURCE_LANGUAGE = "page_text_left_in_source_language"   # pipeline.py:737
SOURCE_TEXT_DETAIL_ONE = (                                                # pipeline.py:755-758
    " — the translation could not be placed on that page, so we left the original "
    "wording in place. Check it in the downloaded PDF before you rely on it."
)
SOURCE_TEXT_DETAIL_MANY = (                                               # pipeline.py:759-763
    " — the translations could not be placed on those pages, so we left the "
    "original wording in place. Check them in the downloaded PDF before you rely "
    "on them."
)
NOTICE_DETAIL_ONE = (                                                     # pipeline.py:1594-1597
    " — we couldn't put a usable translation on it, so it still reads as the "
    "original. It still needs translating another way."
)
NOTICE_DETAIL_MANY = (                                                    # pipeline.py:1598-1601
    " — we couldn't put a usable translation on them, so they still read as the "
    "original. They still need translating another way."
)
