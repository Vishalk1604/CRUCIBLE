"""Parsing and normalization of industrial quantity notation.

Generic unit parsers assume values look like "12.7 mm". Industrial catalogs do not.
They look like this:

    1/2"        3/8-16      1-1/2 IN     600WOG      -20...120C
    M8x1.25     .500 DIA    12,7 mm      #10-24      150# FLG

Every one of those appears in real distributor data, and every one of them breaks a
naive float() plus split(). This module turns them into something pint can reason about,
which is the precondition for the dimensional verifier catching "thread pitch: 4.2 kg".

Two deliberate choices:

  * Parsing never guesses a unit. If the text carries no unit, the result has unit=None
    and the caller decides whether the attribute's canonical unit may be assumed. A
    parser that silently invents units would manufacture exactly the errors we exist to
    catch.
  * Trade ratings that imply a unit (600 WOG means 600 psi) are expanded through an
    explicit, auditable table rather than by pattern-guessing, so a reviewer can see
    why a conversion happened.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

import pint


@lru_cache(maxsize=1)
def registry() -> pint.UnitRegistry:
    """The shared pint registry.

    Cached because constructing a UnitRegistry parses the full unit definition file,
    which is far too slow to repeat per value on a million-SKU catalog.
    """
    ureg = pint.UnitRegistry()
    # Trade units pint does not ship with. Defined rather than aliased so their
    # dimensionality is explicit and checkable.
    ureg.define("wog = psi")  # water/oil/gas cold working pressure rating
    ureg.define("wsp = psi")  # working steam pressure rating
    ureg.define("cfm = foot ** 3 / minute")
    ureg.define("gpm = gallon / minute")
    return ureg


#: Unit tokens as they actually appear in catalogs, mapped to pint unit names.
#: Lower-cased keys; lookup is case-insensitive.
UNIT_ALIASES: dict[str, str] = {
    # length
    '"': "inch",
    "in": "inch",
    "inch": "inch",
    "inches": "inch",
    "ins": "inch",
    "'": "foot",
    "ft": "foot",
    "feet": "foot",
    "mm": "millimeter",
    "millimeter": "millimeter",
    "millimetre": "millimeter",
    "cm": "centimeter",
    "m": "meter",
    "meter": "meter",
    "metre": "meter",
    "mil": "thou",
    "thou": "thou",
    "micron": "micrometer",
    "um": "micrometer",
    # mass
    "lb": "pound",
    "lbs": "pound",
    "pound": "pound",
    "pounds": "pound",
    "oz": "ounce",
    "kg": "kilogram",
    "g": "gram",
    "gram": "gram",
    # pressure
    "psi": "psi",
    "psig": "psi",
    "psia": "psi",
    "wog": "wog",
    "wsp": "wsp",
    "bar": "bar",
    "kpa": "kilopascal",
    "mpa": "megapascal",
    "pa": "pascal",
    # temperature
    "c": "degC",
    "degc": "degC",
    "deg c": "degC",
    "celsius": "degC",
    "f": "degF",
    "degf": "degF",
    "deg f": "degF",
    "fahrenheit": "degF",
    "k": "kelvin",
    # torque / force
    "nm": "newton * meter",
    "n-m": "newton * meter",
    "n.m": "newton * meter",
    "ftlb": "foot * pound_force",
    "ft-lb": "foot * pound_force",
    "inlb": "inch * pound_force",
    "in-lb": "inch * pound_force",
    "n": "newton",
    "kn": "kilonewton",
    # electrical
    "v": "volt",
    "volt": "volt",
    "volts": "volt",
    "vdc": "volt",
    "vac": "volt",
    "a": "ampere",
    "amp": "ampere",
    "amps": "ampere",
    "ma": "milliampere",
    "w": "watt",
    "kw": "kilowatt",
    "hp": "horsepower",
    "hz": "hertz",
    "ohm": "ohm",
    # flow / volume
    "gpm": "gpm",
    "cfm": "cfm",
    "l": "liter",
    "ml": "milliliter",
    # Appliance capacity is written "21CF" for 21 cubic feet. Unlike dBA this is a real
    # physical unit, so it belongs here rather than in DISPLAY_ONLY_UOM: a freezer's
    # capacity is something the dimensional verifier can genuinely check.
    "cf": "foot ** 3",
    "cuft": "foot ** 3",
    "cu ft": "foot ** 3",
    "cuin": "inch ** 3",
}

#: Characters that appear in scanned or copy-pasted catalog text and mean something else.
_CHAR_FIXES = {
    "″": '"',  # double prime
    "′": "'",  # prime
    "”": '"',  # right double quote
    "“": '"',
    "’": "'",
    "‘": "'",
    "°": "deg",  # degree sign
    "Ø": "",  # diameter symbol: a marker, not part of the value
    "⌀": "",
    "µ": "u",  # micro sign
    "μ": "u",
    "−": "-",  # unicode minus
    "–": "-",  # en dash
    "—": "-",  # em dash
    "·": ".",  # middot, as in N·m
    " ": " ",  # non-breaking space
}

_RANGE_SEPARATORS = r"(?:\.\.\.|\.\.|--|—|\bto\b|~)"


class UnitParseError(ValueError):
    """Raised when a string cannot be read as a quantity at all."""


@dataclass(frozen=True)
class ParsedQuantity:
    """A magnitude (or range) plus the unit it was written in.

    unit is None when the source text carried no unit at all. That is information, not
    a failure: "1/2" in a bore column is probably inches, but this module refuses to be
    the one that assumes it.
    """

    magnitude: float
    unit: str | None
    low: float | None = None
    high: float | None = None
    raw: str = ""

    @property
    def is_range(self) -> bool:
        return self.low is not None and self.high is not None

    def to_pint(self):
        """As a pint Quantity, using the shared registry. Dimensionless if no unit."""
        ureg = registry()
        if self.unit is None:
            return ureg.Quantity(self.magnitude)
        return ureg.Quantity(self.magnitude, self.unit)


#: Characters NFKC would decompose that must survive intact. The trademark sign becomes
#: the letters "TM" under NFKC while the registered sign is left alone - an inconsistency
#: in the standard, not in the data. Brand names have to match the approved list "exactly,
#: symbols and all", so it is shielded across normalisation and restored afterwards.
_PROTECTED = {"\u2122": "\x00TM\x00"}


def clean_text(text: str) -> str:
    """Fold the unicode and typographic noise that survives OCR and copy-paste.

    Substitutions run *before* NFKC normalization, not after. NFKC decomposes the double
    prime used for inches into two apostrophes, which would destroy the distinction
    between inches and feet before we ever got to look at it.
    """
    for bad, good in _CHAR_FIXES.items():
        text = text.replace(bad, good)
    for char, token in _PROTECTED.items():
        text = text.replace(char, token)

    text = unicodedata.normalize("NFKC", text)

    for char, token in _PROTECTED.items():
        text = text.replace(token, char)
    return text.strip()


def _parse_number(token: str) -> float:
    """Read the numeric forms industrial catalogs actually use.

    Handles plain decimals, leading-dot decimals (.500), vulgar fractions (3/8),
    mixed numbers (1-1/2 and 1 1/2), and European decimal commas (12,7).
    """
    token = token.strip()
    if not token:
        raise UnitParseError("empty numeric token")

    # European decimal comma: 12,7 -> 12.7. Only when the comma is not a thousands
    # separator, which we detect by the group after it not being exactly three digits.
    if re.fullmatch(r"-?\d+,\d+", token) and not re.fullmatch(r"-?\d+,\d{3}", token):
        token = token.replace(",", ".")
    token = token.replace(",", "")  # any remaining commas are thousands separators

    # Mixed number: "1-1/2" or "1 1/2" -> 1.5. The hyphen form is ambiguous with a
    # negative sign, so require digits on both sides of the fraction to disambiguate.
    mixed = re.fullmatch(r"(-?\d+)[\s-](\d+)/(\d+)", token)
    if mixed:
        whole, num, den = int(mixed[1]), int(mixed[2]), int(mixed[3])
        if den == 0:
            raise UnitParseError(f"zero denominator in {token!r}")
        sign = -1 if whole < 0 else 1
        return whole + sign * (num / den)

    # Simple fraction
    frac = re.fullmatch(r"(-?\d+)/(\d+)", token)
    if frac:
        if int(frac[2]) == 0:
            raise UnitParseError(f"zero denominator in {token!r}")
        return int(frac[1]) / int(frac[2])

    try:
        return float(token)
    except ValueError as exc:
        raise UnitParseError(f"cannot read {token!r} as a number") from exc


def normalize_unit(token: str) -> str | None:
    """Map a catalog unit token onto a pint unit name, or None if unrecognized."""
    token = token.strip().strip(".").lower()
    if not token:
        return None
    if token in UNIT_ALIASES:
        return UNIT_ALIASES[token]
    # "deg c" survives cleaning as "degc"; also handles "deg  f"
    collapsed = re.sub(r"\s+", "", token)
    return UNIT_ALIASES.get(collapsed)


# Number pattern covering mixed numbers, fractions, decimals, leading-dot decimals, and
# comma-bearing numbers (both European decimals and thousands separators). Alternation
# order matters: the longest, most specific forms must be tried first or "1-1/2" is read
# as the single number 1.
_NUM = r"-?(?:\d+[\s-]\d+/\d+|\d+/\d+|\d*\.\d+|\d[\d,]*(?:\.\d+)?)"


def parse_quantity(text: str) -> ParsedQuantity:
    """Read a quantity out of raw catalog text.

    Raises UnitParseError when there is no number to be found. Callers treat that as a
    hard verifier failure rather than an exception to swallow: an attribute declared as
    a quantity whose value contains no number is exactly the failure mode we want caught.
    """
    original = text
    text = clean_text(text)
    if not text:
        raise UnitParseError("empty value")

    # Range first, so "-20 to 120 C" is not read as the single number -20.
    range_match = re.search(
        rf"^\s*({_NUM})\s*(?:[a-zA-Z\"']*)\s*{_RANGE_SEPARATORS}\s*\+?({_NUM})\s*(.*)$",
        text,
        flags=re.IGNORECASE,
    )
    if range_match:
        low = _parse_number(range_match[1])
        high = _parse_number(range_match[2])
        unit = normalize_unit(range_match[3])
        if low > high:
            low, high = high, low
        return ParsedQuantity(
            magnitude=(low + high) / 2, unit=unit, low=low, high=high, raw=original
        )

    # Signed range written as "-20/+120C" — distinct from a fraction because of the plus.
    signed_range = re.search(rf"^\s*({_NUM})\s*/\s*\+({_NUM})\s*(.*)$", text)
    if signed_range:
        low = _parse_number(signed_range[1])
        high = _parse_number(signed_range[2])
        unit = normalize_unit(signed_range[3])
        return ParsedQuantity(
            magnitude=(low + high) / 2, unit=unit, low=low, high=high, raw=original
        )

    # Single value: number, then whatever trails it is the candidate unit.
    single = re.search(rf"({_NUM})\s*(.*)$", text)
    if not single:
        raise UnitParseError(f"no number found in {original!r}")

    magnitude = _parse_number(single[1])
    trailing = single[2].strip()

    # Trade forms where the rating is glued to the number: 600WOG, 150#, 1/2NPT.
    # Take the leading alphabetic (or quote) run as the unit token and drop the rest,
    # which is typically a standard designation rather than a unit.
    unit_token = re.match(r"^([a-zA-Z\"'.\-]+)", trailing)
    unit = normalize_unit(unit_token[1]) if unit_token else None

    return ParsedQuantity(magnitude=magnitude, unit=unit, raw=original)


def convert(qty: ParsedQuantity, target_unit: str) -> float:
    """Convert a parsed quantity to the attribute's canonical unit.

    Offset units are handled by pint directly; degC to kelvin must not be a bare
    multiplication, and getting that wrong silently is a classic catalog bug.
    """
    if qty.unit is None:
        raise UnitParseError(f"cannot convert {qty.raw!r}: no unit in source text")
    ureg = registry()
    return ureg.Quantity(qty.magnitude, qty.unit).to(target_unit).magnitude


def dimensionality(unit: str) -> str:
    """The pint dimensionality string for a unit name, e.g. 'inch' -> '[length]'."""
    return str(registry().Unit(unit).dimensionality)


#: Readable names for the dimensionalities that appear in industrial attributes.
#: pint renders pressure as "[mass] / [length] / [time] ** 2", which is correct and
#: useless to the merchandiser who has to action the review.
_FRIENDLY_DIMENSIONS: dict[str, str] = {
    "[length]": "length",
    "[mass]": "mass",
    "[time]": "time",
    "[temperature]": "temperature",
    "[current]": "electric current",
    "[mass] / [length] / [time] ** 2": "pressure",
    "[length] ** 2 * [mass] / [time] ** 2": "torque or energy",
    "[length] * [mass] / [time] ** 2": "force",
    "[length] ** 2 * [mass] / [time] ** 3": "power",
    "[length] ** 3": "volume",
    "[length] ** 3 / [time]": "flow rate",
    "1 / [time]": "frequency",
    "[length] / [time]": "speed",
    "": "dimensionless",
}


def friendly_dimension(dim: str) -> str:
    """Render a dimensionality for a human, falling back to pint's form when unmapped."""
    return _FRIENDLY_DIMENSIONS.get(dim, dim)


