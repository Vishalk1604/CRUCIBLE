"""Real dimensional tables for the demo categories.

These are engineering standards, not invented numbers. A ball bearing designated 6205
has a 25 mm bore, a 52 mm outside diameter and a 15 mm width because ISO 15 says so, and
a 3/8-16 hex cap screw takes a 9/16 inch wrench because ASME B18.2.1 says so.

Grounding the corpus in real tables matters for two reasons. The generated products are
physically coherent, so the constraint verifier is being tested against realistic data
rather than noise. And a judge who knows the domain can check a row against a catalog and
find it correct, which is worth more than any claim about realism.
"""

from __future__ import annotations

from typing import NamedTuple


class BearingSpec(NamedTuple):
    """Bore, outside diameter and width in millimetres, per ISO 15."""

    designation: str
    bore: float
    outside_diameter: float
    width: float
    dynamic_load_n: float
    static_load_n: float


#: Deep groove ball bearings, 62xx and 63xx series. Load ratings are representative
#: catalog values; the dimensions are standardised exactly.
BEARINGS: tuple[BearingSpec, ...] = (
    BearingSpec("6200", 10, 30, 9, 5100, 2360),
    BearingSpec("6201", 12, 32, 10, 6800, 3100),
    BearingSpec("6202", 15, 35, 11, 7650, 3750),
    BearingSpec("6203", 17, 40, 12, 9550, 4750),
    BearingSpec("6204", 20, 47, 14, 12700, 6550),
    BearingSpec("6205", 25, 52, 15, 14000, 7800),
    BearingSpec("6206", 30, 62, 16, 19500, 11200),
    BearingSpec("6207", 35, 72, 17, 25500, 15300),
    BearingSpec("6208", 40, 80, 18, 32500, 19000),
    BearingSpec("6209", 45, 85, 19, 35100, 21600),
    BearingSpec("6210", 50, 90, 20, 35100, 23200),
    BearingSpec("6300", 10, 35, 11, 8060, 3450),
    BearingSpec("6301", 12, 37, 12, 9750, 4150),
    BearingSpec("6302", 15, 42, 13, 11400, 5400),
    BearingSpec("6303", 17, 47, 14, 13500, 6550),
    BearingSpec("6304", 20, 52, 15, 15900, 7800),
    BearingSpec("6305", 25, 62, 17, 22500, 11600),
    BearingSpec("6306", 30, 72, 19, 28100, 16000),
    BearingSpec("6307", 35, 80, 21, 33200, 19000),
    BearingSpec("6308", 40, 90, 23, 41000, 24000),
)

#: Seal suffixes and the vocabulary term each maps to.
BEARING_SEALS: dict[str, str] = {
    "": "open",
    "-2RS": "2RS rubber sealed both sides",
    "-RS": "RS rubber sealed one side",
    "-2Z": "2Z metal shielded both sides",
    "-Z": "Z metal shielded one side",
}


class ThreadSpec(NamedTuple):
    """Imperial hex cap screw geometry, per ASME B18.2.1. Dimensions in inches."""

    label: str  # e.g. "3/8-16"
    nominal_diameter: float
    threads_per_inch: int
    width_across_flats: float
    head_height: float


#: Unified coarse thread series, the overwhelming majority of MRO fastener stock.
THREADS: tuple[ThreadSpec, ...] = (
    ThreadSpec("1/4-20", 0.250, 20, 0.4375, 0.1563),
    ThreadSpec("5/16-18", 0.3125, 18, 0.5000, 0.2031),
    ThreadSpec("3/8-16", 0.375, 16, 0.5625, 0.2344),
    ThreadSpec("7/16-14", 0.4375, 14, 0.6250, 0.2656),
    ThreadSpec("1/2-13", 0.500, 13, 0.7500, 0.3125),
    ThreadSpec("9/16-12", 0.5625, 12, 0.8125, 0.3438),
    ThreadSpec("5/8-11", 0.625, 11, 0.9375, 0.3750),
    ThreadSpec("3/4-10", 0.750, 10, 1.1250, 0.4375),
    ThreadSpec("7/8-9", 0.875, 9, 1.3125, 0.5000),
    ThreadSpec("1-8", 1.000, 8, 1.5000, 0.5625),
)

