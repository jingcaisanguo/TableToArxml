"""推导白表 General 页的值。"""

from __future__ import annotations

import logging

from .arxml_model import ArxmlModel
from .values import norm

LOGGER = logging.getLogger(__name__)


def resolve_general(general: dict, model: ArxmlModel) -> dict[str, str]:
    """解析白表 General 页的值。

    ``Composition`` 决定新组件放在哪里，必须配置；其余项未配置时按唯一性推导：
    ``System`` 取根组合逐层包含该 Composition 的 System，``EcuInstance`` 取该 System
    FIBEX-ELEMENTS 中的 ECU-INSTANCE，``RootSwCompositionPrototype`` 取该 System 的根组合。
    """

    composition = norm(general.get("Composition"))
    if not composition:
        raise ValueError("Missing config.general.Composition")
    resolved: dict[str, str] = {"Composition": composition}

    system_name = norm(general.get("System"))
    if system_name:
        system = model.find_system(system_name)
        if system is None:
            raise ValueError(f"config.general.System '{system_name}' was not found in the loaded ARXML files")
    else:
        systems = model.systems_containing(composition)
        if len(systems) != 1:
            raise ValueError(
                f"Cannot derive config.general.System: {len(systems)} System(s) contain {composition}"
                + "".join(f", {item.getFullName()}" for item in systems))
        system = systems[0]
        LOGGER.info("config.general.System resolved from Composition %s: %s", composition, system.getFullName())
    # 已有 System 统一写完整路径，生成时按同路径拆分合并到该 System。
    resolved["System"] = system.getFullName()

    ecu_instance = norm(general.get("EcuInstance"))
    if not ecu_instance:
        ecu_instances = model.system_ecu_instances(system)
        if len(ecu_instances) != 1:
            raise ValueError(
                f"Cannot derive config.general.EcuInstance: System '{resolved['System']}' "
                f"references {len(ecu_instances)} ECU-INSTANCE(s)")
        ecu_instance = ecu_instances[0]
        LOGGER.info("config.general.EcuInstance resolved from System %s: %s", resolved["System"], ecu_instance)
    resolved["EcuInstance"] = ecu_instance

    root_composition = norm(general.get("RootSwCompositionPrototype"))
    if not root_composition:
        root_composition = model.root_composition_path(resolved["System"])
        if not root_composition:
            raise ValueError(
                "Missing config.general.RootSwCompositionPrototype because System "
                f"'{resolved['System']}' has no ROOT-SOFTWARE-COMPOSITION in the loaded ARXML")
        LOGGER.info(
            "config.general.RootSwCompositionPrototype resolved from System %s: %s",
            resolved["System"], root_composition)
    resolved["RootSwCompositionPrototype"] = root_composition
    return resolved