@lru_cache(maxsize=256)
def abbreviate_unit(unit: str) -> str:
    """The compact symbol for a unit name: 'millimeter' -> 'mm'.

    Canonical unit names are what the schema stores, but a review queue showing
    "200 millimeter" reads like a machine wrote it. Unknown units pass through
    unchanged rather than raising, since display must never be a failure path.
    """
    try:
        return f"{registry().Unit(unit):~}"
    except Exception:  # noqa: BLE001 - formatting is best-effort by design
        return unit


# --------------------------------------------------------------------------------------
# Presentation: splitting a value into the two cells a delivery sheet wants
# --------------------------------------------------------------------------------------

#: Canonical unit name -> the symbol the delivery sheet writes. Distinct from
#: `abbreviate_unit`, which asks pint: pint renders inch as "in" but ampere as "A" and
#: degree_Celsius as "°C", and the sheet has its own conventions that are not pint's.
DISPLAY_UOM: dict[str, str] = {
    "inch": "in",
    "foot": "ft",
    "millimeter": "mm",
    "centimeter": "cm",
    "meter": "m",
    "pound": "lb",
    "ounce": "oz",
    "kilogram": "kg",
    "gram": "g",
    "psi": "PSI",
    "volt": "V",
    "ampere": "A",
    "milliampere": "mA",
    "watt": "W",
    "kilowatt": "kW",
    "horsepower": "HP",
    "hertz": "Hz",
    "ohm": "ohm",
    "kelvin": "K",
    "degC": "C",
    "degF": "F",
    "liter": "L",
    "milliliter": "mL",
    "foot ** 3": "CF",
    "inch ** 3": "CUIN",
    "gpm": "GPM",
    "cfm": "CFM",
}

