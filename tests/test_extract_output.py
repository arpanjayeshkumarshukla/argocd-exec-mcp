from argocd_exec_mcp.session import extract_output


def test_basic_completion():
    start, end = "__START_abc123__", "__DONE_abc123_"
    raw = (
        f"printf '{start}\\n'; echo hi; printf '\\n{end}%s__\\n' \"$?\"\n"  # echo of input
        f"{start}\n"  # start marker's real output
        "hi\n\n"
        f"{end}0__\n"  # end sentinel's real output
    )
    assert extract_output(raw, start, end) == "hi"


def test_takes_the_last_occurrence_of_start_marker_not_the_echoed_one():
    # The echo of the input line contains the literal start marker text too
    # (it's what we typed) — using the *first* occurrence would wrongly
    # treat the echo itself as the body boundary.
    start, end = "__START_deadbeef__", "__DONE_deadbeef_"
    raw = f"{start}\nreal output\n{end}42__\n"
    assert extract_output(raw, start, end) == "real output"


def test_tolerant_of_a_split_or_garbled_echo_since_it_never_looks_at_the_echo():
    # Simulates the failure mode this design replaced: the echoed command
    # line is corrupted/split by a terminal wrap boundary. The old
    # echo-based design depended on finding an intact echo; this design
    # never looks at the echo at all, only at its own short markers.
    start, end = "__START_x__", "__DONE_x_"
    garbled_echo = "some\ngarbled partial ec\x00ho of the input line"
    raw = f"{garbled_echo}\n{start}\noutput after a garbled echo\n{end}0__\n"
    assert extract_output(raw, start, end) == "output after a garbled echo"


def test_no_start_marker_found_falls_back_to_start_of_stream():
    start, end = "__START_y__", "__DONE_y_"
    raw = f"just output\n{end}0__\n"
    assert extract_output(raw, start, end) == "just output"


def test_no_end_sentinel_found_returns_everything_after_start():
    start, end = "__START_z__", "__DONE_z_"
    raw = f"{start}\nno sentinel here at all"
    assert extract_output(raw, start, end) == "no sentinel here at all"


def test_multiline_output_preserved():
    start, end = "__START_m__", "__DONE_m_"
    raw = f"{start}\nline one\nline two\nline three\n{end}0__\n"
    assert extract_output(raw, start, end) == "line one\nline two\nline three"
