"""Retain only base connections and signal mappings in a freshly generated document."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path

from armodel import AUTOSAR

from ..common.names import autosar_path, short_name
from .values import class_destination

LOGGER = logging.getLogger(__name__)
NAMESPACE = "http://autosar.org/schema/r4.0"
NS = {"ar": NAMESPACE}
SIGNAL_MAPPINGS = {"SENDER-RECEIVER-TO-SIGNAL-MAPPING", "SENDER-RECEIVER-TO-SIGNAL-GROUP-MAPPING"}


def _tag(name: str) -> str:
    return f"{{{NAMESPACE}}}{name}"


def _name(element: ET.Element) -> str:
    return element.findtext("ar:SHORT-NAME", default="", namespaces=NS)


def _warn_name(name: str, context: str) -> None:
    try:
        short_name(name, context)
    except ValueError as error:
        LOGGER.warning("%s; preserving the existing base object: %s", error, name)


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


def _container(parent: ET.Element, name: str, before: tuple[str, ...] = ()) -> ET.Element:
    existing = parent.find(f"ar:{name}", NS)
    if existing is not None:
        return existing
    container = ET.Element(_tag(name))
    position = next((index for index, child in enumerate(parent) if child.tag in {
        _tag(tag) for tag in before
    }), len(parent))
    parent.insert(position, container)
    return container


def _signature(element: ET.Element, *, ignore_name: bool = False) -> tuple:
    return (
        element.tag, tuple(sorted(element.attrib.items())), (element.text or "").strip(),
        tuple(_signature(child) for child in element
              if not ignore_name or child.tag != _tag("SHORT-NAME")),
    )


def _references(element: ET.Element) -> tuple:
    return tuple(
        (item.tag, tuple(sorted(item.attrib.items())), (item.text or "").strip())
        for item in element.iter()
        if len(item) == 0 and item.tag.endswith(("-REF", "-TREF"))
    )


def _duplicate(items: Iterable[ET.Element], item: ET.Element, context: str) -> bool:
    name = _name(item)
    references = _references(item)
    for existing in items:
        same_name = bool(name) and name == _name(existing)
        same_refs = item.tag == existing.tag and references == _references(existing)
        if same_name or same_refs:
            if _signature(item, ignore_name=True) != _signature(existing, ignore_name=True):
                raise ValueError(f"Conflicting base connection or mapping at {context}/{name}")
            LOGGER.info("Reuse duplicate base connection or mapping: %s/%s", context, name)
            return True
        other = _name(existing)
        if name and other and name.casefold() == other.casefold():
            LOGGER.warning("%s: base name %s differs only in case from %s", context, name, other)
    return False


def _append(container: ET.Element, item: ET.Element, context: str) -> bool:
    if _duplicate(container, item, context):
        return False
    container.append(deepcopy(item))
    return True


def _owner(index: dict[str, ET.Element], path: str, tag: str) -> ET.Element:
    owner = index.get(path)
    if owner is None or owner.tag != _tag(tag):
        raise ValueError(f"Cannot retain base connections or signal mappings: no generated {tag} at {path}")
    return owner


def _retain_connection_dependency(
    path: str,
    base: dict[str, ET.Element],
    root: ET.Element,
    document: AUTOSAR,
    tag: str,
    container_name: str,
) -> ET.Element | None:
    source = base.get(path)
    if source is None or source.tag != _tag(tag):
        raise ValueError(f"Base connection references a missing {tag}: {path}")
    owner_path, _, name = path.rpartition("/")
    generated = _index(root)
    owner = generated.get(owner_path)
    if owner is None:
        raise ValueError(f"Cannot retain base {tag}; generated owner is missing: {owner_path}")
    if owner.tag != _tag("COMPOSITION-SW-COMPONENT-TYPE"):
        raise ValueError(f"Cannot retain base {tag}; owner is not a Composition: {owner_path}")
    for sibling_container in ("COMPONENTS", "PORTS", "CONNECTORS"):
        for sibling in owner.findall(f"ar:{sibling_container}/*", NS):
            if _name(sibling) != name:
                continue
            if sibling.tag != source.tag or _signature(sibling) != _signature(source):
                raise ValueError(f"Base connection dependency conflicts with generated object: {path}")
            return
    retained = deepcopy(source)
    _container(owner, container_name, ("PORTS", "CONNECTORS")).append(retained)
    LOGGER.info("Retained base connection dependency: %s", path)
    return retained


def _retain_interface_mapping(
    path: str, base: dict[str, ET.Element], root: ET.Element, document: AUTOSAR,
) -> ET.Element | None:
    autosar_path(path, "Base interface mapping reference", 3)
    current = _index(root)
    if path in current:
        if path in base and _signature(base[path]) != _signature(current[path]):
            raise ValueError(f"Conflicting base interface mapping: {path}")
        return
    if document.find(path) is not None:
        return
    source = base.get(path)
    if source is None or source.tag != _tag("VARIABLE-AND-PARAMETER-INTERFACE-MAPPING"):
        raise ValueError(f"Base connector references a missing interface mapping: {path}")
    package_path, set_name, _ = path.rsplit("/", 2)
    set_path = f"{package_path}/{set_name}"
    if set_path not in current:
        if document.find(set_path) is not None:
            raise ValueError(f"Cannot split existing interface mapping set while merging base: {set_path}")
        packages = _container(root, "AR-PACKAGES")
        parts = package_path.strip("/").split("/")
        for index, part in enumerate(parts):
            package = next((item for item in packages if _name(item) == part), None)
            if package is None:
                package = ET.SubElement(packages, _tag("AR-PACKAGE"))
                ET.SubElement(package, _tag("SHORT-NAME")).text = part
            if index < len(parts) - 1:
                packages = _container(package, "AR-PACKAGES")
        elements = _container(package, "ELEMENTS", ("AR-PACKAGES",))
        mapping_set = ET.SubElement(elements, _tag("PORT-INTERFACE-MAPPING-SET"))
        ET.SubElement(mapping_set, _tag("SHORT-NAME")).text = set_name
    else:
        mapping_set = _owner(current, set_path, "PORT-INTERFACE-MAPPING-SET")
    _append(_container(mapping_set, "PORT-INTERFACE-MAPPINGS"), source, set_path)
    return source


def merge_base_connections(
    output_file: Path, base_file: Path, document: AUTOSAR, composition_path: str, system_path: str,
) -> None:
    """Merge selected base subtrees; fail before publication on conflicts or dangling references."""

    LOGGER.info("Retaining connections and signal mappings from base ARXML: %s", base_file)
    try:
        tree = ET.parse(output_file)
        base_root = ET.parse(base_file).getroot()
    except ET.ParseError as error:
        raise ValueError(f"Cannot parse ARXML for base merge ({base_file}): {error}") from error
    root = tree.getroot()
    if root.tag != _tag("AUTOSAR") or base_root.tag != root.tag:
        raise ValueError(f"Base ARXML must use the AUTOSAR r4.0 namespace: {base_file}")
    generated = _index(root)
    base = _index(base_root)
    retained: list[ET.Element] = []
    connectors = signals = 0
    for path, source in base.items():
        if source.tag == _tag("COMPOSITION-SW-COMPONENT-TYPE"):
            if path != composition_path:
                continue
            items = source.findall("ar:CONNECTORS/*", NS)
            if not items:
                continue
            owner = _owner(generated, path, "COMPOSITION-SW-COMPONENT-TYPE")
            target = _container(owner, "CONNECTORS", ("DATA-TYPE-MAPPING-REFS",))
            for item in items:
                name = _name(item)
                if not name:
                    raise ValueError(f"Base connector has no SHORT-NAME: {path}")
                _warn_name(name, f"Base connector in {path}")
                for container_name in ("PORTS", "COMPONENTS"):
                    if any(_name(child) == name for child in owner.findall(f"ar:{container_name}/*", NS)):
                        raise ValueError(f"Base connector conflicts with generated port or component: {path}/{name}")
                if _append(target, item, path):
                    connectors += 1
                retained.append(item)
        elif source.tag == _tag("SYSTEM"):
            if path != system_path:
                continue
            for mapping in source.findall("ar:MAPPINGS/ar:SYSTEM-MAPPING", NS):
                items = [item for item in mapping.findall("ar:DATA-MAPPINGS/*", NS)
                         if item.tag in {_tag(name) for name in SIGNAL_MAPPINGS}]
                if not items:
                    continue
                owner = _owner(generated, path, "SYSTEM")
                existing = owner.findall("ar:MAPPINGS/ar:SYSTEM-MAPPING/ar:DATA-MAPPINGS/*", NS)
                retained.extend(items)
                items = [item for item in items if not _duplicate(existing, item, path)]
                if not items:
                    continue
                mappings = _container(owner, "MAPPINGS", ("ROOT-SOFTWARE-COMPOSITIONS", "SYSTEM-VERSION"))
                name = _name(mapping)
                if not name:
                    raise ValueError(f"Base SYSTEM-MAPPING has no SHORT-NAME: {path}")
                _warn_name(name, f"Base SYSTEM-MAPPING in {path}")
                target = next((item for item in mappings if _name(item) == name), None)
                if target is None:
                    if document.find(f"{path}/{name}") is not None:
                        raise ValueError(f"Cannot split existing SYSTEM-MAPPING while merging base: {path}/{name}")
                    target = ET.SubElement(mappings, _tag("SYSTEM-MAPPING"))
                    ET.SubElement(target, _tag("SHORT-NAME")).text = name
                data = _container(target, "DATA-MAPPINGS", ("SW-IMPL-MAPPINGS", "SW-MAPPINGS"))
                for item in items:
                    if _append(data, item, f"{path}/{name}"):
                        signals += 1
    dependencies = {
        (reference.text or "").strip()
        for item in retained for reference in item.iter(_tag("MAPPING-REF"))
    }
    for path in sorted(dependencies):
        dependency = _retain_interface_mapping(path, base, root, document)
        if dependency is not None:
            retained.append(dependency)
    dependencies = {
        ((reference.text or "").strip(), reference.get("DEST", ""))
        for item in tuple(retained)
        for reference in item.iter()
        if not len(reference) and reference.tag in {
            _tag("CONTEXT-COMPONENT-REF"), _tag("TARGET-P-COMPONENT-REF"),
            _tag("TARGET-P-PORT-REF"), _tag("TARGET-R-PORT-REF"), _tag("OUTER-PORT-REF"),
        }
    }
    for path, destination in sorted(dependencies):
        if not path or path in _index(root) or document.find(path) is not None:
            continue
        if destination == "SW-COMPONENT-PROTOTYPE":
            dependency = _retain_connection_dependency(
                path, base, root, document, "SW-COMPONENT-PROTOTYPE", "COMPONENTS")
        elif destination in ("P-PORT-PROTOTYPE", "R-PORT-PROTOTYPE"):
            dependency = _retain_connection_dependency(
                path, base, root, document, destination, "PORTS")
        else:
            continue
        if dependency is not None:
            retained.append(dependency)
    merged = _index(root)
    for item in retained:
        for reference in item.iter():
            # P-PORT-IN-COMPOSITION-INSTANCE-REF is a container, not a reference value.
            if len(reference) or not reference.tag.endswith(("-REF", "-TREF")):
                continue
            path = (reference.text or "").strip()
            autosar_path(path, "Base connection or signal mapping reference")
            target = merged.get(path)
            if target is not None:
                destination = target.tag.split("}")[-1]
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
    LOGGER.info("Retained %s base connectors and %s base signal mappings -> %s", connectors, signals, output_file)
