"""Minimal TripIt API client (connection test only)."""
from __future__ import annotations

import logging
from typing import Any

import requests
from requests_oauthlib import OAuth1

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.tripit.com/v1"


def test_connection(
    *,
    consumer_key: str,
    consumer_secret: str,
    access_token: str,
    access_token_secret: str,
) -> None:
    """Perform a minimal authenticated TripIt API call.

    Raises an exception if the request fails.
    """
    auth = OAuth1(
        client_key=consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=access_token,
        resource_owner_secret=access_token_secret,
    )

    url = f"{BASE_URL}/list/trip"
    params = {"max_results": 1}

    resp = requests.get(url, auth=auth, params=params, timeout=15)

    if resp.status_code != 200:
        _LOGGER.error(
            "TripIt connection test failed: %s %s",
            resp.status_code,
            resp.text[:300],
        )
        resp.raise_for_status()

    _LOGGER.info("TripIt connection test succeeded (HTTP 200)")
