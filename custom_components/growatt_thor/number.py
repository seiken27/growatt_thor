"""Number entities for Growatt THOR load balancing."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberDeviceClass, NumberMode
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from ocpp.v16.enums import ConfigurationStatus

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN]["coordinator"]

    async_add_entities([
        MaxCurrentNumber(coordinator, entry),
        LoadBalancingLimitNumber(coordinator, entry),
        ElectricityPriceNumber(coordinator, entry),
    ])


# ─────────────────────────────
# Base class
# ─────────────────────────────

class BaseConfigNumber(CoordinatorEntity, NumberEntity):

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_mode = NumberMode.BOX

    def __init__(self, coordinator, entry, key):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self.hass = coordinator.hass

    def _format_value(self, value: float) -> str:
        return str(int(round(value)))


# ─────────────────────────────
# Max Current
# ─────────────────────────────

_NOMINAL_VOLTAGE = 230.0
_PHASES = 3  # THOR + XC40 onboard AC are both 3-phase


def _kw_to_amps(kw: float) -> int:
    amps = (float(kw) * 1000.0) / (_PHASES * _NOMINAL_VOLTAGE)
    return max(6, min(32, int(round(amps))))


def _amps_to_kw(amps: float) -> float:
    return round((float(amps) * _PHASES * _NOMINAL_VOLTAGE) / 1000.0, 1)


class MaxCurrentNumber(BaseConfigNumber):
    """Charge power slider in kW.

    UI shows kW (easier to reason about); OCPP profile is still sent in
    Amps because that's what the THOR reliably accepts. Conversion
    assumes 3-phase @ 230 V nominal.
    """

    _attr_name = "Max Charge Power"
    _attr_icon = "mdi:ev-station"
    _attr_native_min_value = 4.2   # 6 A × 3 phases × 230 V ≈ 4.14 kW; THOR won't start below this
    _attr_native_max_value = 22.0
    _attr_native_step = 0.2
    _attr_native_unit_of_measurement = "kW"
    _attr_suggested_display_precision = 1
    _config_key = "G_MaxCurrent"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "max_current")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Growatt THOR EV Charger",
            "manufacturer": "Growatt",
            "model": "THOR",
        }

    @property
    def native_value(self):
        value = self.coordinator.max_current
        return _amps_to_kw(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        kw = float(value)
        amps = _kw_to_amps(kw)

        charge_point = self.hass.data.get(DOMAIN, {}).get("charge_point")
        if not charge_point:
            _LOGGER.warning("Cannot change Max Charge Power: charger not connected")
            return

        current = self.coordinator.max_current
        if current is not None and int(round(current)) == amps:
            _LOGGER.debug("Max Charge Power unchanged (%.1f kW = %d A) - skipping", kw, amps)
            return

        previous = int(round(current)) if current is not None else None
        self.coordinator.max_current = amps
        self.coordinator.async_set_updated_data(True)
        _LOGGER.info(
            "📝 Max Charge Power UI → %.1f kW = %d A/phase (SetChargingProfile, direct)",
            kw, amps,
        )

        self.hass.async_create_task(
            self._write_via_profile(charge_point, amps, previous)
        )

    async def _write_via_profile(self, charge_point, value: int, previous: int | None):
        try:
            txn = self.coordinator.transaction_id
            result = await charge_point.set_charging_profile(
                connector_id=1,
                limit_amps=value,
                transaction_id=txn if txn else None,
            )
            status = result.get("status")
            if status == "Accepted":
                self.coordinator.max_current = value
                self.coordinator.async_set_updated_data(True)
                _LOGGER.info(
                    "✅ Max Charge Power → %d A (≈ %.1f kW, profile accepted)",
                    value, _amps_to_kw(value),
                )
            else:
                _LOGGER.error(
                    "❌ SetChargingProfile rejected: %s — rolling back to %s A",
                    status, previous,
                )
                if previous is not None:
                    self.coordinator.max_current = previous
                    self.coordinator.async_set_updated_data(True)
        except Exception as exc:
            _LOGGER.error("❌ Failed SetChargingProfile: %s", exc, exc_info=True)
            if previous is not None:
                self.coordinator.max_current = previous
                self.coordinator.async_set_updated_data(True)

    async def _write_to_thor(self, charge_point, value: int, previous: int | None):
        # Legacy path via ChangeConfiguration(G_MaxCurrent). Kept for reference
        # but no longer wired up — use _write_via_profile instead.
        try:
            result = await charge_point.change_configuration(
                self._config_key,
                str(value)
            )

            if result == ConfigurationStatus.accepted:
                self.coordinator.max_current = value
                self.coordinator.async_set_updated_data(True)
                _LOGGER.info("✅ Max Current written to Thor: %d A", value)
            elif result == ConfigurationStatus.reboot_required:
                self.coordinator.max_current = value
                self.coordinator.async_set_updated_data(True)
                _LOGGER.warning("⚠️ Max Current write accepted (reboot required): %d A", value)
            else:
                _LOGGER.error("❌ Max Current rejected by Thor: %s — rolling back UI to %s A", result, previous)
                if previous is not None:
                    self.coordinator.max_current = previous
                    self.coordinator.async_set_updated_data(True)

        except Exception as exc:
            _LOGGER.error("❌ Failed to set Max Current: %s", exc, exc_info=True)
            if previous is not None:
                self.coordinator.max_current = previous
                self.coordinator.async_set_updated_data(True)


# ─────────────────────────────
# Load Balancing Limit
# ─────────────────────────────

class LoadBalancingLimitNumber(BaseConfigNumber):

    _attr_name = "Loadbalancing limit"
    _attr_icon = "mdi:speedometer"
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_min_value = 4
    _attr_native_max_value = 22
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "kW"
    _config_key = "G_ExternalLimitPower"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "load_balancing_limit")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id, "grid_connection")},
            "name": "Growatt THOR Load balancing",
            "manufacturer": "Growatt",
            "model": "THOR Load balancing",
        }

    @property
    def native_value(self):
        value = self.coordinator.external_limit_power
        return int(value) if value is not None else 10

    async def async_set_native_value(self, value: float) -> None:
        value = int(round(value))

        charge_point = self.hass.data.get(DOMAIN, {}).get("charge_point")
        if not charge_point:
            _LOGGER.warning("Cannot change Load Balancing Limit: charger not connected")
            return

        current = self.coordinator.external_limit_power
        if current is not None and int(round(current)) == value:
            _LOGGER.debug("Load Balancing Limit unchanged (%d kW) - skipping write", value)
            return

        previous = int(round(current)) if current is not None else None
        self.coordinator.external_limit_power = value
        self.coordinator.async_set_updated_data(True)
        _LOGGER.info("📝 Load Balancing Limit UI updated to %d kW (queued for write)", value)

        await self.coordinator.queue_write(
            self._write_to_thor,
            charge_point,
            value,
            previous,
            dedupe_key=self._config_key,
        )

    async def _write_to_thor(self, charge_point, value: int, previous: int | None):
        try:
            result = await charge_point.change_configuration(
                self._config_key,
                str(value)
            )

            if result == ConfigurationStatus.accepted:
                self.coordinator.external_limit_power = value
                self.coordinator.async_set_updated_data(True)
                _LOGGER.info("✅ Load Balancing Limit written to Thor: %d kW", value)
            elif result == ConfigurationStatus.reboot_required:
                self.coordinator.external_limit_power = value
                self.coordinator.async_set_updated_data(True)
                _LOGGER.warning("⚠️ Load Balancing Limit write accepted (reboot required): %d kW", value)
            else:
                _LOGGER.error("❌ Load Balancing Limit rejected by Thor: %s — rolling back UI to %s kW", result, previous)
                if previous is not None:
                    self.coordinator.external_limit_power = previous
                    self.coordinator.async_set_updated_data(True)

        except Exception as exc:
            _LOGGER.error("❌ Failed to set Load Balancing Limit: %s", exc, exc_info=True)
            if previous is not None:
                self.coordinator.external_limit_power = previous
                self.coordinator.async_set_updated_data(True)


# ─────────────────────────────
# Electricity price
# ─────────────────────────────

class ElectricityPriceNumber(BaseConfigNumber):

    _attr_name = "Electricity Price"
    _attr_icon = "mdi:currency-eur"
    _attr_native_min_value = -2.0
    _attr_native_max_value = 2.0
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_suggested_display_precision = 2
    _config_key = "G_TimeSharingPrice"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "electricity_price")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Growatt THOR EV Charger",
            "manufacturer": "Growatt",
            "model": "THOR",
        }

    @property
    def native_value(self):
        return self.coordinator.electricity_price

    async def async_set_native_value(self, value: float) -> None:
        value = round(value, 2)

        charge_point = self.hass.data.get(DOMAIN, {}).get("charge_point")
        if not charge_point:
            _LOGGER.warning("Cannot change Elektricteitstarief: charger not connected")
            return

        current = self.coordinator.electricity_price
        if current is not None and round(current, 2) == value:
            _LOGGER.debug("Elektricteitstarief unchanged (%.2f EUR/kWh) - skipping write", value)
            return

        previous = round(current, 2) if current is not None else None
        self.coordinator.electricity_price = value
        self.coordinator.async_set_updated_data(True)
        _LOGGER.info("📝 Elektricteitstarief UI updated to %.2f EUR/kWh (queued for write)", value)

        await self.coordinator.queue_write(
            self._write_to_thor,
            charge_point,
            value,
            previous,
            dedupe_key=self._config_key,
        )

    async def _write_to_thor(self, charge_point, value: float, previous: float | None):
        price_str = f"time1=00:00-23:59&price1={value:.2f}"  # ← gecorrigeerd: formaat conform THOR response
        try:
            result = await charge_point.change_configuration(
                self._config_key,
                price_str,
            )

            if result == ConfigurationStatus.accepted:
                self.coordinator.electricity_price = value
                self.coordinator.async_set_updated_data(True)
                _LOGGER.info("✅ Elektricteitstarief written to Thor: %s", price_str)
            elif result == ConfigurationStatus.reboot_required:
                self.coordinator.electricity_price = value
                self.coordinator.async_set_updated_data(True)
                _LOGGER.warning("⚠️ Elektricteitstarief write accepted (reboot required): %s", price_str)
            else:
                _LOGGER.error("❌ Elektricteitstarief rejected by Thor: %s — rolling back to %.2f", result, previous)
                if previous is not None:
                    self.coordinator.electricity_price = previous
                    self.coordinator.async_set_updated_data(True)

        except Exception as exc:
            _LOGGER.error("❌ Failed to set Elektricteitstarief: %s", exc, exc_info=True)
            if previous is not None:
                self.coordinator.electricity_price = previous
                self.coordinator.async_set_updated_data(True)
