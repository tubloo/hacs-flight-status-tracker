"""TripIt OAuth1 (PIN / verifier) helper."""
from __future__ import annotations

from dataclasses import dataclass
import logging

from requests_oauthlib import OAuth1Session

_LOGGER = logging.getLogger(__name__)

REQUEST_TOKEN_URL = "https://api.tripit.com/oauth/request_token"
AUTHORIZE_URL = "https://www.tripit.com/oauth/authorize"
ACCESS_TOKEN_URL = "https://api.tripit.com/oauth/access_token"

# Out-of-band / PIN flow
OOB_CALLBACK = "oob"


@dataclass(frozen=True)
class TripItRequestToken:
    oauth_token: str
    oauth_token_secret: str
    authorize_url: str


@dataclass(frozen=True)
class TripItAccessToken:
    oauth_token: str
    oauth_token_secret: str


def get_request_token(consumer_key: str, consumer_secret: str) -> TripItRequestToken:
    """Start OAuth flow: obtain an unauthorized request token and authorize URL."""
    sess = OAuth1Session(
        client_key=consumer_key,
        client_secret=consumer_secret,
        callback_uri=OOB_CALLBACK,
    )
    tokens = sess.fetch_request_token(REQUEST_TOKEN_URL)
    oauth_token = tokens["oauth_token"]
    oauth_token_secret = tokens["oauth_token_secret"]

    # TripIt expects oauth_token; callback is already OOB
    authorize_url = sess.authorization_url(AUTHORIZE_URL)

    _LOGGER.info("Obtained TripIt request token (oauth_token=%s)", oauth_token)
    return TripItRequestToken(
        oauth_token=oauth_token,
        oauth_token_secret=oauth_token_secret,
        authorize_url=authorize_url,
    )


def exchange_for_access_token(
    *,
    consumer_key: str,
    consumer_secret: str,
    request_token: str,
    request_token_secret: str,
    verifier: str,
) -> TripItAccessToken:
    """Complete OAuth flow: exchange request token + verifier for access token."""
    sess = OAuth1Session(
        client_key=consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=request_token,
        resource_owner_secret=request_token_secret,
        verifier=verifier,
    )
    tokens = sess.fetch_access_token(ACCESS_TOKEN_URL)
    oauth_token = tokens["oauth_token"]
    oauth_token_secret = tokens["oauth_token_secret"]

    _LOGGER.info("Obtained TripIt access token (oauth_token=%s)", oauth_token)
    return TripItAccessToken(oauth_token=oauth_token, oauth_token_secret=oauth_token_secret)
