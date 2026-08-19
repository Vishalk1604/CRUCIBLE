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


def clean_text(text: str) -> str:
    """Fold the unicode and typographic noise that survives OCR and copy-paste.

    Substitutions run *before* NFKC normalization, not after. NFKC decomposes the double
    prime used for inches into two apostrophes, which would destroy the distinction
    between inches and feet before we ever got to look at it.
    """
    for bad, good in _CHAR_FIXES.items():
        text = text.replace(bad, good)
    return unicodedata.normalize("NFKC", text).strip()


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
