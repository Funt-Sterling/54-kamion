"""Adapter skeleton for arabam.com's çekici (tractor-unit) listings.

STATUS: unverified skeleton, not a working scraper. This sandbox's network
egress is blocked for every classifieds domain tried, arabam.com included
(confirmed with a direct `curl https://www.arabam.com/robots.txt`, which
failed at the proxy level before reaching the site at all) — see
data_pipeline/README.md for the full note. None of the selectors below have
been checked against the live page.

Before running this for real, whoever has network access needs to:
  1. Open https://www.arabam.com/robots.txt and the site's terms of use —
     confirm scraping this section isn't prohibited. Don't assume it's fine
     just because it's Turkish and public: see docs/plan.md's note on
     sahibinden.com, which explicitly bans this and looks superficially
     similar from the outside.
  2. Open the çekici search results in a real browser, inspect the DOM
     (or the network tab — many of these sites' frontends call a JSON API
     that's far more stable to parse than HTML), and fill in the TODOs.
  3. Add a real delay between requests and a User-Agent that identifies
     this project honestly.

`fetch()` raises on purpose until this is filled in — a scraper that
silently returns nothing (or wrong data) is worse than one that fails
loudly, because a "0 comparables -> insufficient_market_data" result
downstream would look identical whether the site is genuinely thin on
inventory or the selectors are just wrong.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from ..schema import ListingRecord
from .base import SourceAdapter

LISTING_SEARCH_URL = "https://www.arabam.com/ikinci-el/ticari-arac/cekici"  # TODO(verify): confirm this path
USER_AGENT = "KamionInspectHackathonBot/0.1 (+contact: team email here)"


class ArabamCekiciAdapter(SourceAdapter):
    name = "arabam_cekici"

    def __init__(self, request_delay_seconds: float = 1.5, max_pages: int = 20):
        self.request_delay_seconds = request_delay_seconds
        self.max_pages = max_pages

    def fetch(self, limit: int | None = None) -> Iterator[ListingRecord]:
        raise NotImplementedError(
            "Unverified skeleton — see this module's docstring. Fill in "
            "_parse_listing_card()/_parse_listing_detail() against the real "
            "page structure once you have network access, then remove this "
            "guard."
        )
        # Intended shape once verified — left here so filling this in is a
        # matter of fixing selectors, not designing the adapter from scratch:
        #
        # import requests
        # from bs4 import BeautifulSoup
        #
        # yielded = 0
        # for page in range(1, self.max_pages + 1):
        #     resp = requests.get(
        #         LISTING_SEARCH_URL, params={"page": page}, headers={"User-Agent": USER_AGENT}, timeout=15
        #     )
        #     resp.raise_for_status()
        #     soup = BeautifulSoup(resp.text, "html.parser")
        #     cards = soup.select("TODO(verify): listing card selector")
        #     if not cards:
        #         break
        #     for card in cards:
        #         record = self._parse_listing_card(card)
        #         if record is not None:
        #             yield record
        #             yielded += 1
        #             if limit is not None and yielded >= limit:
        #                 return
        #     time.sleep(self.request_delay_seconds)

    def _parse_listing_card(self, card) -> ListingRecord | None:
        raise NotImplementedError("TODO(verify): fill in against the real DOM")
