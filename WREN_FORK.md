# Wren fork notes

Fork of [bobbesnl/growatt_thor](https://github.com/bobbesnl/growatt_thor).

## What changed (v1.5.4-wren.1)

### 1. Max Current uses `SetChargingProfile` instead of `ChangeConfiguration`

Upstream sends `ChangeConfiguration(G_MaxCurrent, "<A>")` for every current
change, which writes to the THOR's flash config and reportedly crashes the
firmware under rapid updates. Upstream mitigated this with a 20-second
write-queue on `coordinator.queue_write(...)`.

This fork instead sends `SetChargingProfile` (OCPP 1.6 smart charging) —
TxProfile / Relative / Amps, matching the payload shape that the official
[home-assistant/core-ocpp](https://github.com/lbbrhzn/ocpp) integration uses
via `ocpp.set_charge_rate`. Verified working on THOR FJN00003231900xx — can
fire on every state change of an amperage input_number with no crashes.

- Direct dispatch, no queue.
- Uses TxProfile when a transaction is active, TxDefaultProfile otherwise.
- Legacy `_write_to_thor` (ChangeConfiguration path) kept as dead code for
  reference in case a specific model needs the old behaviour.

### 2. Start/Stop buttons replaced with a Charge Control switch

New: `switch.growatt_thor_ev_charger_charge_control`.

- `turn_on` → `RemoteStartTransaction(connector=1, id_tag="12345678")`
- `turn_off` → `RemoteStopTransaction(transaction_id=<current or 0>)`

Direct, no queue. Matches the mental model of the official OCPP
integration's `charge_control` switch — one entity, on/off, snappy.

The old start/stop buttons (`button.growatt_thor_ev_charger_start_charging`,
`_stop_charging`) are gone. `button.py` is a stub that registers zero
entities. Existing automations that used those button IDs will need to be
retargeted at the switch.

### 3. What stayed queued

Load-balancing limit, load-balancing enable, LCD display, tariff, and the
charging-schedule times still go through `ChangeConfiguration` and stay
inside the write-queue. Those are legit flash-config writes and don't need
to fire frequently, so the FW-protection queue still makes sense.

## Deploy

```
scp -r custom_components/growatt_thor hmlm@10.2.1.11:/tmp/
ssh hmlm@10.2.1.11 'sudo rm -rf /config/custom_components/growatt_thor && sudo mv /tmp/growatt_thor /config/custom_components/ && sudo chown -R root:root /config/custom_components/growatt_thor'
```

(HAOS's Advanced SSH addon doesn't support scp — use the `cat | ssh` pattern
in practice.)

Restart HA, then:

- Check the Growatt THOR device page for the new `Charge Control` switch.
- Slide the Max Current number — logs should show
  `⚡ SetChargingProfile: ...` instead of `ChangeConfiguration`.
