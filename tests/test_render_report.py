import os
from openreview_mcp.server import render_meta_review_report


def _minimal_synthesis(**overrides):
    base = {
        "venue_id": "Test.cc/2026/Conference",
        "venue_display_name": "Test Conference 2026",
        "papers": [
            {
                "ref": "paper_1_Test_Paper",
                "number": 1,
                "title": "Test Paper",
                "strengths": [
                    {
                        "claim": "Good results",
                        "who": ["xuxJ"],
                        "quotes": [{"reviewer": "xuxJ", "text": "results are strong"}],
                    }
                ],
                "weaknesses": [
                    {
                        "claim": "Weak baseline",
                        "who": ["aB3q"],
                        "quotes": [{"reviewer": "aB3q", "text": "missing comparison"}],
                    }
                ],
                "minor": [
                    {
                        "claim": "typo",
                        "who": ["aB3q"],
                        "quotes": [{"reviewer": "aB3q", "text": "line 42 has a typo"}],
                    }
                ],
                "disagreements": [
                    {
                        "claim": "disagree on novelty",
                        "who": ["xuxJ", "aB3q"],
                        "quotes": [
                            {"reviewer": "xuxJ", "text": "novel"},
                            {"reviewer": "aB3q", "text": "not novel"},
                        ],
                    }
                ],
                "isolatedPoints": [
                    {
                        "reviewer": "R9mZ",
                        "claim": "clarify training",
                        "quotes": [{"reviewer": "R9mZ", "text": "unclear schedule"}],
                    }
                ],
                "actionItems": {
                    "critical": [
                        {
                            "claim": "add missing baseline",
                            "who": ["xuxJ"],
                            "quotes": [{"reviewer": "xuxJ", "text": "must fix now"}],
                        }
                    ],
                    "medium": [
                        {
                            "claim": "improve writing",
                            "who": ["aB3q"],
                            "quotes": [{"reviewer": "aB3q", "text": "unclear prose"}],
                        }
                    ],
                    "low": [
                        {
                            "claim": "fix typo",
                            "who": ["aB3q"],
                            "quotes": [{"reviewer": "aB3q", "text": "typo on page 3"}],
                        }
                    ],
                },
            }
        ],
        "excludedWithdrawn": [{"id": "w1", "number": 2, "title": "Withdrawn Paper"}],
        "verificationLog": {
            "total_checked": 5,
            "passed": 5,
            "failed": 0,
            "failures": [],
        },
    }
    base.update(overrides)
    return base


def test_render_writes_both_files_with_expected_structure(tmp_path):
    synthesis = _minimal_synthesis()
    res = render_meta_review_report(synthesis, str(tmp_path))

    assert os.path.isfile(res["markdown_path"])
    assert os.path.isfile(res["html_path"])

    with open(res["markdown_path"]) as f:
        md_text = f.read()
    with open(res["html_path"]) as f:
        html_text = f.read()

    assert "## Paper paper_1_Test_Paper: Test Paper" in md_text
    assert "**Critical**" in md_text
    assert "**Medium**" in md_text
    assert "**Low**" in md_text

    assert 'class="point strength"' in html_text
    assert 'class="point weak"' in html_text
    assert 'class="point conflict"' in html_text
    assert 'class="action-tier tier-critical"' in html_text
    assert 'class="action-tier tier-medium"' in html_text
    assert 'class="action-tier tier-low"' in html_text
    assert "xuxJ" in html_text
    assert "aB3q" in html_text
    assert "R9mZ" in html_text

    assert res["paper_count"] == 1
    assert res["excluded_withdrawn_count"] == 1
    assert res["counts"] == {
        "strengths": 1,
        "weaknesses": 1,
        "minor": 1,
        "disagreements": 1,
        "isolated_points": 1,
        "action_items": {"critical": 1, "medium": 1, "low": 1},
    }


def test_render_escapes_html_special_chars_in_quotes(tmp_path):
    synthesis = _minimal_synthesis()
    synthesis["papers"][0]["strengths"][0]["quotes"][0]["text"] = (
        "the bound f(x) < g(x) & h(x) > 0 is loose"
    )

    res = render_meta_review_report(synthesis, str(tmp_path))
    with open(res["html_path"]) as f:
        html_text = f.read()

    assert "f(x) &lt; g(x) &amp; h(x) &gt; 0" in html_text
    # No raw '<' immediately followed by a letter from that quote (would indicate
    # a broken/interpreted tag rather than escaped text).
    assert "<g(x)" not in html_text
    assert "<h(x)" not in html_text


def test_render_embedded_markdown_survives_script_close_sequence(tmp_path):
    synthesis = _minimal_synthesis()
    synthesis["papers"][0]["actionItems"]["critical"][0]["claim"] = (
        "Note: a reviewer literally wrote </script> in their comment"
    )

    res = render_meta_review_report(synthesis, str(tmp_path))
    with open(res["markdown_path"]) as f:
        md_text = f.read()
    with open(res["html_path"]) as f:
        html_text = f.read()

    # The standalone .md file keeps the literal text untouched.
    assert "</script> in their comment" in md_text

    # The HTML document must still be well-formed: exactly the two real
    # <script>...</script> pairs (md-source + the visible JS logic), not a third
    # accidental one from the embedded claim prematurely closing the tag.
    assert html_text.count("<script") == html_text.count("</script>") == 2

    # Reversing the fixup on the embedded payload must reconstruct the original
    # Markdown exactly -- proving round-trip fidelity for "Copy as Markdown".
    import re

    script_match = re.search(
        r'<script type="text/plain" id="md-source">(.*?)</script>', html_text, re.S
    )
    embedded = script_match.group(1)
    reconstructed = re.sub(r"(?i)<\\/script", "</script", embedded)
    assert reconstructed == md_text
