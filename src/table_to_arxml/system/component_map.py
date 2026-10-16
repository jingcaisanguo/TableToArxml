"""绿表组件名到目标 Composition 中实例名、类型路径的映射。"""

from __future__ import annotations

import re

from .arxml_model import ArxmlModel


class ComponentTypeMap(dict):
    """名称 -> ``(实例名, 类型路径)``；有歧义的名称不在映射中，由 :meth:`resolve` 报出候选。"""

    def __init__(self, candidates: dict[str, set[tuple[str, str]]]) -> None:
        super().__init__((alias, next(iter(values))) for alias, values in candidates.items()
                         if len(values) == 1)
        self.ambiguous = {alias: values for alias, values in candidates.items() if len(values) > 1}

    def resolve(self, name: str) -> tuple[str, str] | None:
        """返回唯一匹配；未知名称返回 ``None``，有歧义时报错并列出候选。"""

        if name in self.ambiguous:
            choices = ", ".join(f"{prototype} ({path})" for prototype, path in sorted(self.ambiguous[name]))
            raise ValueError(f"Ambiguous component '{name}': {choices}; configure componentAliases")
        return self.get(name)


def build_component_type_map(
    model: ArxmlModel,
    composition_path: str,
    aliases: dict[str, str | dict[str, str]],
    prototype_prefix: str,
) -> ComponentTypeMap:
    """按完整路径索引类型，短名只在唯一时可用；已有实例限定到目标 composition。"""

    component_types = model.component_types()
    # 组合类型只能通过 componentAliases 显式引用，避免目标/根组合参与短名匹配。
    composition_types = model.composition_types()
    prototypes = model.component_prototypes(composition_path.rstrip("/"))
    candidates: dict[str, set[tuple[str, str]]] = {}
    by_type: dict[str, set[tuple[str, str]]] = {}
    named_types: set[str] = set()
    for instance_path, type_path in prototypes.items():
        definition = (instance_path.rsplit("/", 1)[-1], type_path)
        by_type.setdefault(type_path, set()).add(definition)
        candidates.setdefault(instance_path, set()).add(definition)
    for type_path, short_name in component_types.items():
        definitions = by_type.setdefault(type_path, {(prototype_prefix + short_name, type_path)})
        candidates.setdefault(type_path, set()).update(definitions)
        candidates.setdefault(short_name, set()).update(definitions)
        for definition in definitions:
            candidates.setdefault(definition[0], set()).add(definition)
    for alias, target in aliases.items():
        if not isinstance(alias, str) or not alias or "/" in alias or "\\" in alias:
            raise ValueError("componentAliases keys must be nonempty names without path separators")
        # 对象形式 {"type": 类型路径, "prototype": 实例名} 用于不带 prototypePrefix 的新实例名。
        prototype_name = ""
        path = target
        if isinstance(target, dict):
            path, prototype_name = target.get("type"), target.get("prototype")
            if not isinstance(prototype_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", prototype_name):
                raise ValueError(f"componentAliases.{alias}.prototype must be an AUTOSAR SHORT-NAME")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError(f"componentAliases.{alias} must be a full AUTOSAR path")
        if path in prototypes and not prototype_name:
            definition = (path.rsplit("/", 1)[-1], prototypes[path])
        elif path in component_types or path in composition_types:
            definitions = by_type.setdefault(path, {(prototype_prefix + path.rsplit("/", 1)[-1], path)})
            if len(definitions) != 1:
                raise ValueError(f"componentAliases.{alias}: multiple target instances; use a prototype path")
            definition = next(iter(definitions))
            if path not in prototypes.values() and path not in named_types:
                definition = (prototype_name or prototype_prefix + alias, path)
                old_definition = next(iter(definitions))
                for values in candidates.values():
                    if old_definition in values:
                        values.remove(old_definition)
                        values.add(definition)
                by_type[path] = {definition}
                named_types.add(path)
            elif prototype_name and definition[0] != prototype_name:
                raise ValueError(
                    f"componentAliases.{alias}: {path} already has instance '{definition[0]}'")
        else:
            raise ValueError(f"componentAliases.{alias}: unknown type or prototype outside target: {path}")
        candidates[alias] = {definition}
        candidates.setdefault(definition[0], set()).add(definition)
    return ComponentTypeMap(candidates)
