import json
import os
from openreview_mcp.server import verify_quotes_in_batch


def _write_dump(tmp_path, ref, submission_extra=None, replies=None):
    data = {
        "submission": {"id": ref, "content": submission_extra or {}},
        "replies": replies or [],
    }
    path = os.path.join(tmp_path, f"{ref}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def test_verify_all_pass(tmp_path):
    _write_dump(
        tmp_path,
        "paper_1",
        replies=[
            {
                "id": "r1",
                "content": {
                    "summary": {"value": "the results are consistently strong"}
                },
            }
        ],
    )
    _write_dump(
        tmp_path,
        "paper_2",
        replies=[
            {
                "id": "r1",
                "content": {"weaknesses": {"value": "the ablation is thin"}},
            }
        ],
    )

    quotes = [
        {"ref": "paper_1", "quote": "the results are consistently strong"},
        {"ref": "paper_2", "quote": "the ablation is thin"},
        {"ref": "paper_1.json", "quote": "results are consistently"},
    ]

    res = verify_quotes_in_batch(quotes, str(tmp_path))

    assert res["total_checked"] == 3
    assert res["passed"] == 3
    assert res["failed"] == 0
    assert res["all_passed"] is True
    assert res["failures"] == []


def test_verify_flags_fabricated_quote(tmp_path):
    _write_dump(
        tmp_path,
        "paper_1",
        replies=[
            {
                "id": "r1",
                "content": {
                    "weaknesses": {
                        "value": "the ablation in Table 3 omits the strongest baseline"
                    }
                },
            }
        ],
    )

    quotes = [
        {
            "ref": "paper_1",
            "quote": "the ablation in Table 3 omits the strongest baseline",
        },
        # Subtly different wording -- not a true substring.
        {"ref": "paper_1", "quote": "the ablation in Table 3 omits key baselines"},
    ]

    res = verify_quotes_in_batch(quotes, str(tmp_path))

    assert res["failed"] == 1
    assert res["all_passed"] is False
    assert len(res["failures"]) == 1
    failure = res["failures"][0]
    assert failure["index"] == 1
    assert failure["ref"] == "paper_1"
    assert failure["reason"] == "quote_not_found"


def test_verify_missing_file_does_not_crash_batch(tmp_path):
    _write_dump(
        tmp_path,
        "paper_1",
        replies=[{"id": "r1", "content": {"summary": {"value": "solid paper"}}}],
    )

    quotes = [
        {"ref": "paper_999", "quote": "does not exist"},
        {"ref": "paper_1", "quote": "solid paper"},
    ]

    res = verify_quotes_in_batch(quotes, str(tmp_path))

    assert res["total_checked"] == 2
    assert res["passed"] == 1
    assert res["failed"] == 1
    assert res["failures"][0]["reason"] == "file_not_found"


def test_verify_matches_decoded_json_not_raw_escaped_bytes(tmp_path):
    # A review whose text contains a literal newline and a literal double-quote --
    # json.dump will escape both (\n and \") in the file on disk. A naive raw-byte
    # substring search against the file would fail to find the unescaped quote;
    # the JSON-aware search must find it because it operates on the decoded value.
    tricky_text = 'first line\nsecond line with a "quoted" word'
    _write_dump(
        tmp_path,
        "paper_1",
        replies=[{"id": "r1", "content": {"summary": {"value": tricky_text}}}],
    )

    # Sanity: confirm the raw file bytes really do contain escape sequences, not
    # the literal newline/quote characters, so this test is exercising real escaping.
    with open(os.path.join(tmp_path, "paper_1.json"), "r") as f:
        raw_bytes = f.read()
    assert "\\n" in raw_bytes
    assert '\\"quoted\\"' in raw_bytes

    quotes = [{"ref": "paper_1", "quote": tricky_text}]
    res = verify_quotes_in_batch(quotes, str(tmp_path))

    assert res["all_passed"] is True
