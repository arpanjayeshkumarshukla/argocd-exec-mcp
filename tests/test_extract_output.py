from argocd_exec_mcp.session import extract_output


def test_basic_completion():
    marker = "__DONE_abc123_"
    raw = (
        f"echo hi; printf '\\n{marker}%s__\\n' \"$?\"\n"
        "hi\n\n"
        f"{marker}0__\n"
    )
    assert extract_output(raw, marker) == "hi"


def test_literal_percent_s_in_echo_is_not_mistaken_for_the_real_sentinel():
    # The terminal echoes the raw bytes we sent, unexpanded — literal `%s`,
    # not a digit. A naive substring match on `marker` alone would treat
    # that echo as the completion signal and return nothing.
    marker = "__DONE_deadbeef_"
    raw = f"{marker}%s__\nreal output\n{marker}42__\n"
    assert extract_output(raw, marker) == "real output"


def test_no_echo_found_falls_back_to_start_of_stream():
    marker = "__DONE_x_"
    raw = f"just output\n{marker}0__\n"
    assert extract_output(raw, marker) == "just output"


def test_no_sentinel_found_returns_everything_as_is():
    marker = "__DONE_y_"
    raw = "no sentinel here at all"
    assert extract_output(raw, marker) == "no sentinel here at all"


def test_multiline_output_preserved():
    marker = "__DONE_z_"
    raw = f"{marker}%s__\nline one\nline two\nline three\n{marker}0__\n"
    assert extract_output(raw, marker) == "line one\nline two\nline three"
