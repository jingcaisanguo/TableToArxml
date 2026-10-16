"""AUTOSAR and generated naming boundaries."""

import pytest

from table_to_arxml.common.names import autosar_path, c_identifier, generated_name, short_name


def test_short_name_length_boundary():
    assert short_name("A" * 128) == "A" * 128
    with pytest.raises(ValueError, match="at most 128"):
        short_name("A" * 129)


def test_generated_names_are_stable_and_distinguishable():
    first = generated_name("Prefix_" + "A" * 128)
    second = generated_name("Prefix_" + "A" * 127 + "B")
    assert len(first) == len(second) == 128
    assert first != second
    assert first == generated_name("Prefix_" + "A" * 128)


def test_paths_and_c_symbols_have_distinct_rules():
    assert autosar_path("/Pkg/Types/T", minimum_parts=2) == "/Pkg/Types/T"
    assert c_identifier("_run") == "_run"
    with pytest.raises(ValueError):
        autosar_path("/Pkg//T")
    with pytest.raises(ValueError):
        c_identifier("run-now")
