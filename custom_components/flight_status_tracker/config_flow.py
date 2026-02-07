"""Config flow for Flight Status Tracker."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv, selector

# TripIt imports can remain even if you don't use them right now
from .providers.tripit.client import test_connection
from .providers.tripit.oauth import (
    TripItRequestToken,
    exchange_for_access_token,
    get_request_token,
)

DOMAIN = "flight_status_tracker"

# Itinerary options
CONF_ITINERARY_PROVIDERS = "itinerary_providers"
CONF_DAYS_AHEAD = "days_ahead"
CONF_INCLUDE_PAST_HOURS = "include_past_hours"
CONF_MAX_FLIGHTS = "max_flights"
CONF_MERGE_TOLERANCE_HOURS = "merge_tolerance_hours"
CONF_AUTO_PRUNE_LANDED = "auto_prune_landed"
CONF_PRUNE_LANDED_HOURS = "prune_landed_hours"
CONF_CACHE_DIRECTORY = "cache_directory"
CONF_CACHE_TTL_DAYS = "cache_ttl_days"
CONF_DIRECTORY_AIRPORTS_URL = "directory_airports_url"
CONF_DIRECTORY_AIRLINES_URL = "directory_airlines_url"
CONF_DIRECTORY_SOURCE = "directory_source"

# Status options
CONF_STATUS_PROVIDER = "status_provider"  # local|aviationstack|airlabs|opensky|flightradar24
CONF_POSITION_PROVIDER = "position_provider"  # same_as_status|flightradar24|opensky|airlabs|none
CONF_SCHEDULE_PROVIDER = "schedule_provider"  # auto|aviationstack|airlabs|flightradar24|mock
CONF_STATUS_TTL_MINUTES = "status_ttl_minutes"
CONF_DELAY_GRACE_MINUTES = "delay_grace_minutes"
CONF_AVIATIONSTACK_KEY = "aviationstack_access_key"
CONF_AIRLABS_KEY = "airlabs_api_key"
CONF_FLIGHTAPI_KEY = "flightapi_api_key"
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
DEFAULT_DAYS_AHEAD = 120
DEFAULT_INCLUDE_PAST_HOURS = 24
DEFAULT_MAX_FLIGHTS = 50
DEFAULT_MERGE_TOLERANCE_HOURS = 6
DEFAULT_AUTO_PRUNE_LANDED = True
DEFAULT_PRUNE_LANDED_HOURS = 1
DEFAULT_CACHE_DIRECTORY = True
DEFAULT_CACHE_TTL_DAYS = 30
DEFAULT_DIRECTORY_AIRPORTS_URL = "https://github.com/mborsetti/airportsdata/raw/main/airportsdata/airports.csv"
DEFAULT_DIRECTORY_AIRLINES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
DEFAULT_DIRECTORY_SOURCE = "airportsdata"

DEFAULT_STATUS_PROVIDER = "flightapi"
DEFAULT_POSITION_PROVIDER = "none"
DEFAULT_SCHEDULE_PROVIDER = "flightapi"
DEFAULT_STATUS_TTL_MINUTES = 5
DEFAULT_DELAY_GRACE_MINUTES = 10

DEFAULT_FR24_USE_SANDBOX = False
DEFAULT_FR24_API_VERSION = "v1"


class FlightDashboardConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Flight Status Tracker", data={})
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
            # Some HA builds may omit unchecked optional booleans. Use the option-flow
            # defaults as the fallback.
            options[CONF_AUTO_PRUNE_LANDED] = bool(
                user_input.get(CONF_AUTO_PRUNE_LANDED, DEFAULT_AUTO_PRUNE_LANDED)
            )
            options[CONF_PRUNE_LANDED_HOURS] = max(
                1, int(user_input.get(CONF_PRUNE_LANDED_HOURS, DEFAULT_PRUNE_LANDED_HOURS))
            )
            options[CONF_CACHE_DIRECTORY] = bool(user_input.get(CONF_CACHE_DIRECTORY, DEFAULT_CACHE_DIRECTORY))
            options[CONF_CACHE_TTL_DAYS] = int(user_input.get(CONF_CACHE_TTL_DAYS, DEFAULT_CACHE_TTL_DAYS))
            options[CONF_DIRECTORY_AIRLINES_URL] = user_input.get(CONF_DIRECTORY_AIRLINES_URL, "").strip()
            options[CONF_DIRECTORY_SOURCE] = user_input.get(CONF_DIRECTORY_SOURCE, DEFAULT_DIRECTORY_SOURCE)

            # Status
            options[CONF_STATUS_PROVIDER] = user_input[CONF_STATUS_PROVIDER]
            options[CONF_POSITION_PROVIDER] = user_input.get(CONF_POSITION_PROVIDER, DEFAULT_POSITION_PROVIDER)
            options[CONF_SCHEDULE_PROVIDER] = user_input[CONF_SCHEDULE_PROVIDER]
            options[CONF_STATUS_TTL_MINUTES] = user_input[CONF_STATUS_TTL_MINUTES]
            options[CONF_DELAY_GRACE_MINUTES] = user_input[CONF_DELAY_GRACE_MINUTES]
            options[CONF_AVIATIONSTACK_KEY] = user_input.get(CONF_AVIATIONSTACK_KEY, "").strip()
            options[CONF_AIRLABS_KEY] = user_input.get(CONF_AIRLABS_KEY, "").strip()
            options[CONF_FLIGHTAPI_KEY] = user_input.get(CONF_FLIGHTAPI_KEY, "").strip()
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
        prune_hours = max(1, int(options.get(CONF_PRUNE_LANDED_HOURS, DEFAULT_PRUNE_LANDED_HOURS)))
        cache_dir = options.get(CONF_CACHE_DIRECTORY, DEFAULT_CACHE_DIRECTORY)
        cache_ttl_days = options.get(CONF_CACHE_TTL_DAYS, DEFAULT_CACHE_TTL_DAYS)
        directory_airlines_url = options.get(CONF_DIRECTORY_AIRLINES_URL, DEFAULT_DIRECTORY_AIRLINES_URL)
        directory_source = options.get(CONF_DIRECTORY_SOURCE, DEFAULT_DIRECTORY_SOURCE)

        status_provider = options.get(CONF_STATUS_PROVIDER, DEFAULT_STATUS_PROVIDER)
        position_provider = options.get(CONF_POSITION_PROVIDER, DEFAULT_POSITION_PROVIDER)
        schedule_provider = options.get(CONF_SCHEDULE_PROVIDER, DEFAULT_SCHEDULE_PROVIDER)
        ttl = options.get(CONF_STATUS_TTL_MINUTES, DEFAULT_STATUS_TTL_MINUTES)
        grace = options.get(CONF_DELAY_GRACE_MINUTES, DEFAULT_DELAY_GRACE_MINUTES)
        av_key = options.get(CONF_AVIATIONSTACK_KEY, "")
        al_key = options.get(CONF_AIRLABS_KEY, "")
        fa_key = options.get(CONF_FLIGHTAPI_KEY, "")
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
                    selector.SelectOptionDict(value="flightapi", label="FlightAPI.io"),
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
                    selector.SelectOptionDict(value="flightapi", label="FlightAPI.io"),
                    selector.SelectOptionDict(value="opensky", label="OpenSky"),
                    selector.SelectOptionDict(value="flightradar24", label="Flightradar24"),
                    selector.SelectOptionDict(value="mock", label="Mock"),
                ],
                multiple=False,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        position_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="same_as_status", label="Same as status provider"),
                    selector.SelectOptionDict(value="flightradar24", label="Flightradar24"),
                    selector.SelectOptionDict(value="opensky", label="OpenSky"),
                    selector.SelectOptionDict(value="airlabs", label="AirLabs"),
                    selector.SelectOptionDict(value="none", label="Disabled"),
                ],
                multiple=False,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

        number_minutes = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=120, step=1, mode=selector.NumberSelectorMode.SLIDER, unit_of_measurement="min")
        )
        number_minutes_small = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=60, step=1, mode=selector.NumberSelectorMode.SLIDER, unit_of_measurement="min")
        )
        number_hours_0_72 = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=72, step=1, mode=selector.NumberSelectorMode.SLIDER, unit_of_measurement="h")
        )
        number_hours_0_168 = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=168, step=1, mode=selector.NumberSelectorMode.SLIDER, unit_of_measurement="h")
        )
        number_hours_1_168 = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=168, step=1, mode=selector.NumberSelectorMode.SLIDER, unit_of_measurement="h")
        )
        number_hours_0_48 = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=48, step=1, mode=selector.NumberSelectorMode.SLIDER, unit_of_measurement="h")
        )
        number_days = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=365, step=1, mode=selector.NumberSelectorMode.SLIDER, unit_of_measurement="days")
        )
        number_days_ttl = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=3650, step=1, mode=selector.NumberSelectorMode.SLIDER, unit_of_measurement="days")
        )
        number_flights = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=200, step=1, mode=selector.NumberSelectorMode.SLIDER)
        )

        directory_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="auto", label="Auto (best available)"),
                    selector.SelectOptionDict(value="aviationstack", label="Aviationstack"),
                    selector.SelectOptionDict(value="airlabs", label="AirLabs"),
                    selector.SelectOptionDict(value="fr24", label="Flightradar24"),
                    selector.SelectOptionDict(value="airportsdata", label="Airportsdata (CSV)"),
                    selector.SelectOptionDict(value="openflights", label="OpenFlights (airports.dat)"),
                ],
                multiple=False,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

        schema_dict: dict[Any, Any] = {
            # Providers
            vol.Required(CONF_SCHEDULE_PROVIDER, default=schedule_provider): schedule_selector,
            vol.Required(CONF_STATUS_PROVIDER, default=status_provider): status_selector,
            vol.Required(CONF_POSITION_PROVIDER, default=position_provider): position_selector,
            vol.Required(CONF_ITINERARY_PROVIDERS, default=providers): itinerary_selector,

            # API keys
            vol.Optional(CONF_FLIGHTAPI_KEY, default=fa_key): str,
            vol.Optional(CONF_FR24_API_KEY, default=fr24_key): str,
            vol.Optional(CONF_FR24_SANDBOX_KEY, default=fr24_sandbox_key): str,
            vol.Optional(CONF_FR24_USE_SANDBOX, default=fr24_sandbox): bool,
            vol.Optional(CONF_AVIATIONSTACK_KEY, default=av_key): str,
            vol.Optional(CONF_AIRLABS_KEY, default=al_key): str,
            vol.Optional(CONF_OPENSKY_USERNAME, default=os_user): str,
            vol.Optional(CONF_OPENSKY_PASSWORD, default=os_pass): str,
        }

        # TripIt (only show if selected)
        if "tripit" in providers:
            schema_dict.update(
                {
                    vol.Optional(CONF_TRIPIT_CONSUMER_KEY, default=tripit_key): str,
                    vol.Optional(CONF_TRIPIT_CONSUMER_SECRET, default=tripit_secret): str,
                    vol.Optional(CONF_TRIPIT_AUTHORIZE_NOW, default=False): bool,
                }
            )

        # Refresh & rate limits
        schema_dict.update(
            {
                vol.Required(CONF_STATUS_TTL_MINUTES, default=ttl): number_minutes,
                vol.Required(CONF_DELAY_GRACE_MINUTES, default=grace): number_minutes_small,
                vol.Required(CONF_INCLUDE_PAST_HOURS, default=include_past): number_hours_0_72,
                vol.Required(CONF_DAYS_AHEAD, default=days_ahead): number_days,
            }
        )

        # Data & caching
        schema_dict.update(
            {
                vol.Optional(CONF_CACHE_DIRECTORY, default=cache_dir): bool,
                vol.Optional(CONF_CACHE_TTL_DAYS, default=cache_ttl_days): number_days_ttl,
                vol.Optional(CONF_DIRECTORY_SOURCE, default=directory_source): directory_selector,
                vol.Optional(CONF_DIRECTORY_AIRLINES_URL, default=directory_airlines_url): str,
            }
        )

        # Pruning / cleanup
        schema_dict.update(
            {
                vol.Optional(CONF_AUTO_PRUNE_LANDED, default=auto_prune): bool,
                vol.Optional(CONF_PRUNE_LANDED_HOURS, default=prune_hours): number_hours_1_168,
            }
        )

        # Advanced list behavior
        schema_dict.update(
            {
                vol.Required(CONF_MAX_FLIGHTS, default=max_flights): number_flights,
                vol.Required(CONF_MERGE_TOLERANCE_HOURS, default=tolerance): number_hours_0_48,
            }
        )

        schema = vol.Schema(schema_dict)

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
                return self.async_show_form(
                    step_id="tripit_verifier",
                    data_schema=vol.Schema({vol.Required(CONF_TRIPIT_VERIFIER): str}),
                    errors={"base": "tripit_connection_test_failed"},
                )

            self._pending_options[CONF_TRIPIT_ACCESS_TOKEN] = access.oauth_token
            self._pending_options[CONF_TRIPIT_ACCESS_TOKEN_SECRET] = access.oauth_token_secret

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

        return self.async_show_form(
            step_id="tripit_verifier",
            data_schema=vol.Schema({vol.Required(CONF_TRIPIT_VERIFIER): str}),
        )
