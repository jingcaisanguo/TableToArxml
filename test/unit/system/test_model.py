"""模型查询：组合实例链、包含目标组合的 System、总线类型和组件名映射。"""

from __future__ import annotations

import pytest

from system_fixtures import write_model
from table_to_arxml.system.arxml_model import MAX_COMPOSITION_DEPTH, ArxmlModel, bus_module
from table_to_arxml.system.component_map import build_component_type_map


@pytest.fixture(scope="module")
def model(tmp_path_factory) -> ArxmlModel:
    directory = tmp_path_factory.mktemp("model")
    write_model(directory)
    return ArxmlModel.load([directory / "model.arxml"])


def test_composition_chains(model):
    assert model.composition_chains("/Pkg/Root", "/Pkg/Top") == [("/Pkg/Root/TopProto",)]
    assert model.composition_chains("/Pkg/Top", "/Pkg/Top") == [()]
    assert model.composition_chains("/Pkg/Top", "/Pkg/Root") == []


def test_cyclic_composition_stops_at_depth_limit(model):
    assert MAX_COMPOSITION_DEPTH == 16
    assert model.composition_chains("/Pkg/Loop", "/Pkg/Top") == []


def test_systems_containing(model):
    assert [system.getFullName() for system in model.systems_containing("/Pkg/Top")] == ["/Pkg/Sys"]
    assert model.systems_containing("/Pkg/Loop") == []


def test_bus_module():
    class CanCluster:
        pass

    class LinCluster:
        pass

    class FlexrayCluster:
        pass

    assert [bus_module(cluster()) for cluster in (CanCluster, LinCluster, FlexrayCluster)] == ["CAN", "LIN", ""]


def test_component_map_prefers_target_instances_and_reports_ambiguity(model):
    mapping = build_component_type_map(model, "/Pkg/Top", {}, "its")
    assert mapping.resolve("/Pkg/Top/A") == ("A", "/Pkg/A")
    assert mapping.resolve("/Pkg/A") == ("A", "/Pkg/A")
    assert mapping.resolve("B") == ("B", "/Pkg/B")
    assert mapping.resolve("/Other/A") == ("itsA", "/Other/A")
    assert mapping.resolve("Unknown") is None
    with pytest.raises(ValueError, match="Ambiguous component 'A'"):
        mapping.resolve("A")


def test_component_aliases(model):
    mapping = build_component_type_map(model, "/Pkg/Top", {"Alpha": "/Other/A"}, "its")
    assert mapping.resolve("Alpha") == ("itsAlpha", "/Other/A")
    named = build_component_type_map(model, "/Pkg/Top", {"Beta": {"type": "/Other/A", "prototype": "Beta1"}}, "its")
    assert named.resolve("Beta") == ("Beta1", "/Other/A")
    with pytest.raises(ValueError, match="already has instance 'itsAlpha'"):
        build_component_type_map(
            model, "/Pkg/Top", {"Alpha": "/Other/A", "Beta": {"type": "/Other/A", "prototype": "Beta1"}}, "its")
    with pytest.raises(ValueError, match="componentAliases.Beta"):
        build_component_type_map(model, "/Pkg/Top", {"Beta": "/Missing/Type"}, "its")
    with pytest.raises(ValueError, match="path separators"):
        build_component_type_map(model, "/Pkg/Top", {"a/b": "/Other/A"}, "its")
