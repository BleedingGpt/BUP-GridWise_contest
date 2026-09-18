def post_validate(plan: list[dict], hours: list[dict], battery: dict, directives: list[dict]) -> tuple[bool, str]:
    """Sanity check the schedule before sending response."""
    cap = battery["capacity_kwh"]
    init_e = battery["initial_energy_kwh"]
    min_e = battery["minimum_energy_kwh"]
    max_charge = battery["max_charge_kwh_per_hour"]
    max_discharge = battery["max_discharge_kwh_per_hour"]

    # Parse directive hour constraints
    no_charge_hours = set()
    no_discharge_hours = set()
    max_grid_limits = {}
    min_reserve_hours = {}

    for d in directives:
        if not d.get("applies") or d.get("directive_type") == "no_op":
            continue
        dt = d["directive_type"]
        adj = d.get("structured_adjustment")
        if not adj:
            continue
        if dt == "no_charge_window":
            no_charge_hours.update(adj["hours"])
        elif dt == "no_discharge_window":
            no_discharge_hours.update(adj["hours"])
        elif dt == "max_grid_window":
            for h in adj["hours"]:
                max_grid_limits[h] = adj["max_grid_kwh"]
        elif dt == "minimum_battery_reserve":
            for h in adj["hours"]:
                min_reserve_hours[h] = max(min_reserve_hours.get(h, 0), adj["minimum_energy_kwh"])

    # Compute effective solar
    effective_solar = [h["solar_kwh"] for h in hours]
    for d in directives:
        if d.get("directive_type") == "solar_reduction" and d.get("applies"):
            adj = d.get("structured_adjustment")
            if adj:
                for h in adj["hours"]:
                    effective_solar[h] = effective_solar[h] * adj["factor"]

    for i, entry in enumerate(plan):
        h = entry["hour"]
        demand = hours[h]["demand_kwh"]
        tariff = hours[h]["tariff_bdt_per_kwh"]

        # Energy balance
        balance_lhs = entry["grid_kwh"] + entry["solar_used_kwh"] + (
            entry["battery_kwh"] if entry["battery_action"] == "discharge" else 0
        )
        balance_rhs = demand + (
            entry["battery_kwh"] if entry["battery_action"] == "charge" else 0
        )
        if abs(balance_lhs - balance_rhs) > 0.01:
            return False, f"Hour {h}: energy balance violated ({balance_lhs} != {balance_rhs})"

        # Solar cap
        if entry["solar_used_kwh"] > effective_solar[h] + 0.01:
            return False, f"Hour {h}: solar_used exceeds effective solar"

        # Battery bounds
        e_after = entry["battery_energy_after_kwh"]
        effective_min = max(min_e, min_reserve_hours.get(h, 0))
        if e_after < effective_min - 0.01:
            return False, f"Hour {h}: battery below minimum ({e_after} < {effective_min})"
        if e_after > cap + 0.01:
            return False, f"Hour {h}: battery exceeds capacity"

        # Charge/discharge limits
        if entry["battery_action"] == "charge" and entry["battery_kwh"] > max_charge + 0.01:
            return False, f"Hour {h}: charge exceeds max"
        if entry["battery_action"] == "discharge" and entry["battery_kwh"] > max_discharge + 0.01:
            return False, f"Hour {h}: discharge exceeds max"

        # Directive constraints
        if h in no_charge_hours and entry["battery_action"] == "charge" and entry["battery_kwh"] > 0.01:
            return False, f"Hour {h}: charge during no_charge_window"
        if h in no_discharge_hours and entry["battery_action"] == "discharge" and entry["battery_kwh"] > 0.01:
            return False, f"Hour {h}: discharge during no_discharge_window"
        if h in max_grid_limits and entry["grid_kwh"] > max_grid_limits[h] + 0.01:
            return False, f"Hour {h}: grid exceeds max_grid limit"

    # Battery transition continuity
    prev_energy = init_e
    for entry in plan:
        expected = prev_energy
        if entry["battery_action"] == "charge":
            expected += entry["battery_kwh"]
        elif entry["battery_action"] == "discharge":
            expected -= entry["battery_kwh"]
        if abs(entry["battery_energy_after_kwh"] - expected) > 0.01:
            return False, f"Hour {entry['hour']}: battery transition mismatch"
        prev_energy = entry["battery_energy_after_kwh"]

    # End-of-day neutrality
    if abs(plan[-1]["battery_energy_after_kwh"] - init_e) > 0.01:
        return False, f"End-of-day battery {plan[-1]['battery_energy_after_kwh']} != initial {init_e}"

    # Verify totals
    recalc_grid = sum(e["grid_kwh"] for e in plan)
    recalc_cost = sum(e["grid_kwh"] * hours[e["hour"]]["tariff_bdt_per_kwh"] for e in plan)
    recalc_peak = max(e["grid_kwh"] for e in plan)

    if abs(recalc_grid - sum(e["grid_kwh"] for e in plan)) > 0.01:
        return False, "total_grid_kwh mismatch"

    return True, "ok"
