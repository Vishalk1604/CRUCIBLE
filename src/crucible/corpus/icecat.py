"""Real product data from Open Icecat.

Everything measured so far runs on a generated corpus, and no amount of care in the
method fixes that: descriptions assembled from code tables are tidier, more consistent
and more complete than anything in a distributor's ERP. This is the route to an answer
key nobody in this project wrote.

What Icecat provides that the generator cannot
----------------------------------------------
Its datasheets carry attributes already split into a presentation value and a raw
magnitude - `Width` as `375 mm` alongside `375`, `Operating temperature` as
`10 - 32.5 °C`. So the units are real, the ranges are real, and crucially the *attribute
names and vocabulary* are someone else's. On the generated corpus the schema, the
descriptions and the answer key all descend from `corpus.tables`, which is what made the
rule extractor score a meaningless 100%. Here the schema is induced from what Icecat
actually publishes, so nothing downstream can agree with itself by construction.

Authentication
--------------
Token headers rather than a password, read from the environment. Open Icecat also accepts
a bare username on the URL, but tokens keep the credential out of request logs and out of
anything that might be pasted into a terminal.

Scope
-----
Only the free Open Icecat subset is reachable, and it is brand-sponsored, so coverage is
uneven across verticals. Whether the industrial categories have enough depth to calibrate
against is a question this module can answer but cannot fix.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://live.icecat.biz/api"
DEFAULT_CACHE = Path("data/raw/icecat")

#: Icecat asks for courtesy on the free tier. This is slow enough to be polite and fast
#: enough that a few hundred products is minutes rather than an evening.
REQUEST_INTERVAL = 0.35

#: Feature groups that describe the product rather than the packaging or the marketing.
#: Logistics data is excluded because carton dimensions are a fact about a box, and an
#: extractor asked for them from a product description would be right to refuse.
SKIP_GROUPS = {"packaging data", "logistics data", "packaging content", "other features"}


class IcecatError(RuntimeError):
    """A request failed in a way retrying will not fix."""


@dataclass
class IcecatFeature:
    """One published attribute."""

    name: str
    presentation: str
    raw: str
    group: str

    @property
    def usable(self) -> bool:
        """Whether this is worth asking an extractor to recover.

        Empty values and pure booleans are dropped. A `Yes`/`No` feature carries almost
        no information for verification - there is no unit to check, no constraint to
        violate, and a coin flip is right half the time - so including them would inflate
        accuracy without demonstrating anything.
        """
        if not self.presentation or not self.presentation.strip():
            return False
        return self.presentation.strip().lower() not in {"yes", "no", "true", "false", "-"}


@dataclass
class IcecatProduct:
    """A datasheet reduced to what this project needs."""

    icecat_id: int
    brand: str
    product_code: str
    title: str
    category: str
    features: list[IcecatFeature] = field(default_factory=list)

    @property
    def usable_features(self) -> list[IcecatFeature]:
        return [f for f in self.features if f.usable]

    def answer_key(self) -> dict[str, str]:
        """Attribute name to published value, as the gold standard."""
        return {f.name: f.presentation.strip() for f in self.usable_features}


def _credentials() -> tuple[str, dict[str, str]]:
    """Read username and token headers from the environment.

    Loaded from `.env` if present. Failing loudly here rather than letting requests
    return unauthorised means a misconfiguration is reported once, at the top, instead of
    as several hundred confusing 403s.
    """
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

    username = os.environ.get("ICECAT_USERNAME")
    api_token = os.environ.get("ICECAT_API_TOKEN")
    content_token = os.environ.get("ICECAT_CONTENT_TOKEN")

    if not username:
        raise IcecatError("ICECAT_USERNAME is not set; add it to .env")
    headers = {}
    if api_token:
        headers["api-token"] = api_token
    if content_token:
        headers["content-token"] = content_token
    return username, headers


def _parse(payload: dict[str, Any]) -> IcecatProduct:
    data = payload["data"]
    general = data.get("GeneralInfo") or {}

    features: list[IcecatFeature] = []
    for group in data.get("FeaturesGroups") or []:
        group_name = ((group.get("FeatureGroup") or {}).get("Name") or {}).get("Value") or ""
        if group_name.strip().lower() in SKIP_GROUPS:
            continue
        for feature in group.get("Features") or []:
            name = ((feature.get("Feature") or {}).get("Name") or {}).get("Value") or ""
            if not name:
                continue
            features.append(
                IcecatFeature(
                    name=name.strip(),
                    presentation=str(feature.get("PresentationValue") or "").strip(),
                    raw=str(feature.get("RawValue") or "").strip(),
                    group=group_name.strip(),
                )
            )

    category = ((general.get("Category") or {}).get("Name") or {}).get("Value") or ""
    brand = general.get("Brand") or general.get("BrandInfo", {}).get("BrandName") or ""

    return IcecatProduct(
        icecat_id=int(general.get("IcecatId") or 0),
        brand=str(brand),
        product_code=str(general.get("BrandPartCode") or general.get("ProductCode") or ""),
        title=str(general.get("Title") or ""),
        category=str(category),
        features=features,
    )


class IcecatClient:
    """Fetches datasheets, caching every response to disk.

    Caching is per product and permanent. Re-running an experiment must not depend on a
    third party still being reachable, and it must not re-spend someone else's bandwidth
    to obtain bytes already held.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        language: str = "en",
        timeout: float = 30.0,
    ) -> None:
        self.username, self.headers = _credentials()
        self.cache_dir = cache_dir or DEFAULT_CACHE
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.language = language
        self._client = httpx.Client(timeout=timeout, headers=self.headers)
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> IcecatClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _cache_path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        return self.cache_dir / f"{safe}.json"

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)
        self._last_request = time.monotonic()

    def fetch(self, brand: str, product_code: str) -> IcecatProduct | None:
        """One datasheet, or None if Open Icecat does not carry it.

        A missing product is an ordinary outcome on the free tier, not an error: the
        catalog is brand-sponsored and most of it is not free. Raising would turn a
        normal gap into a stopped run.
        """
        key = f"{brand}__{product_code}"
        path = self._cache_path(key)

        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                return _parse(cached) if cached.get("msg") == "OK" else None
            except Exception:
                logger.warning("discarding unreadable cache entry %s", path)

        self._throttle()
        try:
            response = self._client.get(
                API_URL,
                params={
                    "lang": self.language,
                    "shopname": self.username,
                    "Brand": brand,
                    "ProductCode": product_code,
                    "content": "",
                },
            )
        except httpx.HTTPError as exc:
            raise IcecatError(f"request failed for {brand} {product_code}: {exc}") from exc

        if response.status_code in (401, 403):
            raise IcecatError(
                f"Icecat rejected the credentials ({response.status_code}); "
                "check ICECAT_API_TOKEN and ICECAT_CONTENT_TOKEN in .env"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise IcecatError(f"non-JSON response for {brand} {product_code}") from exc

        path.write_text(json.dumps(payload), encoding="utf-8")

        if payload.get("msg") != "OK":
            return None
        return _parse(payload)

    def fetch_many(
        self, pairs: list[tuple[str, str]], progress_every: int = 25
    ) -> list[IcecatProduct]:
        """Fetch a batch, skipping what Open Icecat does not carry."""
        found: list[IcecatProduct] = []
        for i, (brand, code) in enumerate(pairs, start=1):
            try:
                product = self.fetch(brand, code)
            except IcecatError:
                logger.exception("giving up on %s %s", brand, code)
                continue
            if product is not None:
                found.append(product)
            if progress_every and i % progress_every == 0:
                logger.info("icecat: %d/%d requested, %d found", i, len(pairs), len(found))
        return found
