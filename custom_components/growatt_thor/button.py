"""Button entities for Growatt THOR.

The start/stop buttons were removed in the Wren fork. Charging is now
controlled by ``switch.growatt_thor_ev_charger_charge_control`` (see
``switch.py``), which maps directly onto RemoteStartTransaction /
RemoteStopTransaction with no write-queue between the call and the wire.

This file is kept so Home Assistant's platform loader stays happy if it
ever re-scans the domain, and to document the change.
"""
from __future__ import annotations


async def async_setup_entry(hass, entry, async_add_entities):  # noqa: D401
    """No buttons in this fork \u2014 charge control is a switch now."""
    return