#: Trade shorthand for correlated colour temperature. In lamp descriptions "27k" means
#: 2700 K, not 27 kilo-anything - "S21354 8W Led T9 Med 27k" is a 2700 K bulb. This is a
#: genuine unit collision: k is kelvin *and* the SI kilo prefix, and the naive reading
#: turns a warm-white lamp into a 27,000 K one that does not exist as a product.
#:
#: Kept as an explicit table beside the WOG entries rather than folded into a regex,
#: because it is a domain convention someone should be able to audit and correct, not a
#: parsing detail. Bounded at the range real lamps are sold in.
COLOUR_TEMPERATURE_SHORTHAND: dict[str, int] = {
    "20k": 2000,
    "22k": 2200,
    "24k": 2400,
    "25k": 2500,
    "27k": 2700,
    "30k": 3000,
    "35k": 3500,
    "40k": 4000,
    "41k": 4100,
    "45k": 4500,
    "50k": 5000,
    "57k": 5700,
    "60k": 6000,
    "65k": 6500,
}

#: Units the delivery sheet writes that pint has no dimension for. The reference sheet
#: has `ATTRIBUTE_VALUE 12 = 47` with `ATTRIBUTE_UOM 12 = dBA`, so the split has to know
#: dBA even though nothing can be dimensionally verified about it.
#:
#: This is a deliberate asymmetry, not an oversight: the presentation layer legitimately
#: recognises more unit tokens than the physics layer, because its job is to render what a
#: catalog says and the verifier's job is to check what can be checked. Adding these to
#: UNIT_ALIASES instead would make `parse_quantity` claim it understood a decibel-A
#: weighting, and the dimensional verifier would start reporting checks it never made.
DISPLAY_ONLY_UOM: dict[str, str] = {
    "dba": "dBA",
    "db": "dB",
    "lm": "lm",  # lumens
    "lumens": "lm",
    "cri": "CRI",
    "grit": "grit",
    "ah": "Ah",  # battery capacity: "8Ah"
    "rpm": "RPM",
    "spm": "SPM",  # strokes per minute, on reciprocating tools
    "ipm": "IPM",
    "kwh": "kW-hr",
    "kw-hr": "kW-hr",
    "gauge": "ga",
    "ga": "ga",
}