#: Common lengths in inches. Real catalogs stock these and little else.
SCREW_LENGTHS: tuple[float, ...] = (
    0.5,
    0.625,
    0.75,
    1.0,
    1.25,
    1.5,
    1.75,
    2.0,
    2.5,
    3.0,
    3.5,
    4.0,
    5.0,
    6.0,
)

#: Grade designation mapped to (vocabulary term, minimum tensile strength in psi).
SCREW_GRADES: dict[str, tuple[str, float]] = {
    "GR2": ("SAE Grade 2", 74000),
    "GR5": ("SAE Grade 5", 120000),
    "GR8": ("SAE Grade 8", 150000),
    "A307": ("ASTM A307", 60000),
    "A325": ("ASTM A325", 120000),
    "8.8": ("ISO Class 8.8", 116000),
    "10.9": ("ISO Class 10.9", 145000),
    "18-8": ("A2-70 stainless", 70000),
    "316SS": ("A4-80 stainless", 80000),
}

#: Finish code mapped to its vocabulary term.
SCREW_FINISHES: dict[str, str] = {
    "ZP": "zinc plated",
    "HDG": "hot dip galvanized",
    "PLN": "plain",
    "BO": "black oxide",
    "YZ": "yellow zinc",
    "PAS": "passivated",
}

#: Grade to the material it is made from. Grade and material are not independent, and
#: generating them independently would produce records no manufacturer makes.
GRADE_MATERIAL: dict[str, str] = {
    "GR2": "low carbon steel",
    "GR5": "medium carbon steel",
    "GR8": "alloy steel",
    "A307": "low carbon steel",
    "A325": "medium carbon steel",
    "8.8": "medium carbon steel",
    "10.9": "alloy steel",
    "18-8": "304 stainless steel",
    "316SS": "316 stainless steel",
}


class ValveSize(NamedTuple):
    """Nominal pipe size with full-port and reduced-port bores, in inches."""

    label: str  # e.g. "1/2"
    nominal: float
    full_port_bore: float
    reduced_port_bore: float
    body_diameter: float
    end_to_end: float


#: Two-piece threaded ball valves across the common size range.
VALVE_SIZES: tuple[ValveSize, ...] = (
    ValveSize("1/4", 0.25, 0.25, 0.1875, 1.10, 1.75),
    ValveSize("3/8", 0.375, 0.375, 0.25, 1.25, 1.95),
    ValveSize("1/2", 0.5, 0.5, 0.375, 1.55, 2.36),
    ValveSize("3/4", 0.75, 0.75, 0.5, 1.85, 2.76),
    ValveSize("1", 1.0, 1.0, 0.75, 2.25, 3.15),
    ValveSize("1-1/4", 1.25, 1.25, 1.0, 2.75, 3.62),
    ValveSize("1-1/2", 1.5, 1.5, 1.25, 3.15, 4.02),
    ValveSize("2", 2.0, 2.0, 1.5, 3.85, 4.72),
    ValveSize("2-1/2", 2.5, 2.5, 2.0, 4.72, 5.91),
    ValveSize("3", 3.0, 3.0, 2.5, 5.51, 6.50),
    ValveSize("4", 4.0, 4.0, 3.0, 6.89, 7.87),
)

#: Body material code mapped to (vocabulary term, typical pressure rating in psi).
VALVE_BODIES: dict[str, tuple[str, float]] = {
    "SS": ("316 stainless steel", 1000),
    "304SS": ("304 stainless steel", 1000),
    "CS": ("carbon steel", 600),
    "BRS": ("brass", 600),
    "BRZ": ("bronze", 600),
    "DI": ("ductile iron", 400),
    "PVC": ("PVC", 150),
}

#: Seat material code mapped to (vocabulary term, temperature range in degF).
VALVE_SEATS: dict[str, tuple[str, tuple[float, float]]] = {
    "PTFE": ("PTFE", (-20, 400)),
    "RPTFE": ("RPTFE", (-20, 450)),
    "PEEK": ("PEEK", (-20, 550)),
    "NYL": ("nylon", (-20, 200)),
}

#: End connection code mapped to its vocabulary term.
VALVE_ENDS: dict[str, str] = {
    "SCRD": "NPT threaded",
    "NPT": "NPT threaded",
    "BSP": "BSP threaded",
    "SW": "socket weld",
    "BW": "butt weld",
    "FLG": "flanged",
}
