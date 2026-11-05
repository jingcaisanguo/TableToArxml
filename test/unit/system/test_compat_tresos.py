"""EB-generated list entries get the missing ``tresos`` layer at the right position."""

from __future__ import annotations

from pathlib import Path

import pytest

from table_to_arxml.system.tresos_list import normalize_generated_entry

BASE = Path("D:/jenkins/ZCUD.Debug/proj/build/ZCUD.Debug")


@pytest.mark.parametrize("entry, expected", [
    ("D:/jenkins/ZCUD.Debug/proj/build/ZCUD.Debug/output/generated/Rte.arxml",
     "D:/jenkins/ZCUD.Debug/proj/build/ZCUD.Debug/tresos/output/generated/Rte.arxml"),
    ("D:\\jenkins\\ZCUD.Debug\\proj\\build\\ZCUD.Debug\\output\\Rte.arxml",
     "D:/jenkins/ZCUD.Debug/proj/build/ZCUD.Debug/tresos/output/Rte.arxml"),
    ("D:/jenkins/ZCUD.Debug/proj/build/ZCUD.Debug/tresos/output/Rte.arxml",
     "D:/jenkins/ZCUD.Debug/proj/build/ZCUD.Debug/tresos/output/Rte.arxml"),
    ("../ZCUD.Debug/proj/build/ZCUD.Debug/output/Rte.arxml",
     "../ZCUD.Debug/proj/build/ZCUD.Debug/tresos/output/Rte.arxml"),
    ("app/swc/Lamp.arxml", "app/swc/Lamp.arxml"),
])
def test_tresos_layer_is_inserted_after_the_output_base_directory(entry, expected):
    assert normalize_generated_entry(entry, BASE) == expected
