from __future__ import annotations

import polars as pl
import pytest

from dataplat.checks import (
    Rate,
    Verdict,
    check,
    dirty_expr,
    measure_dirt,
    rate_expr,
    shape_class,
    verify_derivation,
    verify_repair,
)
from dataplat.checks import livefire as lf
from dataplat.checks.profile import null_rate_at_most, rows_at_least, share_at_least


@pytest.mark.parametrize(
    ("value", "shape"),
    [
        ("+255700000000.0", "+9{12}.9"),
        ("BD_TELCO_X", "A{2}_A{5}_A"),
        ("a1b2", "a9a9"),
        (None, "<null>"),
        ("", "<empty>"),
    ],
)
def test_shape_class(value: object, shape: str) -> None:
    assert shape_class(value) == shape


@check(origin="test")
def no_plus_in_phone() -> pl.Expr:
    """phone never starts with '+'."""
    return ~pl.col("phone").str.starts_with("+")


def test_check_verdicts_and_null_handling() -> None:
    clean = pl.DataFrame({"phone": ["2547001", "2547002"]})
    assert no_plus_in_phone(clean).verdict is Verdict.PASS
    bad = no_plus_in_phone(pl.DataFrame({"phone": ["+2547001", "2547002"]}))
    assert bad.verdict is Verdict.FAIL
    assert bad.measured["failing"] == 1
    assert bad.measured["shapes[phone]"] == {"+9{7}": 1}
    assert "2547001" not in str(bad)  # never a raw value in the evidence
    # A null result is a failure unless the check handles nulls itself.
    assert no_plus_in_phone(pl.DataFrame({"phone": [None]}, schema={"phone": pl.String})).verdict is Verdict.FAIL
    assert no_plus_in_phone(pl.DataFrame(schema={"phone": pl.String})).verdict is Verdict.INCONCLUSIVE


def test_duplicate_check_names_across_modules_are_rejected() -> None:
    def other() -> pl.Expr:
        return pl.lit(True)

    other.__module__ = "somewhere.else"
    other.__name__ = "no_plus_in_phone"
    with pytest.raises(ValueError, match="registered twice"):
        check(other)


def test_livefire_catches_and_records() -> None:
    clean = pl.DataFrame({"phone": ["2547001", "2547002"]})
    ev = lf.assert_catches(no_plus_in_phone, clean, lf.add_leading_plus("phone"))
    assert ev.verdict is Verdict.FAIL
    assert "no_plus_in_phone" in lf.fired()


def test_livefire_rejects_noop_mutations_and_blind_checks() -> None:
    clean = pl.DataFrame({"phone": ["2547001"], "n": [1]})
    with pytest.raises(AssertionError, match="unchanged"):
        lf.assert_catches(no_plus_in_phone, clean, lf.null_to_zero("n"))  # no nulls -> no-op
    with pytest.raises(AssertionError, match="did NOT catch"):
        lf.assert_catches(no_plus_in_phone, clean, lf.float_artifact("phone"))


def test_standard_mutations() -> None:
    df = pl.DataFrame({"a": [1.0, None, 3.0], "s": ["x", "y", "z"]})
    assert lf.null_to_zero("a")(df)["a"].to_list() == [1.0, 0.0, 3.0]
    assert lf.nudge("a")(df)["a"][0] == pytest.approx(1.000001)
    assert lf.drop_row(1)(df).height == 2
    assert lf.duplicate_row(0)(df).height == 4
    assert lf.prefix("s", "+")(df)["s"][0] == "+x"
    assert lf.suffix("s", ".0", index=2)(df)["s"][2] == "z.0"
    assert lf.set_value("s", "q", 1)(df)["s"].to_list() == ["x", "q", "z"]


def _clean_phone(e: pl.Expr) -> pl.Expr:
    return e.str.strip_prefix("+").str.replace(r"\.0$", "")


def test_dirtiness_is_transform_changes_value() -> None:
    df = pl.DataFrame({"p": ["+2547", "2547", "2547.0", None]})
    assert df.select(dirty_expr("p", _clean_phone))["p"].to_list() == [True, False, True, False]
    ev = measure_dirt(df, "p", _clean_phone)
    assert ev.measured["dirty"] == 2
    assert ev.measured["shapes_before"] == {"+9{4}": 1, "9{4}.9": 1}


def test_witness_derivation_and_repair() -> None:
    pre = pl.DataFrame({"raw": ["+2547", "2548"], "clean": ["+2547", "2548"], "other": [1, 2]})
    assert verify_derivation(pre, derived="clean", witness="raw", derive=_clean_phone).verdict is Verdict.FAIL
    post = pre.with_columns(_clean_phone(pl.col("raw")).alias("clean"))
    ev = verify_repair(pre, post, repaired="clean", witness="raw", derive=_clean_phone)
    assert ev.verdict is Verdict.PASS and ev.measured["changed"] == 1
    tampered = post.with_columns(pl.lit(9).alias("other"))
    assert verify_repair(pre, tampered, repaired="clean", witness="raw", derive=_clean_phone).verdict is Verdict.FAIL
    lost_row = post.head(1)
    assert verify_repair(pre, lost_row, repaired="clean", witness="raw", derive=_clean_phone).verdict is Verdict.FAIL


def test_profile_gates_have_sample_floors() -> None:
    small = pl.DataFrame({"x": [None, 1]})
    assert null_rate_at_most("x", 0.1)(small).verdict is Verdict.INCONCLUSIVE
    big = pl.DataFrame({"x": [1] * 40 + [None] * 10})
    assert null_rate_at_most("x", 0.25)(big).verdict is Verdict.PASS
    assert null_rate_at_most("x", 0.1)(big).verdict is Verdict.FAIL
    assert share_at_least("x=1", pl.col("x") == 1, 0.9)(big).verdict is Verdict.FAIL
    assert rows_at_least(100)(big).verdict is Verdict.FAIL


def test_rate_never_fakes_zero_and_aggregates_by_parts() -> None:
    assert Rate(0, 0).value is None
    assert (Rate(1, 2) + Rate(9, 10)).value == pytest.approx(10 / 12)  # not mean(0.5, 0.9) = 0.7
    df = pl.DataFrame({"n": [1, 0], "d": [2, 0]})
    assert df.select(rate_expr(pl.col("n"), pl.col("d")))["n"].to_list() == [0.5, None]
