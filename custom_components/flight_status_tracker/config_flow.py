"""Config flow for Flight Status Tracker."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

DOMAIN = "flight_status_tracker"

# Itinerary options
CONF_ITINERARY_PROVIDERS = "itinerary_providers"
CONF_DAYS_AHEAD = "days_ahead"
CONF_INCLUDE_PAST_HOURS = "include_past_hours"
CONF_MAX_FLIGHTS = "max_flights"
CONF_AUTO_PRUNE_LANDED = "auto_prune_landed"
CONF_PRUNE_LANDED_HOURS = "prune_landed_hours"
CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES = "auto_remove_after_arrival_minutes"

# Status options
CONF_STATUS_PROVIDER = "status_provider"  # aerodatabox|flightapi
CONF_POSITION_PROVIDER = "position_provider"  # same_as_status|aerodatabox|none
CONF_SCHEDULE_PROVIDER = "schedule_provider"  # aerodatabox|flightapi
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
CONF_FLIGHTAPI_KEY = "flightapi_api_key"
CONF_DIRECTORY_SOURCE_MODE = "directory_source_mode"
CONF_AERODATABOX_GATEWAY = "aerodatabox_gateway"  # rapidapi|apimarket
CONF_AERODATABOX_RAPIDAPI_KEY = "aerodatabox_rapidapi_key"
CONF_AERODATABOX_APIMARKET_KEY = "aerodatabox_apimarket_key"

# Wizard-only fields (not persisted directly)
CONF_PRIMARY_PROVIDER = "primary_provider"
CONF_ENABLE_POSITION = "enable_position"

DEFAULT_ITINERARY_PROVIDERS = ["manual"]
DEFAULT_DAYS_AHEAD = 120
DEFAULT_INCLUDE_PAST_HOURS = 24
DEFAULT_MAX_FLIGHTS = 50
DEFAULT_AUTO_PRUNE_LANDED = True
DEFAULT_PRUNE_LANDED_HOURS = 1
DEFAULT_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES = 60

DEFAULT_STATUS_PROVIDER = "aerodatabox"
DEFAULT_POSITION_PROVIDER = "none"
DEFAULT_SCHEDULE_PROVIDER = "aerodatabox"
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

DEFAULT_DIRECTORY_SOURCE_MODE = "provider"
DEFAULT_AERODATABOX_GATEWAY = "rapidapi"

SINGLE_PROVIDER_OPTIONS: tuple[str, ...] = (
    "aerodatabox",
    "flightapi",
)
EXTERNAL_PROVIDER_OPTIONS: tuple[str, ...] = (
    "aerodatabox",
    "flightapi",
)
SCHEDULE_CAPABLE_PROVIDER_OPTIONS: tuple[str, ...] = (
    "aerodatabox",
    "flightapi",
)
POSITION_CAPABLE_PROVIDER_OPTIONS: tuple[str, ...] = (
    "aerodatabox",
)


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
    """Step-based options wizard."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._pending_options: dict[str, Any] = {}

    def _defaults_from_existing(self) -> dict[str, Any]:
        options = dict(self.config_entry.options)

        schedule_provider = str(options.get(CONF_SCHEDULE_PROVIDER, DEFAULT_SCHEDULE_PROVIDER)).strip().lower()
        status_provider = str(options.get(CONF_STATUS_PROVIDER, DEFAULT_STATUS_PROVIDER)).strip().lower()
        position_provider = str(options.get(CONF_POSITION_PROVIDER, DEFAULT_POSITION_PROVIDER)).strip().lower()

        primary_provider = schedule_provider if schedule_provider in SINGLE_PROVIDER_OPTIONS else DEFAULT_SCHEDULE_PROVIDER
        enable_position = position_provider == primary_provider and primary_provider in POSITION_CAPABLE_PROVIDER_OPTIONS

        prune_hours = max(1, int(options.get(CONF_PRUNE_LANDED_HOURS, DEFAULT_PRUNE_LANDED_HOURS)))
        prune_minutes_default = int(options.get(CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES, prune_hours * 60))

        return {
            CONF_PRIMARY_PROVIDER: primary_provider,
            CONF_ENABLE_POSITION: bool(enable_position),
            CONF_SCHEDULE_PROVIDER: schedule_provider,
            CONF_STATUS_PROVIDER: status_provider,
            CONF_POSITION_PROVIDER: position_provider,
            CONF_ITINERARY_PROVIDERS: ["manual"],
            CONF_DAYS_AHEAD: int(options.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD)),
            CONF_INCLUDE_PAST_HOURS: int(options.get(CONF_INCLUDE_PAST_HOURS, DEFAULT_INCLUDE_PAST_HOURS)),
            CONF_MAX_FLIGHTS: int(options.get(CONF_MAX_FLIGHTS, DEFAULT_MAX_FLIGHTS)),
            CONF_AUTO_PRUNE_LANDED: bool(options.get(CONF_AUTO_PRUNE_LANDED, DEFAULT_AUTO_PRUNE_LANDED)),
            CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES: int(prune_minutes_default),
            CONF_DELAY_GRACE_MINUTES: int(options.get(CONF_DELAY_GRACE_MINUTES, DEFAULT_DELAY_GRACE_MINUTES)),
            CONF_FAR_BEFORE_DEP_THRESHOLD_HOURS: int(
                options.get(CONF_FAR_BEFORE_DEP_THRESHOLD_HOURS, DEFAULT_FAR_BEFORE_DEP_THRESHOLD_HOURS)
            ),
            CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES: int(
                options.get(CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES, DEFAULT_FAR_BEFORE_DEP_INTERVAL_MINUTES)
            ),
            CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES: int(
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
            ),
            CONF_DEP_WINDOW_PRE_MINUTES: int(options.get(CONF_DEP_WINDOW_PRE_MINUTES, DEFAULT_DEP_WINDOW_PRE_MINUTES)),
            CONF_DEP_WINDOW_POST_MINUTES: int(options.get(CONF_DEP_WINDOW_POST_MINUTES, DEFAULT_DEP_WINDOW_POST_MINUTES)),
            CONF_DEP_WINDOW_INTERVAL_MINUTES: int(
                options.get(CONF_DEP_WINDOW_INTERVAL_MINUTES, DEFAULT_DEP_WINDOW_INTERVAL_MINUTES)
            ),
            CONF_MID_FLIGHT_INTERVAL_MINUTES: int(options.get(CONF_MID_FLIGHT_INTERVAL_MINUTES, DEFAULT_MID_FLIGHT_INTERVAL_MINUTES)),
            CONF_ARR_WINDOW_PRE_MINUTES: int(options.get(CONF_ARR_WINDOW_PRE_MINUTES, DEFAULT_ARR_WINDOW_PRE_MINUTES)),
            CONF_ARR_WINDOW_POST_MINUTES: int(options.get(CONF_ARR_WINDOW_POST_MINUTES, DEFAULT_ARR_WINDOW_POST_MINUTES)),
            CONF_ARR_WINDOW_INTERVAL_MINUTES: int(
                options.get(CONF_ARR_WINDOW_INTERVAL_MINUTES, DEFAULT_ARR_WINDOW_INTERVAL_MINUTES)
            ),
            CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES: int(
                options.get(CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES, DEFAULT_STOP_REFRESH_AFTER_ARRIVAL_MINUTES)
            ),
            CONF_FLIGHTAPI_KEY: str(options.get(CONF_FLIGHTAPI_KEY, "") or ""),
            CONF_AERODATABOX_GATEWAY: str(options.get(CONF_AERODATABOX_GATEWAY, DEFAULT_AERODATABOX_GATEWAY) or DEFAULT_AERODATABOX_GATEWAY),
            CONF_AERODATABOX_RAPIDAPI_KEY: str(options.get(CONF_AERODATABOX_RAPIDAPI_KEY, "") or ""),
            CONF_AERODATABOX_APIMARKET_KEY: str(options.get(CONF_AERODATABOX_APIMARKET_KEY, "") or ""),
            # Always hybrid/provider-first now
            CONF_DIRECTORY_SOURCE_MODE: "provider",
        }

    @staticmethod
    def _provider_usable(provider_name: str, options: dict[str, Any]) -> bool:
        fa_key = str(options.get(CONF_FLIGHTAPI_KEY, "") or "").strip()
        adb_gateway = str(options.get(CONF_AERODATABOX_GATEWAY, DEFAULT_AERODATABOX_GATEWAY) or DEFAULT_AERODATABOX_GATEWAY).strip().lower()
        adb_rapid = str(options.get(CONF_AERODATABOX_RAPIDAPI_KEY, "") or "").strip()
        adb_market = str(options.get(CONF_AERODATABOX_APIMARKET_KEY, "") or "").strip()

        if provider_name == "aerodatabox":
            return bool(adb_market) if adb_gateway == "apimarket" else bool(adb_rapid)
        if provider_name == "flightapi":
            return bool(fa_key)
        return False

    def _selected_external_providers(self) -> set[str]:
        primary = str(self._pending_options.get(CONF_PRIMARY_PROVIDER, DEFAULT_SCHEDULE_PROVIDER)).strip().lower()
        position_provider = str(self._pending_options.get(CONF_POSITION_PROVIDER, DEFAULT_POSITION_PROVIDER)).strip().lower()

        selected: set[str] = set()
        if primary in EXTERNAL_PROVIDER_OPTIONS:
            selected.add(primary)
        if position_provider in EXTERNAL_PROVIDER_OPTIONS:
            selected.add(position_provider)

        return selected

    def _validate_credentials(self) -> dict[str, str]:
        errors: dict[str, str] = {}

        primary = str(self._pending_options.get(CONF_PRIMARY_PROVIDER, DEFAULT_SCHEDULE_PROVIDER)).strip().lower()
        position_provider = str(self._pending_options.get(CONF_POSITION_PROVIDER, DEFAULT_POSITION_PROVIDER)).strip().lower()

        usable_external = False
        for p in (primary, position_provider):
            if p in EXTERNAL_PROVIDER_OPTIONS and self._provider_usable(p, self._pending_options):
                usable_external = True
                break

        if not usable_external:
            errors["base"] = "at_least_one_provider_required"

        if primary == "aerodatabox" and not self._provider_usable("aerodatabox", self._pending_options):
            adb_gateway = str(
                self._pending_options.get(CONF_AERODATABOX_GATEWAY, DEFAULT_AERODATABOX_GATEWAY)
            ).strip().lower()
            key_field = CONF_AERODATABOX_APIMARKET_KEY if adb_gateway == "apimarket" else CONF_AERODATABOX_RAPIDAPI_KEY
            errors[key_field] = "provider_key_required"
        if primary == "flightapi" and not self._provider_usable("flightapi", self._pending_options):
            errors[CONF_FLIGHTAPI_KEY] = "provider_key_required"

        if position_provider == "aerodatabox" and not self._provider_usable("aerodatabox", self._pending_options):
            adb_gateway = str(
                self._pending_options.get(CONF_AERODATABOX_GATEWAY, DEFAULT_AERODATABOX_GATEWAY)
            ).strip().lower()
            key_field = CONF_AERODATABOX_APIMARKET_KEY if adb_gateway == "apimarket" else CONF_AERODATABOX_RAPIDAPI_KEY
            errors[key_field] = "provider_key_required"

        return errors

    async def async_step_init(self, user_input=None) -> FlowResult:
        self._pending_options = self._defaults_from_existing()
        return await self.async_step_providers()

    async def async_step_providers(self, user_input=None) -> FlowResult:
        if user_input is not None:
            primary = str(user_input.get(CONF_PRIMARY_PROVIDER, DEFAULT_SCHEDULE_PROVIDER)).strip().lower()
            if primary not in SINGLE_PROVIDER_OPTIONS:
                primary = DEFAULT_SCHEDULE_PROVIDER
            self._pending_options[CONF_PRIMARY_PROVIDER] = primary
            self._pending_options[CONF_SCHEDULE_PROVIDER] = primary
            self._pending_options[CONF_STATUS_PROVIDER] = primary
            enable_position = bool(user_input.get(CONF_ENABLE_POSITION, False))
            self._pending_options[CONF_ENABLE_POSITION] = enable_position
            if enable_position and primary in POSITION_CAPABLE_PROVIDER_OPTIONS:
                self._pending_options[CONF_POSITION_PROVIDER] = primary
            else:
                self._pending_options[CONF_POSITION_PROVIDER] = "none"
            return await self.async_step_credentials()

        primary_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="aerodatabox", label="AeroDataBox"),
                    selector.SelectOptionDict(value="flightapi", label="FlightAPI.io"),
                ],
                multiple=False,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        primary = str(self._pending_options.get(CONF_PRIMARY_PROVIDER, DEFAULT_SCHEDULE_PROVIDER)).strip().lower()
        enable_position = bool(self._pending_options.get(CONF_ENABLE_POSITION, False))
        schema = vol.Schema(
            {
                vol.Required(CONF_PRIMARY_PROVIDER, default=primary): primary_selector,
                vol.Optional(CONF_ENABLE_POSITION, default=enable_position): bool,
            }
        )

        return self.async_show_form(step_id="providers", data_schema=schema)

    async def async_step_credentials(self, user_input=None) -> FlowResult:
        providers = self._selected_external_providers()

        if user_input is not None:
            for key in (
                CONF_FLIGHTAPI_KEY,
                CONF_AERODATABOX_RAPIDAPI_KEY,
                CONF_AERODATABOX_APIMARKET_KEY,
            ):
                if key in user_input:
                    self._pending_options[key] = str(user_input.get(key, "") or "").strip()
            if CONF_AERODATABOX_GATEWAY in user_input:
                self._pending_options[CONF_AERODATABOX_GATEWAY] = str(user_input.get(CONF_AERODATABOX_GATEWAY) or DEFAULT_AERODATABOX_GATEWAY).strip().lower()

            errors = self._validate_credentials()
            if errors:
                return self.async_show_form(step_id="credentials", data_schema=self._credentials_schema(providers), errors=errors)
            return await self.async_step_polling()

        return self.async_show_form(step_id="credentials", data_schema=self._credentials_schema(providers))

    def _credentials_schema(self, providers: set[str]) -> vol.Schema:
        schema_dict: dict[Any, Any] = {}

        if "aerodatabox" in providers:
            schema_dict[
                vol.Optional(
                    CONF_AERODATABOX_GATEWAY,
                    default=self._pending_options.get(CONF_AERODATABOX_GATEWAY, DEFAULT_AERODATABOX_GATEWAY),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="rapidapi", label="RapidAPI"),
                        selector.SelectOptionDict(value="apimarket", label="API.Market"),
                    ],
                    multiple=False,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema_dict[
                vol.Optional(
                    CONF_AERODATABOX_RAPIDAPI_KEY,
                    default=self._pending_options.get(CONF_AERODATABOX_RAPIDAPI_KEY, ""),
                )
            ] = str
            schema_dict[
                vol.Optional(
                    CONF_AERODATABOX_APIMARKET_KEY,
                    default=self._pending_options.get(CONF_AERODATABOX_APIMARKET_KEY, ""),
                )
            ] = str
        if "flightapi" in providers:
            schema_dict[vol.Optional(CONF_FLIGHTAPI_KEY, default=self._pending_options.get(CONF_FLIGHTAPI_KEY, ""))] = str

        if not schema_dict:
            schema_dict[vol.Optional(CONF_FLIGHTAPI_KEY, default=self._pending_options.get(CONF_FLIGHTAPI_KEY, ""))] = str

        return vol.Schema(schema_dict)

    async def async_step_polling(self, user_input=None) -> FlowResult:
        number_minutes_1_120 = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                max=120,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="min",
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
        number_minutes_0_10080 = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=10080,
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

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_FAR_BEFORE_DEP_THRESHOLD_HOURS,
                    default=self._pending_options.get(CONF_FAR_BEFORE_DEP_THRESHOLD_HOURS, DEFAULT_FAR_BEFORE_DEP_THRESHOLD_HOURS),
                ): number_hours_0_168,
                vol.Required(
                    CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES,
                    default=self._pending_options.get(CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES, DEFAULT_FAR_BEFORE_DEP_INTERVAL_MINUTES),
                ): number_minutes_5_1440,
                vol.Required(
                    CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES,
                    default=self._pending_options.get(CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES, DEFAULT_PREPARE_TO_TRAVEL_INTERVAL_MINUTES),
                ): number_minutes_5_240,
                vol.Required(
                    CONF_DEP_WINDOW_PRE_MINUTES,
                    default=self._pending_options.get(CONF_DEP_WINDOW_PRE_MINUTES, DEFAULT_DEP_WINDOW_PRE_MINUTES),
                ): number_minutes_0_180,
                vol.Required(
                    CONF_DEP_WINDOW_POST_MINUTES,
                    default=self._pending_options.get(CONF_DEP_WINDOW_POST_MINUTES, DEFAULT_DEP_WINDOW_POST_MINUTES),
                ): number_minutes_0_180,
                vol.Required(
                    CONF_DEP_WINDOW_INTERVAL_MINUTES,
                    default=self._pending_options.get(CONF_DEP_WINDOW_INTERVAL_MINUTES, DEFAULT_DEP_WINDOW_INTERVAL_MINUTES),
                ): number_minutes_1_120,
                vol.Required(
                    CONF_MID_FLIGHT_INTERVAL_MINUTES,
                    default=self._pending_options.get(CONF_MID_FLIGHT_INTERVAL_MINUTES, DEFAULT_MID_FLIGHT_INTERVAL_MINUTES),
                ): number_minutes_5_240,
                vol.Required(
                    CONF_ARR_WINDOW_PRE_MINUTES,
                    default=self._pending_options.get(CONF_ARR_WINDOW_PRE_MINUTES, DEFAULT_ARR_WINDOW_PRE_MINUTES),
                ): number_minutes_0_180,
                vol.Required(
                    CONF_ARR_WINDOW_POST_MINUTES,
                    default=self._pending_options.get(CONF_ARR_WINDOW_POST_MINUTES, DEFAULT_ARR_WINDOW_POST_MINUTES),
                ): number_minutes_0_180,
                vol.Required(
                    CONF_ARR_WINDOW_INTERVAL_MINUTES,
                    default=self._pending_options.get(CONF_ARR_WINDOW_INTERVAL_MINUTES, DEFAULT_ARR_WINDOW_INTERVAL_MINUTES),
                ): number_minutes_1_120,
                vol.Required(
                    CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES,
                    default=self._pending_options.get(CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES, DEFAULT_STOP_REFRESH_AFTER_ARRIVAL_MINUTES),
                ): number_minutes_0_10080,
                vol.Required(
                    CONF_DELAY_GRACE_MINUTES,
                    default=self._pending_options.get(CONF_DELAY_GRACE_MINUTES, DEFAULT_DELAY_GRACE_MINUTES),
                ): number_minutes_small,
            }
        )

        if user_input is not None:
            errors: dict[str, str] = {}

            def _ival(key: str, default_val: int = 0) -> int:
                try:
                    return int(user_input.get(key, default_val))
                except Exception:
                    return default_val

            arr_post_in = _ival(CONF_ARR_WINDOW_POST_MINUTES, DEFAULT_ARR_WINDOW_POST_MINUTES)
            stop_after_arr_in = _ival(
                CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES, DEFAULT_STOP_REFRESH_AFTER_ARRIVAL_MINUTES
            )
            if stop_after_arr_in < arr_post_in:
                errors[CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES] = "stop_before_arrival_window_end"

            # Non-critical windows keep a hard floor of 5 minutes.
            interval_fields: Iterable[str] = (
                CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES,
                CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES,
                CONF_MID_FLIGHT_INTERVAL_MINUTES,
            )
            for k in interval_fields:
                if _ival(k, 0) < 5:
                    errors[k] = "interval_below_min_api_poll"

            if errors:
                return self.async_show_form(step_id="polling", data_schema=schema, errors=errors)

            for key in (
                CONF_FAR_BEFORE_DEP_THRESHOLD_HOURS,
                CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES,
                CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES,
                CONF_DEP_WINDOW_PRE_MINUTES,
                CONF_DEP_WINDOW_POST_MINUTES,
                CONF_DEP_WINDOW_INTERVAL_MINUTES,
                CONF_MID_FLIGHT_INTERVAL_MINUTES,
                CONF_ARR_WINDOW_PRE_MINUTES,
                CONF_ARR_WINDOW_POST_MINUTES,
                CONF_ARR_WINDOW_INTERVAL_MINUTES,
                CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES,
                CONF_DELAY_GRACE_MINUTES,
            ):
                self._pending_options[key] = _ival(key, int(self._pending_options.get(key, 0)))

            return await self.async_step_list_cleanup()

        return self.async_show_form(step_id="polling", data_schema=schema)

    async def async_step_list_cleanup(self, user_input=None) -> FlowResult:
        number_minutes_0_10080 = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=10080,
                step=1,
                mode=selector.NumberSelectorMode.SLIDER,
                unit_of_measurement="min",
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
        number_days = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=365, step=1, mode=selector.NumberSelectorMode.SLIDER, unit_of_measurement="days")
        )
        number_flights = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=200, step=1, mode=selector.NumberSelectorMode.SLIDER)
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_INCLUDE_PAST_HOURS,
                    default=self._pending_options.get(CONF_INCLUDE_PAST_HOURS, DEFAULT_INCLUDE_PAST_HOURS),
                ): number_hours_0_72,
                vol.Required(
                    CONF_DAYS_AHEAD,
                    default=self._pending_options.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD),
                ): number_days,
                vol.Required(
                    CONF_MAX_FLIGHTS,
                    default=self._pending_options.get(CONF_MAX_FLIGHTS, DEFAULT_MAX_FLIGHTS),
                ): number_flights,
                vol.Optional(
                    CONF_AUTO_PRUNE_LANDED,
                    default=self._pending_options.get(CONF_AUTO_PRUNE_LANDED, DEFAULT_AUTO_PRUNE_LANDED),
                ): bool,
                vol.Optional(
                    CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES,
                    default=self._pending_options.get(
                        CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES,
                        DEFAULT_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES,
                    ),
                ): number_minutes_0_10080,
            }
        )

        if user_input is not None:
            def _ival(key: str, default_val: int = 0) -> int:
                try:
                    return int(user_input.get(key, default_val))
                except Exception:
                    return default_val

            self._pending_options[CONF_INCLUDE_PAST_HOURS] = _ival(
                CONF_INCLUDE_PAST_HOURS,
                int(self._pending_options.get(CONF_INCLUDE_PAST_HOURS, DEFAULT_INCLUDE_PAST_HOURS)),
            )
            self._pending_options[CONF_DAYS_AHEAD] = _ival(
                CONF_DAYS_AHEAD,
                int(self._pending_options.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD)),
            )
            self._pending_options[CONF_MAX_FLIGHTS] = _ival(
                CONF_MAX_FLIGHTS,
                int(self._pending_options.get(CONF_MAX_FLIGHTS, DEFAULT_MAX_FLIGHTS)),
            )
            self._pending_options[CONF_AUTO_PRUNE_LANDED] = bool(
                user_input.get(CONF_AUTO_PRUNE_LANDED, DEFAULT_AUTO_PRUNE_LANDED)
            )
            self._pending_options[CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES] = max(
                0,
                _ival(
                    CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES,
                    int(
                        self._pending_options.get(
                            CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES,
                            DEFAULT_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES,
                        )
                    ),
                ),
            )

            return await self.async_step_review()

        return self.async_show_form(step_id="list_cleanup", data_schema=schema)

    async def async_step_review(self, user_input=None) -> FlowResult:
        if user_input is not None:
            options = dict(self.config_entry.options)

            options[CONF_ITINERARY_PROVIDERS] = ["manual"]
            options[CONF_DAYS_AHEAD] = int(self._pending_options.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD))
            options[CONF_INCLUDE_PAST_HOURS] = int(
                self._pending_options.get(CONF_INCLUDE_PAST_HOURS, DEFAULT_INCLUDE_PAST_HOURS)
            )
            options[CONF_MAX_FLIGHTS] = int(self._pending_options.get(CONF_MAX_FLIGHTS, DEFAULT_MAX_FLIGHTS))
            options[CONF_AUTO_PRUNE_LANDED] = bool(
                self._pending_options.get(CONF_AUTO_PRUNE_LANDED, DEFAULT_AUTO_PRUNE_LANDED)
            )

            prune_minutes_in = int(
                self._pending_options.get(
                    CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES,
                    DEFAULT_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES,
                )
            )
            options[CONF_AUTO_REMOVE_AFTER_ARRIVAL_MINUTES] = max(0, prune_minutes_in)
            if options[CONF_AUTO_PRUNE_LANDED] and prune_minutes_in > 0:
                options[CONF_PRUNE_LANDED_HOURS] = max(1, (prune_minutes_in + 59) // 60)
            else:
                options[CONF_PRUNE_LANDED_HOURS] = int(
                    options.get(CONF_PRUNE_LANDED_HOURS, DEFAULT_PRUNE_LANDED_HOURS)
                )

            options[CONF_STATUS_PROVIDER] = str(
                self._pending_options.get(CONF_STATUS_PROVIDER, DEFAULT_STATUS_PROVIDER)
            )
            options[CONF_POSITION_PROVIDER] = str(
                self._pending_options.get(CONF_POSITION_PROVIDER, DEFAULT_POSITION_PROVIDER)
            )
            options[CONF_SCHEDULE_PROVIDER] = str(
                self._pending_options.get(CONF_SCHEDULE_PROVIDER, DEFAULT_SCHEDULE_PROVIDER)
            )

            # Always provider-first with inbuilt fallback for directory data.
            options[CONF_DIRECTORY_SOURCE_MODE] = "provider"

            # Hidden, fixed minimum poll to keep backend rationing stable.
            options[CONF_MIN_API_POLL_MINUTES] = 5

            options[CONF_FAR_BEFORE_DEP_THRESHOLD_HOURS] = int(
                self._pending_options.get(CONF_FAR_BEFORE_DEP_THRESHOLD_HOURS, DEFAULT_FAR_BEFORE_DEP_THRESHOLD_HOURS)
            )
            options[CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES] = int(
                self._pending_options.get(CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES, DEFAULT_FAR_BEFORE_DEP_INTERVAL_MINUTES)
            )
            options[CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES] = int(
                self._pending_options.get(
                    CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES,
                    DEFAULT_PREPARE_TO_TRAVEL_INTERVAL_MINUTES,
                )
            )
            options.pop(CONF_MID_BEFORE_DEP_THRESHOLD_HOURS, None)
            options.pop(CONF_MID_BEFORE_DEP_INTERVAL_MINUTES, None)
            options.pop(CONF_NEAR_BEFORE_DEP_INTERVAL_MINUTES, None)
            options[CONF_DEP_WINDOW_PRE_MINUTES] = int(
                self._pending_options.get(CONF_DEP_WINDOW_PRE_MINUTES, DEFAULT_DEP_WINDOW_PRE_MINUTES)
            )
            options[CONF_DEP_WINDOW_POST_MINUTES] = int(
                self._pending_options.get(CONF_DEP_WINDOW_POST_MINUTES, DEFAULT_DEP_WINDOW_POST_MINUTES)
            )
            options[CONF_DEP_WINDOW_INTERVAL_MINUTES] = max(
                1,
                int(self._pending_options.get(CONF_DEP_WINDOW_INTERVAL_MINUTES, DEFAULT_DEP_WINDOW_INTERVAL_MINUTES)),
            )
            options[CONF_MID_FLIGHT_INTERVAL_MINUTES] = int(
                self._pending_options.get(CONF_MID_FLIGHT_INTERVAL_MINUTES, DEFAULT_MID_FLIGHT_INTERVAL_MINUTES)
            )
            options[CONF_ARR_WINDOW_PRE_MINUTES] = int(
                self._pending_options.get(CONF_ARR_WINDOW_PRE_MINUTES, DEFAULT_ARR_WINDOW_PRE_MINUTES)
            )
            options[CONF_ARR_WINDOW_POST_MINUTES] = int(
                self._pending_options.get(CONF_ARR_WINDOW_POST_MINUTES, DEFAULT_ARR_WINDOW_POST_MINUTES)
            )
            options[CONF_ARR_WINDOW_INTERVAL_MINUTES] = max(
                1,
                int(self._pending_options.get(CONF_ARR_WINDOW_INTERVAL_MINUTES, DEFAULT_ARR_WINDOW_INTERVAL_MINUTES)),
            )
            options[CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES] = int(
                self._pending_options.get(
                    CONF_STOP_REFRESH_AFTER_ARRIVAL_MINUTES,
                    DEFAULT_STOP_REFRESH_AFTER_ARRIVAL_MINUTES,
                )
            )
            options[CONF_DELAY_GRACE_MINUTES] = int(
                self._pending_options.get(CONF_DELAY_GRACE_MINUTES, DEFAULT_DELAY_GRACE_MINUTES)
            )

            options[CONF_FLIGHTAPI_KEY] = str(self._pending_options.get(CONF_FLIGHTAPI_KEY, "") or "").strip()
            options[CONF_AERODATABOX_GATEWAY] = str(
                self._pending_options.get(CONF_AERODATABOX_GATEWAY, DEFAULT_AERODATABOX_GATEWAY) or DEFAULT_AERODATABOX_GATEWAY
            ).strip().lower()
            options[CONF_AERODATABOX_RAPIDAPI_KEY] = str(
                self._pending_options.get(CONF_AERODATABOX_RAPIDAPI_KEY, "") or ""
            ).strip()
            options[CONF_AERODATABOX_APIMARKET_KEY] = str(
                self._pending_options.get(CONF_AERODATABOX_APIMARKET_KEY, "") or ""
            ).strip()

            return self.async_create_entry(title="", data=options)

        schedule = str(self._pending_options.get(CONF_SCHEDULE_PROVIDER, DEFAULT_SCHEDULE_PROVIDER))
        status = str(self._pending_options.get(CONF_STATUS_PROVIDER, DEFAULT_STATUS_PROVIDER))
        position = str(self._pending_options.get(CONF_POSITION_PROVIDER, DEFAULT_POSITION_PROVIDER))

        placeholders = {
            "mode": "Single",
            "schedule": schedule,
            "status": status,
            "position": position,
            "far": str(self._pending_options.get(CONF_FAR_BEFORE_DEP_INTERVAL_MINUTES, DEFAULT_FAR_BEFORE_DEP_INTERVAL_MINUTES)),
            "prepare": str(self._pending_options.get(CONF_PREPARE_TO_TRAVEL_INTERVAL_MINUTES, DEFAULT_PREPARE_TO_TRAVEL_INTERVAL_MINUTES)),
            "takeoff": str(self._pending_options.get(CONF_DEP_WINDOW_INTERVAL_MINUTES, DEFAULT_DEP_WINDOW_INTERVAL_MINUTES)),
            "mid": str(self._pending_options.get(CONF_MID_FLIGHT_INTERVAL_MINUTES, DEFAULT_MID_FLIGHT_INTERVAL_MINUTES)),
            "landing": str(self._pending_options.get(CONF_ARR_WINDOW_INTERVAL_MINUTES, DEFAULT_ARR_WINDOW_INTERVAL_MINUTES)),
        }

        return self.async_show_form(
            step_id="review",
            data_schema=vol.Schema({}),
            description_placeholders=placeholders,
        )
