"""Switch entities for Growatt THOR load balancing and LCD display."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from ocpp.v16.enums import ConfigurationStatus

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Growatt THOR switches."""
    coordinator = hass.data[DOMAIN]["coordinator"]

    async_add_entities([
        ChargeControlSwitch(coordinator, entry),
        LoadBalancingEnableSwitch(coordinator, entry),
        LcdDisplaySwitch(coordinator, entry),
    ])


DEFAULT_ID_TAG = "12345678"  # Growatt handshake key


class ChargeControlSwitch(CoordinatorEntity, SwitchEntity):
    """Charge control switch — on = start transaction, off = stop.

    Sends RemoteStartTransaction / RemoteStopTransaction directly, no queue.
    Matches the behaviour of the official OCPP integration's charge_control
    switch, which fires cleanly and can be toggled as fast as you like.
    """

    _attr_has_entity_name = True
    _attr_name = "Charge Control"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_charge_control"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Growatt THOR EV Charger",
            "manufacturer": "Growatt",
            "model": "THOR",
        }
        self.hass = coordinator.hass

    @property
    def is_on(self):
        """On while a transaction is active."""
        return self.coordinator.status == "Charging" or self.coordinator.transaction_id is not None

    async def async_turn_on(self, **kwargs):
        charge_point = self.hass.data.get(DOMAIN, {}).get("charge_point")
        if not charge_point:
            _LOGGER.warning("Cannot start charging: charger not connected")
            return
        if self.coordinator.status == "Charging":
            _LOGGER.debug("Charge control on — already charging, no-op")
            return
        result = await charge_point.remote_start_transaction(
            connector_id=1, id_tag=DEFAULT_ID_TAG
        )
        status = result.get("status")
        if status == "Accepted":
            _LOGGER.info("✅ Charge control ON — RemoteStart accepted")
            self.coordinator.async_set_updated_data(True)
        else:
            _LOGGER.error("❌ RemoteStart rejected: %s", status)

    async def async_turn_off(self, **kwargs):
        charge_point = self.hass.data.get(DOMAIN, {}).get("charge_point")
        if not charge_point:
            _LOGGER.warning("Cannot stop charging: charger not connected")
            return
        txn = self.coordinator.transaction_id
        if txn is None and self.coordinator.status != "Charging":
            _LOGGER.debug("Charge control off — no active session, no-op")
            return
        tid = int(txn) if txn is not None else 0
        result = await charge_point.remote_stop_transaction(transaction_id=tid)
        status = result.get("status")
        if status == "Accepted":
            _LOGGER.info("✅ Charge control OFF — RemoteStop accepted (txn=%s)", tid)
            self.coordinator.async_set_updated_data(True)
        else:
            _LOGGER.error("❌ RemoteStop rejected: %s", status)


class LoadBalancingEnableSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable load balancing."""

    _attr_has_entity_name = False
    _attr_name = "Loadbalancing"
    _attr_icon = "mdi:power-plug-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_load_balancing_enable"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id, "grid_connection")},
            "name": "Growatt THOR Load balancing",
            "manufacturer": "Growatt",
            "model": "THOR Load balancing",
        }
        self.hass = coordinator.hass

    @property
    def is_on(self):
        """Return true if load balancing is enabled."""
        return self.coordinator.external_limit_power_enable

    async def async_turn_on(self, **kwargs):
        """Enable load balancing."""
        await self._set_value("1")

    async def async_turn_off(self, **kwargs):
        """Disable load balancing."""
        await self._set_value("0")

    async def _apply_external_limit_power_enable(self, charge_point, value: str):
        """Actually write setting to the charger (runs inside write-queue)."""
        _LOGGER.info("Setting G_ExternalLimitPowerEnable to %s", value)

        result = await charge_point.change_configuration(
            "G_ExternalLimitPowerEnable",
            value
        )

        new_state = (value == "1")

        if result == ConfigurationStatus.accepted:
            self.coordinator.external_limit_power_enable = new_state
            self.coordinator.async_set_updated_data(True)
            _LOGGER.info(
                "✅ Loadbalancing enabled → %s (accepted)",
                "ON" if new_state else "OFF"
            )
        elif result == ConfigurationStatus.reboot_required:
            self.coordinator.external_limit_power_enable = new_state
            self.coordinator.async_set_updated_data(True)
            _LOGGER.warning(
                "⚠️ Loadbalancing enabled → %s (reboot required)",
                "ON" if new_state else "OFF"
            )
        else:
            _LOGGER.error("❌ Enable loadbalancing change rejected: %s", result)

    async def _set_value(self, value: str):
        """Queue the configuration update (prevents rapid-fire FW crashes)."""
        charge_point = self.hass.data.get(DOMAIN, {}).get("charge_point")

        if not charge_point:
            _LOGGER.warning("Cannot change Loadbalancing: charger not connected")
            return

        try:
            await self.coordinator.queue_write(
                self._apply_external_limit_power_enable,
                charge_point,
                value
            )

        except Exception as exc:
            _LOGGER.error("❌ Failed to change Loadbalancing: %s", exc, exc_info=True)


class LcdDisplaySwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable LCD display (G_LCDCloseEnable)."""

    _attr_has_entity_name = False
    _attr_name = "LCD Display"
    _attr_icon = "mdi:monitor"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_lcd_display"
        # Zelfde identifiers als de hoofdcharger zodat deze entity
        # verschijnt bij max stroom, charge aan/uit etc.
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Growatt THOR EV Charger",
            "manufacturer": "Growatt",
            "model": "THOR",
        }
        self.hass = coordinator.hass

    @property
    def is_on(self):
        """Return true als LCD AAN is (G_LCDCloseEnable = Disable)."""
        if self.coordinator.lcd_close_enable is None:
            return None
        return self.coordinator.lcd_close_enable == "Disable"

    async def async_turn_on(self, **kwargs):
        """Zet LCD AAN (G_LCDCloseEnable = Disable)."""
        await self._set_value("Disable")

    async def async_turn_off(self, **kwargs):
        """Zet LCD UIT (G_LCDCloseEnable = Enable)."""
        await self._set_value("Enable")

    async def _apply_lcd_close_enable(self, charge_point, value: str):
        """Actually write LCD setting to the charger (runs inside write-queue)."""
        _LOGGER.info("Setting G_LCDCloseEnable to %s", value)

        result = await charge_point.change_configuration(
            "G_LCDCloseEnable",
            value
        )

        if result == ConfigurationStatus.accepted:
            self.coordinator.lcd_close_enable = value
            self.coordinator.async_set_updated_data(True)
            _LOGGER.info(
                "✅ LCD display → %s (accepted)",
                "ON" if value == "Disable" else "OFF"
            )
        elif result == ConfigurationStatus.reboot_required:
            self.coordinator.lcd_close_enable = value
            self.coordinator.async_set_updated_data(True)
            _LOGGER.warning(
                "⚠️ LCD display → %s (reboot required)",
                "ON" if value == "Disable" else "OFF"
            )
        else:
            _LOGGER.error("❌ LCD display change rejected: %s", result)

    async def _set_value(self, value: str):
        """Queue the configuration update (prevents rapid-fire FW crashes)."""
        charge_point = self.hass.data.get(DOMAIN, {}).get("charge_point")

        if not charge_point:
            _LOGGER.warning("Cannot change LCD display: charger not connected")
            return

        try:
            await self.coordinator.queue_write(
                self._apply_lcd_close_enable,
                charge_point,
                value
            )

        except Exception as exc:
            _LOGGER.error("❌ Failed to change LCD display: %s", exc, exc_info=True)
