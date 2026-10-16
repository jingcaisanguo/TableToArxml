"""Workbook headers and accepted values shared by the reader and template generator."""

from .constants import (
    ACCESS_READ, ACCESS_RECEIVE_BY_ARGUMENT, ACCESS_RECEIVE_BY_VALUE, ACCESS_SEND, ACCESS_WRITE,
)

COMPONENTS_SHEET = "Components"
P_PORTS_SHEET = "P-Ports"
R_PORTS_SHEET = "R-Ports"
DATA_ACCESS_SHEET = "Data Accesses"
SR_INTERFACE_SHEET = "SR-Interfaces"
CS_INTERFACE_SHEET = "CS-Interfaces"
ENUMERATION_SHEET = "Enumerations"
ARRAY_SHEET = "Arrays"
STRUCTURE_SHEET = "Structures"

P_PORT_OPTIONAL_HEADERS = (
    "Create RTE Artifact",
    "Runnable Name",
    "Access Kind",
    "Transmission Acknowledge Timeout",
    "Uses End To End Protection",
    "Runnable Symbol",
    "Minimum Start Interval",
    "Can Be Invoked Concurrently",
)
R_PORT_OPTIONAL_HEADERS = (
    "Create RTE Artifact",
    "Runnable Name",
    "Access Kind",
    "Enable Update",
    "Queue Length",
)

# ``Access Kind`` 列的取值（比较时忽略大小写）。
ACCESS_KIND_LABELS = {
    "send": ACCESS_SEND,
    "receivebyargument": ACCESS_RECEIVE_BY_ARGUMENT,
    "receivebyvalue": ACCESS_RECEIVE_BY_VALUE,
    "read": ACCESS_READ,
    "write": ACCESS_WRITE,
}

# 布尔标志列的取值（比较时忽略大小写）；留空视为否。
BOOLEAN_LABELS = {
    "true": True,
    "yes": True,
    "y": True,
    "1": True,
    "false": False,
    "no": False,
    "n": False,
    "0": False,
}
BOOLEAN_CHOICES = "Y, N, TRUE, FALSE, YES, NO, 1, 0"
ARRAY_SIZE_SEMANTICS = ("FIXED-SIZE", "VARIABLE-SIZE")
CS_ARGUMENT_DIRECTIONS = ("IN", "OUT", "INOUT")
ACCESS_KIND_CHOICES = (
    "Send",
    "ReceiveByArgument",
    "ReceiveByValue",
    "Read",
    "Write",
)
BOOLEAN_VALUES = tuple(BOOLEAN_CHOICES.split(", "))
SHEET_HEADERS = {
    COMPONENTS_SHEET: (("SW-C", "Component Path"), ()),
    ENUMERATION_SHEET: (
        ("Type Path", "Target ARXML", "Implementation Data Type Ref", "Value", "Label"),
        ("Compu Method Ref", "Data Constr Ref"),
    ),
    ARRAY_SHEET: (
        ("Type Path", "Target ARXML", "Element Name", "Element Type Ref", "Array Size"),
        ("Array Size Semantics",),
    ),
    STRUCTURE_SHEET: (("Type Path", "Target ARXML", "Member Name", "Member Type Ref"), ()),
    SR_INTERFACE_SHEET: (
        ("Interface Path", "Data Element Name", "Data Type Ref", "Target ARXML"),
        ("Is Queued", "Is Service"),
    ),
    CS_INTERFACE_SHEET: (
        ("Interface Path", "Operation Name", "Target ARXML"),
        ("Argument Name", "Direction", "Data Type Ref", "Is Service"),
    ),
    P_PORTS_SHEET: (("SW-C", "Port Name", "Port Interface Ref"), P_PORT_OPTIONAL_HEADERS),
    R_PORTS_SHEET: (("SW-C", "Port Name", "Port Interface Ref"), R_PORT_OPTIONAL_HEADERS),
    DATA_ACCESS_SHEET: (
        ("SW-C", "Runnable Name", "Port Name"),
        ("Data Element Name", "Access Kind", "Access Name"),
    ),
}
