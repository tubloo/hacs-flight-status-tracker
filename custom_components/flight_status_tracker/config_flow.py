"""Config flow for Flight Status Tracker."""
from __future__ import annotations

from collections.abc import Iterable
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv, selector

DOMAIN = "flight_status_tracker"

# Itinerary options
CONF_ITINERARY_PROVIDERS = "itinerary_providers"
CONF_DAYS_AHEAD = "days_ahead"
CONF_INCLUDE_PAST_HOURS = "include_past_hours"
CONF_MAX_FLIGHTS = "max_flights"
CONF_MERGE_TOLERANCE_HOURS = "merge_tolerance_hours"
CONF_AUTO_PRUNE_LANDED = "auto_prune_landed"
CONF_PRUNE_LANDED_HOURS = "prune_landed_hours"
CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES = "auto_remove_after_arrival_minutes"

# Status options
CONF_STATUS_PROVIDER = "status_provider"  # local|aviationstack|airlabs|opensky|flightradar24
CONF_POSITION_PROVIDER = "position_provider"  # same_as_status|flightradar24|opensky|airlabs|none
CONF_SCHEDULE_PROVIDER = "schedule_provider"  # auto|aviationstack|airlabs|flightradar24|mock
CONF_MIN_API_POLL_MINUTES = "min_api_poll_minutes"
CONF_DELAY_GRACE_MINUTES = "delay_grace_minutes"
CONF_FAR_BEFORE_DEP_THRESHOLD_HOURS = "far_before_dep_threshold_hours"
CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES = "far_before_dep_interval_minutes"
CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES = "prepare_to_travel_interval_minutes"
CONF_MID_BEFORE_DEP_THRESHOLD_HOURS = "mid_before_dep_threshold_hours"
CONF_MID_BEFORE_DEP_INTERVAL_MINUTES = "mid_before_dep_interval_minutes"
CONF_NEAR_BEFORE_DEP_INTERVAL_MINUTES = "near_before_dep_interval_minutes"
CONF_DEP_WINDOW_PRE_MINUTES = "dep_window_pre_minutes"
CONF_DEP_WINDOW_POST_MINUTES = "dep_window_post_minutes"
CONF_DEP_WINDOW_INTERVAL_MINUTES = "dep_window_interval_minutes"
CONF_MID_FLIGHT_INTERVAL_MINUTES = "mid_flight_interval_minutes"
CONF_ARR_WINDOW_PRE_MINUTES = "arr_window_pre_minutes"
CONF_ARR_WINDOW_POST_MINUTES = "arr_window_post_minutes"
CONF_ARR_WINDOW_INTERVAL_MINUTES = "arr_window_interval_minutes"
CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES = "stop_refresh_after_arrival_minutes"
CONF_AVIATIONSTACK_KEY = "aviationstack_access_key"
CONF_AIRLABS_KEY = "airlabs_api_key"
CONF_FLIGHTAPI_KEY = "flightapi_api_key"
CONF_OPENSKY_USERNAME = "opensky_username"
CONF_OPENSKY_PASSWORD = "opensky_password"
CONF_DIRECTORY_SOURCE_MODE = "directory_source_mode"

# NEW: Flightradar24 options
CONF_FR24_API_KEY = "fr24_api_key"
CONF_FR24_SANDBOX_KEY = "fr24_sandbox_key"
CONF_FR24_USE_SANDBOX = "fr24_use_sandbox"
CONF_FR24_API_VERSION = "fr24_api_version"

DEFAULT_ITINERARY_PROVIDERS = ["manual"]
DEFAULT_DAYS_AHEAD = 120
DEFAULT_INCLUDE_PAST_HOURS = 24
DEFAULT_MAX_FLIGHTS = 50
DEFAULT_MERGE_TOLERANCE_HOURS = 6
DEFAULT_AUTO_PRUNE_LANDED = True
DEFAULT_PRUNE_LANDED_HOURS = 1
DEFAULT_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES = 60

