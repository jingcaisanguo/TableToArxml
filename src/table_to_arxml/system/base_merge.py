"""Append table-generated objects to a complete base document."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

from armodel import AUTOSAR

from ..common.names import autosar_path
from .values import class_destination

LOGGER = logging.getLogger(__name__)
NAMESPACE = "http://autosar.org/schema/r4.0"
NS = {"ar": NAMESPACE}
SIGNAL_MAPPINGS = {"SENDER-RECEIVER-TO-SIGNAL-MAPPING", "SENDER-RECEIVER-TO-SIGNAL-GROUP-MAPPING"}
MERGE_CHILDREN = {
    "AUTOSAR": {"AR-PACKAGES"},
    "AR-PACKAGE": {"AR-PACKAGES", "ELEMENTS"},
    "COMPOSITION-SW-COMPONENT-TYPE": {"PORTS", "COMPONENTS", "CONNECTORS"},
    "SYSTEM": {"MAPPINGS"},
    "SYSTEM-MAPPING": {"DATA-MAPPINGS", "SW-MAPPINGS", "SW-IMPL-MAPPINGS"},
    "PORT-INTERFACE-MAPPING-SET": {"PORT-INTERFACE-MAPPINGS"},
    "SWC-TO-ECU-MAPPING": {"COMPONENT-IREFS"},
}
CONTAINERS = {
    "AR-PACKAGES", "ELEMENTS", "PORTS", "COMPONENTS", "CONNECTORS", "MAPPINGS",
    "DATA-MAPPINGS", "SW-MAPPINGS", "SW-IMPL-MAPPINGS", "PORT-INTERFACE-MAPPINGS", "COMPONENT-IREFS",
}
BEFORE = {
    "AR-PACKAGES": (),
    "ELEMENTS": ("AR-PACKAGES",),
    "PORTS": ("COMPONENTS", "CONNECTORS", "DATA-TYPE-MAPPING-REFS"),
    "COMPONENTS": ("CONNECTORS", "DATA-TYPE-MAPPING-REFS"),
    "CONNECTORS": ("DATA-TYPE-MAPPING-REFS",),
    "MAPPINGS": ("ROOT-SOFTWARE-COMPOSITIONS", "SYSTEM-VERSION"),
    "DATA-MAPPINGS": ("SW-IMPL-MAPPINGS", "SW-MAPPINGS"),
    "SW-IMPL-MAPPINGS": ("SW-MAPPINGS",),
    "COMPONENT-IREFS": ("ECU-INSTANCE-REF",),
}


def _tag(name: str) -> str:
    return f"{{{NAMESPACE}}}{name}"


def _name(element: ET.Element) -> str:
    return element.findtext("ar:SHORT-NAME", default="", namespaces=NS)


def _index(root: ET.Element) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}

    def visit(element: ET.Element, parent: str) -> None:
        name = _name(element)
        path = f"{parent}/{name}" if name else parent
        if name:
            if path in result:
                raise ValueError(f"Duplicate AUTOSAR path in merge document: {path}")
            result[path] = element
        for child in element:
            visit(child, path)

    visit(root, "")
    return result


def _parse(path: Path) -> ET.ElementTree:
    try:
        tree = ET.parse(path, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True)))
    except ET.ParseError as error:
        raise ValueError(f"Cannot parse ARXML for base merge ({path}): {error}") from error
    if tree.getroot().tag != _tag("AUTOSAR"):
        raise ValueError(f"Base ARXML must use the AUTOSAR r4.0 namespace: {path}")
    _index(tree.getroot())
    return tree


def prepare_base_model(base_file: Path, model_file: Path, document: AUTOSAR) -> tuple[Path, frozenset[str]]:
    """Expose base definitions to resolution without treating its mappings as external splits."""

    tree = _parse(base_file)
    base = _index(tree.getroot())
    for path, item in base.items():
        if item.tag in {_tag("SYSTEM-MAPPING"), _tag("PORT-INTERFACE-MAPPING-SET")} and document.find(path) is not None:
            raise ValueError(f"Cannot split existing {item.tag.rsplit('}', 1)[-1]} while merging base: {path}")
    for owner in tree.getroot().iter():
        if owner.tag == _tag("COMPOSITION-SW-COMPONENT-TYPE"):
            container = owner.find("ar:CONNECTORS", NS)
        elif owner.tag == _tag("SYSTEM"):
            container = owner.find("ar:MAPPINGS", NS)
        else:
            continue
        if container is not None:
            owner.remove(container)
    model_file.parent.mkdir(parents=True, exist_ok=True)
    tree.write(model_file, encoding="utf-8", xml_declaration=True)
    return model_file, frozenset(
        path for path, item in base.items() if item.tag == _tag("PORT-INTERFACE-MAPPING-SET"))


def _references(element: ET.Element) -> tuple:
    return tuple(
        (item.tag, tuple(sorted(item.attrib.items())), (item.text or "").strip())
        for item in element.iter()
        if isinstance(item.tag, str) and not len(item) and item.tag.endswith(("-REF", "-TREF"))
    )


def _compatible(existing: ET.Element, request: ET.Element, *, ignore_name: bool = False) -> bool:
    """Generated fields must agree; base-only metadata is not an update request."""

    if existing.tag != request.tag or (existing.text or "").strip() != (request.text or "").strip():
        return False
    if any(existing.get(key) != value for key, value in request.attrib.items()):
        return False
    remaining = list(existing)
    for child in request:
        if ignore_name and child.tag == _tag("SHORT-NAME"):
            continue
        match = next((item for item in remaining if _compatible(item, child)), None)
        if match is None:
            return False
        remaining.remove(match)
    return True


def _duplicate(items: list[ET.Element], item: ET.Element, context: str) -> bool:
    name = _name(item)
    references = _references(item)
    for existing in items:
        same_name = bool(name) and name == _name(existing)
        same_refs = item.tag == existing.tag and references == _references(existing)
        if same_name or same_refs:
            if not same_refs or not _compatible(existing, item, ignore_name=True):
                raise ValueError(f"Conflicting base connection or mapping at {context}/{name}")
            LOGGER.info("Reuse duplicate base connection or mapping: %s/%s", context, name)
            return True
        other = _name(existing)
        if name and other and name.casefold() == other.casefold():
            LOGGER.warning("%s: base name %s differs only in case from %s", context, other, name)
    return False


def _insert(parent: ET.Element, child: ET.Element) -> ET.Element:
    retained = deepcopy(child)
    before = {_tag(name) for name in BEFORE.get(child.tag.rsplit("}", 1)[-1], ())}
    position = next((index for index, sibling in enumerate(parent) if sibling.tag in before), len(parent))
    parent.insert(position, retained)
    return retained


def _merge(target: ET.Element, source: ET.Element, path: str, added: list[ET.Element]) -> None:
    kind = source.tag.rsplit("}", 1)[-1]
    allowed = MERGE_CHILDREN.get(kind)
    for child in source:
        child_kind = child.tag.rsplit("}", 1)[-1]
        if allowed is not None and child_kind not in allowed:
            continue
        name = _name(child)
        context = f"{path}/{name}" if name else path
        if child_kind.endswith("SW-CONNECTOR") or child_kind in SIGNAL_MAPPINGS:
            if not _duplicate(list(target), child, path):
                added.append(_insert(target, child))
            continue
        existing = next((item for item in target if
                         (name and _name(item) == name) or (not name and item.tag == child.tag)), None)
        if existing is None:
            added.append(_insert(target, child))
            continue
        if existing.tag != child.tag:
            raise ValueError(f"Conflicting base object: {context}")
        if child_kind == "SWC-TO-ECU-MAPPING":
            if existing.findtext("ar:ECU-INSTANCE-REF", namespaces=NS) != child.findtext(
                    "ar:ECU-INSTANCE-REF", namespaces=NS):
                raise ValueError(f"Conflicting base ECU mapping: {context}")
        if child_kind in CONTAINERS or child_kind in MERGE_CHILDREN:
            _merge(existing, child, context, added)
        elif child_kind == "COMPONENT-IREF":
            if not any(_compatible(item, child) for item in target):
                added.append(_insert(target, child))
        elif not _compatible(existing, child):
            raise ValueError(f"Conflicting base object: {context}")


def merge_base_arxml(
    output_file: Path, base_file: Path, document: AUTOSAR, composition_path: str, system_path: str,
) -> None:
    """Preserve the entire base and append generated objects before atomic publication."""

    LOGGER.info("Adding table objects to complete base ARXML: %s", base_file)
    tree = _parse(base_file)
    root = tree.getroot()
    generated_root = _parse(output_file).getroot()
    base = _index(root)
    generated = _index(generated_root)
    # Signal mappings have no SHORT-NAME and can already exist under another SystemMapping.
    system = base.get(system_path)
    if system is not None:
        peers = list(system.findall("ar:MAPPINGS/ar:SYSTEM-MAPPING/ar:DATA-MAPPINGS/*", NS))
        for mapping in generated_root.findall(".//ar:SYSTEM/ar:MAPPINGS/ar:SYSTEM-MAPPING", NS):
            data = mapping.find("ar:DATA-MAPPINGS", NS)
            if data is not None:
                for item in list(data):
                    if item.tag in {_tag(name) for name in SIGNAL_MAPPINGS} and _duplicate(peers, item, system_path):
                        data.remove(item)
                if not len(data):
                    mapping.remove(data)
        for mappings in generated_root.findall(".//ar:SYSTEM/ar:MAPPINGS", NS):
            for mapping in list(mappings):
                if all(child.tag == _tag("SHORT-NAME") for child in mapping):
                    mappings.remove(mapping)
    for path, source in generated.items():
        if source.tag in {_tag("SYSTEM-MAPPING"), _tag("PORT-INTERFACE-MAPPING-SET")}:
            if path not in base and document.find(path) is not None:
                raise ValueError(f"Cannot split existing {source.tag.rsplit('}', 1)[-1]} while merging base: {path}")
    added: list[ET.Element] = []
    _merge(root, generated_root, "", added)
    merged = _index(root)
    checked = list(added)
    composition = base.get(composition_path)
    if composition is not None:
        checked.extend(composition.findall("ar:CONNECTORS/*", NS))
    if system is not None:
        checked.extend(item for item in system.findall("ar:MAPPINGS/ar:SYSTEM-MAPPING/ar:DATA-MAPPINGS/*", NS)
                       if item.tag in {_tag(name) for name in SIGNAL_MAPPINGS})
    for item in checked:
        for reference in item.iter():
            if not isinstance(reference.tag, str) or len(reference) or not reference.tag.endswith(("-REF", "-TREF")):
                continue
            path = (reference.text or "").strip()
            autosar_path(path, "Base connection or signal mapping reference")
            target = merged.get(path)
            if target is not None:
                destination = target.tag.rsplit("}", 1)[-1]
            else:
                existing = document.find(path)
                if existing is None:
                    raise ValueError(f"Base connection or signal mapping references missing object: {path}")
                destination = class_destination(existing)
            if reference.get("DEST") != destination:
                raise ValueError(f"Base reference {path} has DEST={reference.get('DEST')!r}; expected {destination}")
    ET.register_namespace("", NAMESPACE)
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    ET.indent(tree, space="  ")
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    LOGGER.info("Preserved complete base and added %s table subtrees -> %s", len(added), output_file)
