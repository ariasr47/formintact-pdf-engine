from pdft_engine_contract.naming import artifact_matches


def test_artifact_matches_the_expected_page():
    assert artifact_matches("page", "page-2.pdf.0123456789abcdef.tmp", 2) is True


def test_artifact_matches_rejects_a_different_page():
    assert artifact_matches("page", "page-2.pdf.0123456789abcdef.tmp", 3) is False


def test_artifact_matches_rejects_a_traversal_name():
    assert artifact_matches("page", "../page-2.pdf.0123456789abcdef.tmp", 2) is False


def test_artifact_matches_rejects_a_zero_padded_page_number():
    assert artifact_matches("page", "page-02.pdf.0123456789abcdef.tmp", 2) is False
