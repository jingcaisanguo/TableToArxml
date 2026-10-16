"""求数据类型的最终签名（位宽、编码、字节序、数组/结构形状），用于判断接口两端类型是否兼容。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Callable

from armodel import AtomicSwComponentType
from armodel.models.M2.AUTOSARTemplates.CommonStructure.ImplementationDataTypes import (
    ImplementationDataType, ImplementationDataTypeElement,
)
from armodel.models.M2.AUTOSARTemplates.SWComponentTemplate.Datatype.Datatypes import (
    ApplicationArrayDataType, ApplicationDataType, ApplicationPrimitiveDataType,
    ApplicationRecordDataType, DataTypeMappingSet,
)
from armodel.models.M2.MSR.AsamHdo.BaseTypes import SwBaseType

from .values import ref_value

DATA_TYPE_DESTINATIONS = {
    "SW-BASE-TYPE": SwBaseType,
    "IMPLEMENTATION-DATA-TYPE": ImplementationDataType,
    "APPLICATION-PRIMITIVE-DATA-TYPE": ApplicationPrimitiveDataType,
    "APPLICATION-ARRAY-DATA-TYPE": ApplicationArrayDataType,
    "APPLICATION-RECORD-DATA-TYPE": ApplicationRecordDataType,
}
BASE_TYPE_ENCODINGS = {"NONE", "2C", "1C", "SM", "IEEE754", "BOOLEAN"}
FALSE_VALUES = ("", "false", "0")


def _positive_size(value, label: str) -> int:
    try:
        size = Decimal(ref_value(value))
        if size.is_finite() and size > 0 and size == size.to_integral_value():
            return int(size)
    except InvalidOperation:
        pass
    raise ValueError(f"Missing or invalid {label}: {ref_value(value)}")


class TypeSignatures:
    """应用类型先按组件 InternalBehavior 引用的 DataTypeMappingSet 映射到实现类型，再展开成签名。

    ``lookup(method, path)`` 调用 AUTOSAR 文档的查找方法，由调用方负责缓存。
    """

    def __init__(self, lookup: Callable[[str, str], object]) -> None:
        self.lookup = lookup

    def signature(self, reference, component_type_path: str, active: tuple[str, ...] = ()) -> tuple:
        path = ref_value(reference)
        if not path or path in active:
            raise ValueError(f"Missing or cyclic data type reference: {' -> '.join(active + (path,))}")
        data_type = self.lookup("find", path)
        if data_type is None:
            raise ValueError(f"Data type is not defined: {path}")
        destination = str(reference.getDest() or "")
        expected = DATA_TYPE_DESTINATIONS.get(destination)
        if expected is None or not isinstance(data_type, expected):
            raise ValueError(f"Unsupported or incorrect data type DEST '{destination}': {path}")
        active = active + (path,)
        if isinstance(data_type, SwBaseType):
            definition = data_type.getBaseTypeDefinition()
            if definition is None:
                raise ValueError(f"Missing base type definition: {path}")
            width = _positive_size(definition.getBaseTypeSize(), f"BASE-TYPE-SIZE of {path}")
            encoding = ref_value(definition.getBaseTypeEncoding()).upper()
            if encoding not in BASE_TYPE_ENCODINGS:
                raise ValueError(f"Missing or unsupported BASE-TYPE-ENCODING '{encoding}': {path}")
            return ("scalar", width, encoding, ref_value(definition.getByteOrder()))
        if isinstance(data_type, ApplicationDataType):
            component_type = self.lookup("find", component_type_path)
            behavior = (component_type.getInternalBehavior()
                        if isinstance(component_type, AtomicSwComponentType) else None)
            mapped: dict[str, object] = {}
            for mapping_ref in behavior.getDataTypeMappingRefs() if behavior is not None else []:
                mapping_set = self.lookup("find", ref_value(mapping_ref))
                if not isinstance(mapping_set, DataTypeMappingSet):
                    raise ValueError(f"DataTypeMappingSet is not defined: {ref_value(mapping_ref)}")
                for mapping in mapping_set.getDataTypeMaps():
                    if ref_value(mapping.getApplicationDataTypeRef()) == path:
                        implementation = mapping.getImplementationDataTypeRef()
                        mapped[ref_value(implementation)] = implementation
            if len(mapped) > 1:
                raise ValueError(
                    f"Ambiguous implementation type for {path} in {component_type_path}: {sorted(mapped)}")
            if mapped:
                return self.signature(next(iter(mapped.values())), component_type_path, active)
            if not isinstance(data_type, ApplicationPrimitiveDataType):
                raise ValueError(f"No component-scoped implementation mapping for application type: {path}")
            return self._property_signature(data_type, component_type_path, active)
        return self._implementation_signature(data_type, component_type_path, active)

    def _property_signature(self, data_type, component_type_path: str, active: tuple[str, ...]) -> tuple:
        props = data_type.getSwDataDefProps()
        path = data_type.getFullName()
        if props is None:
            raise ValueError(f"Cannot resolve final data type; SW-DATA-DEF-PROPS is missing: {path}")
        if props.getSwPointerTargetProps() is not None or props.getSwBitRepresentation() is not None:
            raise ValueError(f"Pointer or bit-field data type is not supported for interface mapping: {path}")
        references = [reference for reference in (props.getImplementationDataTypeRef(), props.getBaseTypeRef())
                      if ref_value(reference)]
        if not references:
            raise ValueError(f"Cannot resolve final data type; no implementation/base type reference: {path}")
        signatures = [self.signature(reference, component_type_path, active) for reference in references]
        if any(signature != signatures[0] for signature in signatures[1:]):
            raise ValueError(f"Conflicting implementation/base type definitions: {path}")
        return signatures[0]

    def _implementation_signature(self, data_type, component_type_path: str, active: tuple[str, ...]) -> tuple:
        category = ref_value(data_type.getCategory()).upper()
        path = data_type.getFullName()
        if category in ("VALUE", "TYPE_REFERENCE"):
            if data_type.getSubElements():
                raise ValueError(f"Unexpected sub-elements on scalar/alias type: {path}")
            return self._property_signature(data_type, component_type_path, active)
        if category not in ("ARRAY", "STRUCTURE"):
            raise ValueError(f"Unsupported implementation type category '{category}': {path}")
        if isinstance(data_type, ImplementationDataType) and (
                ref_value(data_type.getDynamicArraySizeProfile())
                or ref_value(data_type.getIsStructWithOptionalElement()).lower() not in FALSE_VALUES):
            raise ValueError(f"Dynamic or optional composite type cannot be mapped automatically: {path}")
        elements = data_type.getSubElements()
        if not elements or (category == "ARRAY" and len(elements) != 1):
            raise ValueError(f"Invalid {category} data type elements: {path}")
        signatures = []
        for element in elements:
            if not isinstance(element, ImplementationDataTypeElement):
                raise ValueError(f"Unsupported data type element in {path}")
            if ref_value(element.getIsOptional()).lower() not in FALSE_VALUES:
                raise ValueError(f"Optional data type element cannot be mapped automatically: {element.getFullName()}")
            if category == "ARRAY":
                if ref_value(element.getArraySizeSemantics()) != "FIXED-SIZE":
                    raise ValueError(f"Only fixed-size arrays support interface mapping: {path}")
                size = _positive_size(element.getArraySize(), f"ARRAY-SIZE of {path}")
            else:
                if element.getArraySize() is not None:
                    raise ValueError(f"Array dimensions require an explicit ARRAY type: {element.getFullName()}")
                size = None
            signature = self._implementation_signature(element, component_type_path, active)
            signatures.append((element.getShortName() if category == "STRUCTURE" else size, signature))
        return (category.lower(), tuple(signatures))