_CCT_SHORTHAND = re.compile(r"\b(\d{2})[kK]\b")

#: A magnitude with an optional trailing unit token. The magnitude alternation keeps the
#: mixed fraction first so "50-1/4" is not read as "50" followed by junk.
#: A magnitude with an optional trailing unit. The unit may be two words ("cu ft",
#: "cu. ft.") because several real units are written that way; without this the words stay
#: glued to the magnitude and a declared unit gets appended after them, printing the unit
#: twice.
_VALUE_UOM = re.compile(
    r"""^\s*
    (?P<magnitude>-?(?:\d+[\s-]\d+/\d+|\d+/\d+|\d*\.\d+|\d[\d,]*(?:\.\d+)?))
    \s*
    (?P<unit>"|'|[A-Za-z][A-Za-z.\-/]*(?:\s+[A-Za-z][A-Za-z.\-/]*)?)?
    \s*$""",
    re.VERBOSE,
)


def expand_colour_temperature(text: str) -> str | None:
    """Rewrite lamp shorthand as a real quantity: '27k' -> '2700 K'.

    Returns None when the text carries no recognised shorthand, so callers can tell
    "not applicable" from "expanded to something".
    """
    match = _CCT_SHORTHAND.search(text or "")
    if match is None:
        return None
    kelvin = COLOUR_TEMPERATURE_SHORTHAND.get(match.group(0).lower())
    if kelvin is None:
        return None
    return f"{kelvin} K"