DEFAULT_STATUS_PROVIDER = "flightapi"
DEFAULT_POSITION_PROVIDER = "none"
DEFAULT_SCHEDULE_PROVIDER = "flightapi"
DEFAULT_MIN_API_POLL_MINUTES = 5
DEFAULT_DELAY_GRACE_MINUTES = 10
DEFAULT_FAR_BEFORE_DEP_THRESHOLD_HOURS = 6
DEFAULT_FAR_BEFORE_DEP_INTERVAL_MINUTES = 1440
DEFAULT_PREPARE_TO_TRAVEL_INTERVAL_MINUTES = 20
DEFAULT_MID_BEFORE_DEP_THRESHOLD_HOURS = 2
DEFAULT_MID_BEFORE_DEP_INTERVAL_MINUTES = 30
DEFAULT_NEAR_BEFORE_DEP_INTERVAL_MINUTES = 10
DEFAULT_DEP_WINDOW_PRE_MINUTES = 10
DEFAULT_DEP_WINDOW_POST_MINUTES = 10
DEFAULT_DEP_WINDOW_INTERVAL_MINUTES = 10
DEFAULT_MID_FLIGHT_INTERVAL_MINUTES = 30
DEFAULT_ARR_WINDOW_PRE_MINUTES = 10
DEFAULT_ARR_WINDOW_POST_MINUTES = 10
DEFAULT_ARR_WINDOW_INTERVAL_MINUTES = 15
DEFAULT_STOP_REFRESH_AFTER_ARRIVAL_MINUTES = 60

