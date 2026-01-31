"""Config flow for Flight Dashboard."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv, selector

# TripIt imports can remain even if you don't use them right now
from .tripit_client import test_connection
from .tripit_oauth import (
    TripItRequestToken,
    exchange_for_access_token,
    get_request_token,
)

DOMAIN = "flight_dashboard"

# Itinerary options
CONF_ITINERARY_PROVIDERS = "itinerary_providers"
CONF_DAYS_AHEAD = "days_ahead"
CONF_INCLUDE_PAST_HOURS = "include_past_hours"
CONF_MAX_FLIGHTS = "max_flights"
CONF_MERGE_TOLERANCE_HOURS = "merge_tolerance_hours"
CONF_AUTO_PRUNE_LANDED = "auto_prune_landed"
CONF_PRUNE_LANDED_HOURS = "prune_landed_hours"

# Status options
CONF_STATUS_PROVIDER = "status_provider"  # local|aviationstack|airlabs|opensky|flightradar24
CONF_SCHEDULE_PROVIDER = "schedule_provider"  # auto|aviationstack|airlabs|flightradar24|mock
CONF_STATUS_TTL_MINUTES = "status_ttl_minutes"
CONF_AVIATIONSTACK_KEY = "aviationstack_access_key"
CONF_AIRLABS_KEY = "airlabs_api_key"
CONF_OPENSKY_USERNAME = "opensky_username"
CONF_OPENSKY_PASSWORD = "opensky_password"

# NEW: Flightradar24 options
CONF_FR24_API_KEY = "fr24_api_key"
CONF_FR24_SANDBOX_KEY = "fr24_sandbox_key"
CONF_FR24_USE_SANDBOX = "fr24_use_sandbox"
CONF_FR24_API_VERSION = "fr24_api_version"

# TripIt options
CONF_TRIPIT_CONSUMER_KEY = "tripit_consumer_key"
CONF_TRIPIT_CONSUMER_SECRET = "tripit_consumer_secret"
CONF_TRIPIT_ACCESS_TOKEN = "tripit_access_token"
CONF_TRIPIT_ACCESS_TOKEN_SECRET = "tripit_access_token_secret"

# UI-only
CONF_TRIPIT_AUTHORIZE_NOW = "tripit_authorize_now"
CONF_TRIPIT_VERIFIER = "tripit_verifier"

DEFAULT_ITINERARY_PROVIDERS = ["manual"]
DEFAULT_DAYS_AHEAD = 30
DEFAULT_INCLUDE_PAST_HOURS = 6
DEFAULT_MAX_FLIGHTS = 50
DEFAULT_MERGE_TOLERANCE_HOURS = 6
DEFAULT_AUTO_PRUNE_LANDED = False
DEFAULT_PRUNE_LANDED_HOURS = 0

DEFAULT_STATUS_PROVIDER = "flightradar24"
DEFAULT_SCHEDULE_PROVIDER = "auto"
DEFAULT_STATUS_TTL_MINUTES = 5

DEFAULT_FR24_USE_SANDBOX = False
DEFAULT_FR24_API_VERSION = "v1"


class FlightDashboardConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Flight Dashboard", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return FlightDashboardOptionsFlowHandler(config_entry)


class FlightDashboardOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow (compatible with your HA build)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._pending_options: dict = {}
        self._tripit_request_token: TripItRequestToken | None = None

    async def async_step_init(self, user_input=None) -> FlowResult:
        options = dict(self.config_entry.options)

        if user_input is not None:
            # Itinerary
            options[CONF_ITINERARY_PROVIDERS] = user_input[CONF_ITINERARY_PROVIDERS]
            options[CONF_DAYS_AHEAD] = user_input[CONF_DAYS_AHEAD]
            options[CONF_INCLUDE_PAST_HOURS] = user_input[CONF_INCLUDE_PAST_HOURS]
            options[CONF_MAX_FLIGHTS] = user_input[CONF_MAX_FLIGHTS]
            options[CONF_MERGE_TOLERANCE_HOURS] = user_input[CONF_MERGE_TOLERANCE_HOURS]
            options[CONF_AUTO_PRUNE_LANDED] = bool(user_input.get(CONF_AUTO_PRUNE_LANDED, False))
            options[CONF_PRUNE_LANDED_HOURS] = int(user_input.get(CONF_PRUNE_LANDED_HOURS, 0))

            # Status
            options[CONF_STATUS_PROVIDER] = user_input[CONF_STATUS_PROVIDER]
            options[CONF_SCHEDULE_PROVIDER] = user_input[CONF_SCHEDULE_PROVIDER]
            options[CONF_STATUS_TTL_MINUTES] = user_input[CONF_STATUS_TTL_MINUTES]
            options[CONF_AVIATIONSTACK_KEY] = user_input.get(CONF_AVIATIONSTACK_KEY, "").strip()
            options[CONF_AIRLABS_KEY] = user_input.get(CONF_AIRLABS_KEY, "").strip()
            options[CONF_OPENSKY_USERNAME] = user_input.get(CONF_OPENSKY_USERNAME, "").strip()
            options[CONF_OPENSKY_PASSWORD] = user_input.get(CONF_OPENSKY_PASSWORD, "").strip()

            # Flightradar24
            options[CONF_FR24_API_KEY] = user_input.get(CONF_FR24_API_KEY, "").strip()
            options[CONF_FR24_SANDBOX_KEY] = user_input.get(CONF_FR24_SANDBOX_KEY, "").strip()
            options[CONF_FR24_USE_SANDBOX] = bool(user_input.get(CONF_FR24_USE_SANDBOX, False))

            # TripIt
            options[CONF_TRIPIT_CONSUMER_KEY] = user_input.get(CONF_TRIPIT_CONSUMER_KEY, "").strip()
            options[CONF_TRIPIT_CONSUMER_SECRET] = user_input.get(CONF_TRIPIT_CONSUMER_SECRET, "").strip()

            authorize_now = bool(user_input.get(CONF_TRIPIT_AUTHORIZE_NOW, False))
            if authorize_now and "tripit" in options.get(CONF_ITINERARY_PROVIDERS, []):
                self._pending_options = options
                return await self.async_step_tripit_verifier()

            return self.async_create_entry(title="", data=options)

        # Defaults / existing
        providers = options.get(CONF_ITINERARY_PROVIDERS, DEFAULT_ITINERARY_PROVIDERS)
        days_ahead = options.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD)
        include_past = options.get(CONF_INCLUDE_PAST_HOURS, DEFAULT_INCLUDE_PAST_HOURS)
        max_flights = options.get(CONF_MAX_FLIGHTS, DEFAULT_MAX_FLIGHTS)
        tolerance = options.get(CONF_MERGE_TOLERANCE_HOURS, DEFAULT_MERGE_TOLERANCE_HOURS)
        auto_prune = options.get(CONF_AUTO_PRUNE_LANDED, DEFAULT_AUTO_PRUNE_LANDED)
        prune_hours = options.get(CONF_PRUNE_LANDED_HOURS, DEFAULT_PRUNE_LANDED_HOURS)

        status_provider = options.get(CONF_STATUS_PROVIDER, DEFAULT_STATUS_PROVIDER)
        schedule_provider = options.get(CONF_SCHEDULE_PROVIDER, DEFAULT_SCHEDULE_PROVIDER)
        ttl = options.get(CONF_STATUS_TTL_MINUTES, DEFAULT_STATUS_TTL_MINUTES)
        av_key = options.get(CONF_AVIATIONSTACK_KEY, "")
        al_key = options.get(CONF_AIRLABS_KEY, "")
        os_user = options.get(CONF_OPENSKY_USERNAME, "")
        os_pass = options.get(CONF_OPENSKY_PASSWORD, "")

        fr24_key = options.get(CONF_FR24_API_KEY, "")
        fr24_sandbox_key = options.get(CONF_FR24_SANDBOX_KEY, "")
        fr24_sandbox = options.get(CONF_FR24_USE_SANDBOX, DEFAULT_FR24_USE_SANDBOX)
        fr24_version = options.get(CONF_FR24_API_VERSION, DEFAULT_FR24_API_VERSION)

        tripit_key = options.get(CONF_TRIPIT_CONSUMER_KEY, "")
        tripit_secret = options.get(CONF_TRIPIT_CONSUMER_SECRET, "")

        itinerary_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="manual", label="Manual"),
                    selector.SelectOptionDict(value="tripit", label="TripIt (optional)"),
                ],
                multiple=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        schedule_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="auto", label="Auto (best available)"),
                    selector.SelectOptionDict(value="aviationstack", label="Aviationstack"),
                    selector.SelectOptionDict(value="airlabs", label="AirLabs"),
                    selector.SelectOptionDict(value="flightradar24", label="Flightradar24"),
                    selector.SelectOptionDict(value="mock", label="Mock"),
                ],
                multiple=False,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        status_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="local", label="Local (no API)"),
                    selector.SelectOptionDict(value="aviationstack", label="Aviationstack"),
                    selector.SelectOptionDict(value="airlabs", label="AirLabs"),
                    selector.SelectOptionDict(value="opensky", label="OpenSky"),
                    selector.SelectOptionDict(value="flightradar24", label="Flightradar24"),
                    selector.SelectOptionDict(value="mock", label="Mock"),
                ],
                multiple=False,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

        schema = vol.Schema(
            {
                # Itinerary providers
                vol.Required(CONF_ITINERARY_PROVIDERS, default=providers): itinerary_selector,
                vol.Optional(CONF_TRIPIT_CONSUMER_KEY, default=tripit_key): str,
                vol.Optional(CONF_TRIPIT_CONSUMER_SECRET, default=tripit_secret): str,
                vol.Optional(CONF_TRIPIT_AUTHORIZE_NOW, default=False): bool,

                # Schedule lookup provider
                vol.Required(CONF_SCHEDULE_PROVIDER, default=schedule_provider): schedule_selector,
                vol.Optional(CONF_AVIATIONSTACK_KEY, default=av_key): str,
                vol.Optional(CONF_AIRLABS_KEY, default=al_key): str,

                # Status provider
                vol.Required(CONF_STATUS_PROVIDER, default=status_provider): status_selector,
                vol.Required(CONF_STATUS_TTL_MINUTES, default=ttl): vol.All(int, vol.Clamp(min=1, max=120)),
                vol.Optional(CONF_FR24_API_KEY, default=fr24_key): str,
                vol.Optional(CONF_FR24_SANDBOX_KEY, default=fr24_sandbox_key): str,
                vol.Optional(CONF_FR24_USE_SANDBOX, default=fr24_sandbox): bool,
                vol.Optional(CONF_FR24_API_VERSION, default=fr24_version): str,
                vol.Optional(CONF_OPENSKY_USERNAME, default=os_user): str,
                vol.Optional(CONF_OPENSKY_PASSWORD, default=os_pass): str,

                # List behavior
                vol.Required(CONF_DAYS_AHEAD, default=days_ahead): vol.All(int, vol.Clamp(min=1, max=365)),
                vol.Required(CONF_INCLUDE_PAST_HOURS, default=include_past): vol.All(int, vol.Clamp(min=0, max=72)),
                vol.Required(CONF_MAX_FLIGHTS, default=max_flights): vol.All(int, vol.Clamp(min=1, max=200)),
                vol.Required(CONF_MERGE_TOLERANCE_HOURS, default=tolerance): vol.All(int, vol.Clamp(min=0, max=48)),
                vol.Optional(CONF_AUTO_PRUNE_LANDED, default=auto_prune): bool,
                vol.Optional(CONF_PRUNE_LANDED_HOURS, default=prune_hours): vol.All(int, vol.Clamp(min=0, max=168)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_tripit_verifier(self, user_input=None) -> FlowResult:
        # unchanged; can keep in place even if you never use TripIt
        if user_input is not None:
            verifier = user_input[CONF_TRIPIT_VERIFIER].strip()
            key = self._pending_options.get(CONF_TRIPIT_CONSUMER_KEY, "")
            secret = self._pending_options.get(CONF_TRIPIT_CONSUMER_SECRET, "")

            if not self._tripit_request_token:
                return self.async_abort(reason="tripit_no_request_token")

            def _exchange():
                return exchange_for_access_token(
                    consumer_key=key,
                    consumer_secret=secret,
                    request_token=self._tripit_request_token.oauth_token,
                    request_token_secret=self._tripit_request_token.oauth_token_secret,
                    verifier=verifier,
                )

            try:
                access = await self.hass.async_add_executor_job(_exchange)
            except Exception:
                return self.async_show_form(
                    step_id="tripit_verifier",
                    data_schema=vol.Schema({vol.Required(CONF_TRIPIT_VERIFIER): str}),
                    errors={"base": "tripit_access_token_failed"},
                )

            def _test():
                test_connection(
                    consumer_key=key,
                    consumer_secret=secret,
                    access_token=access.oauth_token,
                    access_token_secret=access.oauth_token_secret,
                )

            try:
                await self.hass.async_add_executor_job(_test)
            except Exception:
                self.hass.components.persistent_notification.create(
                    title="TripIt connection test failed",
                    message="TripIt authorization succeeded, but the connection test failed. Try again.",
                    notification_id="flight_dashboard_tripit_oauth",
                )
                return self.async_show_form(
                    step_id="tripit_verifier",
                    data_schema=vol.Schema({vol.Required(CONF_TRIPIT_VERIFIER): str}),
                    errors={"base": "tripit_connection_test_failed"},
                )

            self._pending_options[CONF_TRIPIT_ACCESS_TOKEN] = access.oauth_token
            self._pending_options[CONF_TRIPIT_ACCESS_TOKEN_SECRET] = access.oauth_token_secret

            self.hass.components.persistent_notification.create(
                title="TripIt connected",
                message="✅ TripIt authorization succeeded and connection test passed.",
                notification_id="flight_dashboard_tripit_oauth",
            )

            return self.async_create_entry(title="", data=self._pending_options)

        key = self._pending_options.get(CONF_TRIPIT_CONSUMER_KEY, "")
        secret = self._pending_options.get(CONF_TRIPIT_CONSUMER_SECRET, "")

        if not key or not secret:
            return self.async_abort(reason="tripit_missing_consumer_keys")

        def _request():
            return get_request_token(key, secret)

        try:
            self._tripit_request_token = await self.hass.async_add_executor_job(_request)
        except Exception:
            return self.async_show_form(
                step_id="tripit_verifier",
                data_schema=vol.Schema({vol.Required(CONF_TRIPIT_VERIFIER): str}),
                errors={"base": "tripit_request_token_failed"},
            )

        self.hass.components.persistent_notification.create(
            title="TripIt authorization required",
            message=(
                "1) Open this URL and approve access in TripIt:\n\n"
                f"{self._tripit_request_token.authorize_url}\n\n"
                "2) TripIt will show a PIN / verifier.\n"
                "3) Return to Home Assistant and paste it into the verifier field."
            ),
            notification_id="flight_dashboard_tripit_oauth",
        )

        return self.async_show_form(
            step_id="tripit_verifier",
            data_schema=vol.Schema({vol.Required(CONF_TRIPIT_VERIFIER): str}),
        )