def split_value_uom(text: str, spec: object | None = None) -> tuple[str, str | None]:
    """Split a written value into the sheet's ATTRIBUTE_VALUE and ATTRIBUTE_UOM cells.

    The magnitude is preserved **exactly as written**. The reference sheet writes
    `50-1/4` with unit `in`, not `50.25` - the mixed fraction is how the trade writes a
    dimension, and rewriting it as a decimal would be a silent editorial change to a
    customer-visible value that gains nothing. Only the unit token is canonicalised, and
    only to a display symbol.

    Values that are not a bare magnitude-plus-unit come back whole with no unit:
    "24 in W x 24-1/4 in D" is a composite the sheet also writes into a single cell, and
    inventing a unit for it would be worse than leaving the cell's UOM empty.

        >>> split_value_uom("50-1/4 in")
        ('50-1/4', 'in')
        >>> split_value_uom('1/2"')
        ('1/2', 'in')
        >>> split_value_uom("120V")
        ('120', 'V')
        >>> split_value_uom("5")
        ('5', None)
    """
    raw = clean_text(text or "").strip()
    if not raw:
        return "", None

    match = _VALUE_UOM.match(raw)
    if match is None:
        # Not a simple quantity ("24 in W x 24-1/4 in D"). Hand back the source text with
        # no unit: the text already carries whatever units it has, and adding the schema's
        # would duplicate them.
        return raw, None

    # Decimals in, fractions out. Manufacturers publish 0.5 and 50.25; trade buyers
    # search 1/2 and 50-1/4, and the client's guide requires the fraction form.
    magnitude = prefer_fraction(match.group("magnitude").strip())
    token = (match.group("unit") or "").strip()
    if not token:
        return magnitude, _declared_uom(spec)

    canonical = normalize_unit(token)
    if canonical is not None:
        return magnitude, DISPLAY_UOM.get(canonical, abbreviate_unit(canonical))

    display_only = DISPLAY_ONLY_UOM.get(token.casefold())
    if display_only is not None:
        return magnitude, display_only

    # An unrecognised trailing token is more likely part of the value than a unit
    # ("6pc", "3pk"), so it stays with the magnitude - and precisely because it stayed,
    # the schema's declared unit must NOT also be emitted. Doing both printed the unit
    # twice ("5.7 cu ft" + declared "CF" -> "5.7 cu ft CF").
    return raw, None