DEFAULT_FR24_USE_SANDBOX = False
DEFAULT_FR24_API_VERSION = "v1"
DEFAULT_DIRECTORY_SOURCE_MODE = "inbuilt"


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

    async def async_step_init(self, user_input=None) -> FlowResult:
        options = dict(self.config_entry.options)

        # Defaults / existing
        providers = options.get(CONF_ITINERARY_PROVIDERS, DEFAULT_ITINERARY_PROVIDERS)
        if isinstance(providers, str):
            providers = [providers]
        if not isinstance(providers, list):
            providers = list(DEFAULT_ITINERARY_PROVIDERS)
        days_ahead = options.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD)
        include_past = options.get(CONF_INCLUDE_PAST_HOURS, DEFAULT_INCLUDE_PAST_HOURS)
        max_flights = options.get(CONF_MAX_FLIGHTS, DEFAULT_MAX_FLIGHTS)
        tolerance = options.get(CONF_MERGE_TOLERANCE_HOURS, DEFAULT_MERGE_TOLERANCE_HOURS)
        auto_prune = options.get(CONF_AUTO_PRUNE_LANDED, DEFAULT_AUTO_PRUNE_LANDED)
        prune_hours = max(1, int(options.get(CONF_PRUNE_LANDED_HOURS, DEFAULT_PRUNE_LANDED_HOURS)))
        prune_minutes_default = int(options.get(CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES, prune_hours * 60))

        status_provider = options.get(CONF_STATUS_PROVIDER, DEFAULT_STATUS_PROVIDER)
        position_provider = options.get(CONF_POSITION_PROVIDER, DEFAULT_POSITION_PROVIDER)
        schedule_provider = options.get(CONF_SCHEDULE_PROVIDER, DEFAULT_SCHEDULE_PROVIDER)
        directory_source_mode = options.get(CONF_DIRECTORY_SOURCE_MODE, DEFAULT_DIRECTORY_SOURCE_MODE)
        min_poll = int(options.get(CONF_MIN_API_POLL_MINUTES, DEFAULT_MIN_API_POLL_MINUTES))
        grace = int(options.get(CONF_DELAY_GRACE_MINUTES, DEFAULT_DELAY_GRACE_MINUTES))

        far_thr = int(options.get(CONF_FAR_BEFORE_DEP_THRESHOLD_HOURS, DEFAULT_FAR_BEFORE_DEP_THRESHOLD_HOURS))
        far_int = int(options.get(CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES, DEFAULT_FAR_BEFORE_DEP_INTERVAL_MINUTES))
        prepare_int = int(
            options.get(
                CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES,
                options.get(
                    CONF_NEAR_BEFORE_DEP_INTERVAL_MINUTES,
                    options.get(
                        CONF_MID_BEFORE_DEP_INTERVAL_MINUTES,
                        DEFAULT_PREPARE_TO_TRAVEL_INTERVAL_MINUTES,
                    ),
                ),
            )
        )
        dep_pre = int(options.get(CONF_DEP_WINDOW_PRE_MINUTES, DEFAULT_DEP_WINDOW_PRE_MINUTES))
        dep_post = int(options.get(CONF_DEP_WINDOW_POST_MINUTES, DEFAULT_DEP_WINDOW_POST_MINUTES))
        dep_int = int(options.get(CONF_DEP_WINDOW_INTERVAL_MINUTES, DEFAULT_DEP_WINDOW_INTERVAL_MINUTES))
        mid_flight_int = int(options.get(CONF_MID_FLIGHT_INTERVAL_MINUTES, DEFAULT_MID_FLIGHT_INTERVAL_MINUTES))
        arr_pre = int(options.get(CONF_ARR_WINDOW_PRE_MINUTES, DEFAULT_ARR_WINDOW_PRE_MINUTES))
        arr_post = int(options.get(CONF_ARR_WINDOW_POST_MINUTES, DEFAULT_ARR_WINDOW_POST_MINUTES))
        arr_int = int(options.get(CONF_ARR_WINDOW_INTERVAL_MINUTES, DEFAULT_ARR_WINDOW_INTERVAL_MINUTES))
        stop_after_arr = int(
            options.get(CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES, DEFAULT_STOP_REFRESH_AFTER_ARRIVAL_MINUTES)
        )

        av_key = options.get(CONF_AVIATIONSTACK_KEY, "")
        al_key = options.get(CONF_AIRLABS_KEY, "")
        fa_key = options.get(CONF_FLIGHTAPI_KEY, "")
        os_user = options.get(CONF_OPENSKY_USERNAME, "")
        os_pass = options.get(CONF_OPENSKY_PASSWORD, "")

        fr24_key = options.get(CONF_FR24_API_KEY, "")
        fr24_sandbox_key = options.get(CONF_FR24_SANDBOX_KEY, "")
        fr24_sandbox = options.get(CONF_FR24_USE_SANDBOX, DEFAULT_FR24_USE_SANDBOX)
        fr24_version = options.get(CONF_FR24_API_VERSION, DEFAULT_FR24_API_VERSION)

        itinerary_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="manual", label="Manual"),
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
        directory_source_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="inbuilt", label="Inbuilt (OpenFlights/Airportsdata)"),
                    selector.SelectOptionDict(value="provider", label="Provider-first (FlightAPI, fallback to inbuilt)"),
                ],
                multiple=False,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

        number_minutes_5_1440 = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=5,
                max=1440,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="min",
            )
        )
        number_minutes_0_10080 = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=10080,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="min",
            )
        )
        number_minutes_5_120 = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=5,
                max=120,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="min",
            )
        )
        number_minutes_1_120 = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=120,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="min",
            )
        )
        number_minutes_5_240 = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=5,
                max=240,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="min",
            )
        )
        number_minutes_0_180 = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=180,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="min",
            )
        )
        number_minutes_small = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=60,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="min",
            )
        )
        number_hours_0_168 = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=168,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="h",
            )
        )
        number_hours_0_72 = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=72,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="h",
            )
        )
        number_hours_0_48 = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=48,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="h",
            )
        )
        number_days = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=365, step=1, mode=selector.NumberSelectorMode.SLIDER, unit_of_measurement="days")
        )
        number_flights = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=200, step=1, mode=selector.NumberSelectorMode.SLIDER)
        )

        schema_dict: dict[Any, Any] = {
            # Providers
            vol.Required(CONF_SCHEDULE_PROVIDER, default=schedule_provider): schedule_selector,
            vol.Required(CONF_STATUS_PROVIDER, default=status_provider): status_selector,
            vol.Required(CONF_POSITION_PROVIDER, default=position_provider): position_selector,
            vol.Required(CONF_DIRECTORY_SOURCE_MODE, default=directory_source_mode): directory_source_selector,
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

        # Polling schedule + rate limiting
        schema_dict.update(
            {
                vol.Required(CONF_MIN_API_POLL_MINUTES, default=min_poll): number_minutes_5_120,
                vol.Required(CONF_FAR_BEFORE_DEP_THRESHOLD_HOURS, default=far_thr): number_hours_0_168,
                vol.Required(CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES, default=far_int): number_minutes_5_1440,
                vol.Required(CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES, default=prepare_int): number_minutes_5_240,
                vol.Required(CONF_DEP_WINDOW_PRE_MINUTES, default=dep_pre): number_minutes_0_180,
                vol.Required(CONF_DEP_WINDOW_POST_MINUTES, default=dep_post): number_minutes_0_180,
                vol.Required(CONF_DEP_WINDOW_INTERVAL_MINUTES, default=dep_int): number_minutes_1_120,
                vol.Required(CONF_MID_FLIGHT_INTERVAL_MINUTES, default=mid_flight_int): number_minutes_5_240,
                vol.Required(CONF_ARR_WINDOW_PRE_MINUTES, default=arr_pre): number_minutes_0_180,
                vol.Required(CONF_ARR_WINDOW_POST_MINUTES, default=arr_post): number_minutes_0_180,
                vol.Required(CONF_ARR_WINDOW_INTERVAL_MINUTES, default=arr_int): number_minutes_1_120,
                vol.Required(CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES, default=stop_after_arr): number_minutes_0_10080,
            }
        )

        # Status interpretation
        schema_dict.update(
            {
                vol.Required(CONF_DELAY_GRACE_MINUTES, default=grace): number_minutes_small,
            }
        )

        # Flight list time window
        schema_dict.update(
            {
                vol.Required(CONF_INCLUDE_PAST_HOURS, default=include_past): number_hours_0_72,
                vol.Required(CONF_DAYS_AHEAD, default=days_ahead): number_days,
            }
        )

        # Pruning / cleanup
        schema_dict.update(
            {
                vol.Optional(CONF_AUTO_PRUNE_LANDED, default=auto_prune): bool,
                vol.Optional(CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES, default=prune_minutes_default): number_minutes_0_10080,
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

        if user_input is not None:
            errors: dict[str, str] = {}

            def _ival(key: str, default_val: int = 0) -> int:
                try:
                    return int(user_input.get(key, default_val))
                except Exception:
                    return default_val

            ttl_in = _ival(CONF_MIN_API_POLL_MINUTES, DEFAULT_MIN_API_POLL_MINUTES)
            if ttl_in < 5:
                errors[CONF_MIN_API_POLL_MINUTES] = "min_api_poll_too_low"

            arr_post_in = _ival(CONF_ARR_WINDOW_POST_MINUTES, DEFAULT_ARR_WINDOW_POST_MINUTES)
            stop_after_arr_in = _ival(
                CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES, DEFAULT_STOP_REFRESH_AFTER_ARRIVAL_MINUTES
            )
            if stop_after_arr_in < arr_post_in:
                errors[CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES] = "stop_before_arrival_window_end"

            # Intervals must not be below the minimum poll
            interval_fields: Iterable[str] = (
                CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES,
                CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES,
                CONF_MID_FLIGHT_INTERVAL_MINUTES,
            )
            for k in interval_fields:
                if _ival(k, 0) < max(5, ttl_in):
                    errors[k] = "interval_below_min_api_poll"

            if errors:
                return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

            # Itinerary
            options[CONF_ITINERARY_PROVIDERS] = user_input[CONF_ITINERARY_PROVIDERS]
            options[CONF_DAYS_AHEAD] = user_input[CONF_DAYS_AHEAD]
            options[CONF_INCLUDE_PAST_HOURS] = user_input[CONF_INCLUDE_PAST_HOURS]
            options[CONF_MAX_FLIGHTS] = user_input[CONF_MAX_FLIGHTS]
            options[CONF_MERGE_TOLERANCE_HOURS] = user_input[CONF_MERGE_TOLERANCE_HOURS]
            options[CONF_AUTO_PRUNE_LANDED] = bool(
                user_input.get(CONF_AUTO_PRUNE_LANDED, DEFAULT_AUTO_PRUNE_LANDED)
            )

            # Keep legacy hours option updated as a coarse compatibility hint (minutes is authoritative).
            prune_minutes_in = _ival(CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES, prune_minutes_default)
            options[CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES] = max(0, prune_minutes_in)
            if options[CONF_AUTO_PRUNE_LANDED] and prune_minutes_in > 0:
                options[CONF_PRUNE_LANDED_HOURS] = max(1, (prune_minutes_in + 59) // 60)
            else:
                options[CONF_PRUNE_LANDED_HOURS] = prune_hours

            # Status providers
            options[CONF_STATUS_PROVIDER] = user_input[CONF_STATUS_PROVIDER]
            options[CONF_POSITION_PROVIDER] = user_input.get(CONF_POSITION_PROVIDER, DEFAULT_POSITION_PROVIDER)
            options[CONF_SCHEDULE_PROVIDER] = user_input[CONF_SCHEDULE_PROVIDER]
            options[CONF_DIRECTORY_SOURCE_MODE] = user_input.get(CONF_DIRECTORY_SOURCE_MODE, DEFAULT_DIRECTORY_SOURCE_MODE)

            # Polling schedule
            options[CONF_MIN_API_POLL_MINUTES] = max(5, int(user_input[CONF_MIN_API_POLL_MINUTES]))
            options[CONF_FAR_BEFORE_DEP_THRESHOLD_HOURS] = int(user_input[CONF_FAR_BEFORE_DEP_THRESHOLD_HOURS])
            options[CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES] = int(user_input[CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES])
            options[CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES] = int(user_input[CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES])
            options.pop(CONF_MID_BEFORE_DEP_THRESHOLD_HOURS, None)
            options.pop(CONF_MID_BEFORE_DEP_INTERVAL_MINUTES, None)
            options.pop(CONF_NEAR_BEFORE_DEP_INTERVAL_MINUTES, None)
            options[CONF_DEP_WINDOW_PRE_MINUTES] = int(user_input[CONF_DEP_WINDOW_PRE_MINUTES])
            options[CONF_DEP_WINDOW_POST_MINUTES] = int(user_input[CONF_DEP_WINDOW_POST_MINUTES])
            options[CONF_DEP_WINDOW_INTERVAL_MINUTES] = max(1, int(user_input[CONF_DEP_WINDOW_INTERVAL_MINUTES]))
            options[CONF_MID_FLIGHT_INTERVAL_MINUTES] = int(user_input[CONF_MID_FLIGHT_INTERVAL_MINUTES])
            options[CONF_ARR_WINDOW_PRE_MINUTES] = int(user_input[CONF_ARR_WINDOW_PRE_MINUTES])
            options[CONF_ARR_WINDOW_POST_MINUTES] = int(user_input[CONF_ARR_WINDOW_POST_MINUTES])
            options[CONF_ARR_WINDOW_INTERVAL_MINUTES] = max(1, int(user_input[CONF_ARR_WINDOW_INTERVAL_MINUTES]))
            options[CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES] = int(user_input[CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES])

            # Status interpretation
            options[CONF_DELAY_GRACE_MINUTES] = user_input[CONF_DELAY_GRACE_MINUTES]

            # API keys
            options[CONF_AVIATIONSTACK_KEY] = user_input.get(CONF_AVIATIONSTACK_KEY, "").strip()
            options[CONF_AIRLABS_KEY] = user_input.get(CONF_AIRLABS_KEY, "").strip()
            options[CONF_FLIGHTAPI_KEY] = user_input.get(CONF_FLIGHTAPI_KEY, "").strip()
            options[CONF_OPENSKY_USERNAME] = user_input.get(CONF_OPENSKY_USERNAME, "").strip()
            options[CONF_OPENSKY_PASSWORD] = user_input.get(CONF_OPENSKY_PASSWORD, "").strip()

            # Flightradar24
            options[CONF_FR24_API_KEY] = user_input.get(CONF_FR24_API_KEY, "").strip()
            options[CONF_FR24_SANDBOX_KEY] = user_input.get(CONF_FR24_SANDBOX_KEY, "").strip()
            options[CONF_FR24_USE_SANDBOX] = bool(user_input.get(CONF_FR24_USE_SANDBOX, False))

            return self.async_create_entry(title="", data=options)

        return self.async_show_form(step_id="init", data_schema=schema)
