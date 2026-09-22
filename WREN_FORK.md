# Wren fork notes

Fork of [bobbesnl/growatt_thor](https://github.com/bobbesnl/growatt_thor).

**Current:** `v1.5.4-wren.5` on `main`. Upstream is at `v1.6.0` (2026-08-30) —
we're deliberately staying behind. See [Upstream 1.6.0 rebase](#upstream-160-rebase--deferred)
below.

Verified on THOR serial `FJN00003231900xx` (22AS, FW 2.2.16-20240902).

## Why this fork exists

Upstream throttles every write through a 20-second queue as FW-crash mitigation
for the THOR. That's fine for legit flash-config writes, but it makes the Max
Current slider unusable for dynamic smart-charging (EV load balancing, PV
tracking, Amber price arbitrage) — every slider nudge waits 20 s and hammers
flash.

The official [lbbrhzn/ocpp](https://github.com/lbbrhzn/ocpp) integration fires
`SetChargingProfile` instantly with no queue and no crashes, so we do the same
here for the two entities that need to move fast.

## What changed

### 1. Max Current uses `SetChargingProfile` instead of `ChangeConfiguration`

Upstream sends `ChangeConfiguration(G_MaxCurrent, "<A>")` for every change,
which writes to the THOR's flash config. Crashes the FW under rapid updates.

This fork sends OCPP 1.6 smart charging `SetChargingProfile` — TxProfile /
Relative / Amps, matching the payload shape lbbrhzn/ocpp uses via
`ocpp.set_charge_rate`.

- Direct dispatch, no queue, no flash writes.
- `TxProfile` when a transaction is active, `TxDefaultProfile` otherwise.
- New helpers `set_charging_profile` / `clear_charging_profile` in
  `ocpp_server.py`.
- Rewritten `MaxCurrentNumber` in `number.py`.
- Legacy `_write_to_thor` (ChangeConfiguration path) kept as dead code for
  reference.

**Slider is in kW, not amps.** Range 4.2 – 22.0 kW, step 0.2 kW. 4.2 kW is the
THOR's 6 A/phase floor at 3ph @ 230 V (6 × 230 × 3 / 1000 = 4.14 kW, rounded up).
Value gets converted back to amps 3ph @ 230 V before it hits the wire.

### 2. Start/Stop buttons replaced with a Charge Control switch

New: `switch.growatt_thor_ev_charger_charge_control`.

- `turn_on` → `RemoteStartTransaction(connector=1, id_tag="12345678")`
- `turn_off` → `RemoteStopTransaction(transaction_id=<current or 0>)`

Direct, no queue. Matches the mental model of lbbrhzn/ocpp's `charge_control`
switch — one entity, on/off, snappy.

The old buttons (`button.growatt_thor_ev_charger_start_charging`,
`_stop_charging`) are gone. `button.py` is a stub that registers zero
entities. Automations targeting those button IDs need to be retargeted at the
switch.

### 3. Flash Max Current sensor (read-only)

New: `sensor.growatt_thor_ev_charger_flash_max_current`.

Shows the persisted flash-config `G_MaxCurrent` value alongside whatever
`SetChargingProfile` is currently limiting to. Useful for verifying the two
paths haven't drifted — the profile can lower the effective limit below the
flash value, but never above.

### 4. What stayed queued

Load-balancing limit, load-balancing enable, LCD display, tariff, and the
charging-schedule times still go through `ChangeConfiguration` and stay inside
the write-queue. Those are legit flash-config writes and don't need to fire
frequently, so the FW-protection queue still makes sense there.

## Version history

| Tag | Change |
| --- | --- |
| `1.5.4-wren.1` | SetChargingProfile for Max Current + Charge Control switch |
| `1.5.4-wren.2` | Max Current slider now shows kW instead of A |
| `1.5.4-wren.3` | Bump slider min to 4.2 kW (6 A/phase THOR floor) |
| `1.5.4-wren.4` | Add read-only Flash Max Current sensor |
| `1.5.4-wren.5` | Fix NameError: move `FlashMaxCurrentSensor` below `BaseSensor` |

## Upstream 1.6.0 rebase — deferred

Analysed 2026-09-07. 45 files / +6,745 lines / 22 new files, but nothing that
changes what we can do that wasn't already possible:

- Session records / CSV correlation (Dutch ERE reporting — we don't need)
- Configuration diagnostics panel
- Currency auto-detect (would render AUD/kWh instead of EUR — cosmetic)
- "Load balancing" device renamed to "External Meter" (cosmetic)
- Connection watchdog / stale WebSocket expiry (haven't hit the bug it fixes)
- Coordinator rewrite (internal plumbing)
- Tests, translations, docs

**Rebase plan if we ever pull upstream:**

1. Reset `main` to `v1.6.0`.
2. Re-apply the two fork changes (SetChargingProfile Max Current, Charge
   Control switch) on top of the rewritten `number.py` / `ocpp_server.py`.
3. Re-apply the Flash Max Current sensor.
4. Restore upstream's `button.py` if we ever want the old start/stop buttons
   back (currently a stub).
5. Grep for `load.balancing` / `Load balancing` in downstream configs — entity
   IDs are pinned by `unique_id` but device-page names shift under the rename.
6. Tag `v1.6.0-wren.1`.

**Rebase triggers:**

- We hit a bug the connection watchdog would fix.
- EO Phase 3 wants session-record correlation (predicted vs actual $ per
  charge session).

## Deploy

HAOS Advanced SSH addon doesn't support `scp`, so use the `cat | ssh` pattern:

```
for f in custom_components/growatt_thor/*.py; do
  cat "$f" | ssh hmlm@10.2.1.11 "cat > /tmp/$(basename $f)"
done
ssh hmlm@10.2.1.11 'sudo mv /tmp/*.py /config/custom_components/growatt_thor/ && sudo chown -R root:root /config/custom_components/growatt_thor'
```

Then restart HA (`ha core restart` after sourcing `/etc/profile.d/homeassistant.sh`),
and check:

- Device page shows the `Charge Control` switch.
- Slide the Max Current number — logs show `⚡ SetChargingProfile: ...` instead
  of `ChangeConfiguration`.
- `sensor.growatt_thor_ev_charger_flash_max_current` reports a plausible amp
  value.