def _declared_uom(spec: object | None) -> str | None:
    """The schema's display unit, when the text itself carried none.

    Only ever a *declared* unit, never an inferred one. `normalize.py` once supplied a
    canonical unit for bare numbers and thereby committed the exact fault this system
    exists to catch; the difference here is that a schema author wrote this unit down.
    """
    return getattr(spec, "display_uom", None) if spec is not None else None


# --------------------------------------------------------------------------------------
# Decimal inches to trade fractions
# --------------------------------------------------------------------------------------

#: Denominators the trade actually writes, smallest first. A value that lands on 1/2 is
#: written 1/2, never 32/64 - so the search stops at the first exact hit.
_FRACTION_DENOMINATORS = (2, 4, 8, 16, 32, 64)

#: How close a decimal must sit to a sixty-fourth to be treated as that fraction. Tight
#: enough that 0.51 stays decimal rather than becoming 1/2, because a value that is not a
#: standard fraction is information - it may be a genuine metric part.
_FRACTION_TOLERANCE = 1e-4


def to_trade_fraction(value: float, tolerance: float = _FRACTION_TOLERANCE) -> str | None:
    """`0.5` -> `1/2`; `50.25` -> `50-1/4`; `0.51` -> None.

    Returns None when the decimal is not a standard sixty-fourth, because forcing it to
    the nearest one would silently change a dimension. A decimal that survives unconverted
    is a fact about the part, not a failure of this function.
    """
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return None

    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    whole = int(magnitude)
    remainder = magnitude - whole

    if remainder < tolerance:
        return f"{sign}{whole}"

    for denominator in _FRACTION_DENOMINATORS:
        numerator = remainder * denominator
        nearest = round(numerator)
        if nearest == 0 or nearest >= denominator:
            continue
        if abs(numerator - nearest) < tolerance * denominator:
            # Reduce, so 2/4 is written 1/2 even if 4 was reached first.
            divisor = _gcd(nearest, denominator)
            num, den = nearest // divisor, denominator // divisor
            # Mixed numbers take a hyphen in this trade: 50-1/4, not "50 1/4".
            return f"{sign}{whole}-{num}/{den}" if whole else f"{sign}{num}/{den}"

    return None


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def prefer_fraction(text: str) -> str:
    """Rewrite a decimal magnitude as a fraction where one exists, else leave it alone.

    Operates on the magnitude only; any unit or trailing text is preserved untouched.
    """
    stripped = (text or "").strip()
    if not stripped:
        return text

    match = re.match(r"^(-?\d+\.\d+)(.*)$", stripped)
    if match is None:
        return text

    try:
        value = float(match.group(1))
    except ValueError:
        return text

    fraction = to_trade_fraction(value)
    return f"{fraction}{match.group(2)}" if fraction else text
