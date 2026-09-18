from pulp import LpProblem, LpMinimize, LpVariable, lpSum, LpStatus, PULP_CBC_CMD
from typing import List


def build_effective_solar(hours: list[dict], directives: list[dict]) -> list[float]:
    """Apply solar_reduction directives to get effective solar per hour."""
    effective = [h["solar_kwh"] for h in hours]

    for d in directives:
        if d.get("directive_type") == "solar_reduction" and d.get("applies"):
            adj = d["structured_adjustment"]
            if adj:
                for h in adj["hours"]:
                    effective[h] = effective[h] * adj["factor"]

    return effective


def optimize(hours: list[dict], battery: dict, directives: list[dict]) -> dict:
    """Run PuLP linear programming optimizer to minimize grid cost."""
    HOURS = 24
    demand = [h["demand_kwh"] for h in hours]
    tariff = [h["tariff_bdt_per_kwh"] for h in hours]
    effective_solar = build_effective_solar(hours, directives)

    cap = battery["capacity_kwh"]
    init_e = battery["initial_energy_kwh"]
    min_e = battery["minimum_energy_kwh"]
    max_charge = battery["max_charge_kwh_per_hour"]
    max_discharge = battery["max_discharge_kwh_per_hour"]

    # Parse directives into hour sets
    no_charge_hours = set()
    no_discharge_hours = set()
    max_grid = {}  # hour -> max_kwh
    min_reserve = {}  # hour -> min_kwh

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
                max_grid[h] = adj["max_grid_kwh"]
        elif dt == "minimum_battery_reserve":
            for h in adj["hours"]:
                min_reserve[h] = max(min_reserve.get(h, 0), adj["minimum_energy_kwh"])

    # Create problem
    prob = LpProblem("GridWise_Optimizer", LpMinimize)

    # Decision variables
    grid = [LpVariable(f"grid_{h}", 0, None) for h in range(HOURS)]
    solar_used = [LpVariable(f"solar_{h}", 0, None) for h in range(HOURS)]
    charge = [LpVariable(f"charge_{h}", 0, max_charge) for h in range(HOURS)]
    discharge = [LpVariable(f"discharge_{h}", 0, max_discharge) for h in range(HOURS)]

    # Battery energy after each hour
    battery_bounds = [(min_reserve.get(h, min_e), cap) for h in range(HOURS)]
    battery_energy = [
        LpVariable(f"energy_{h}", battery_bounds[h][0], battery_bounds[h][1])
        for h in range(HOURS)
    ]

    # Objective: minimize total grid cost
    prob += lpSum([grid[h] * tariff[h] for h in range(HOURS)])

    # Constraints per hour
    for h in range(HOURS):
        # Energy balance: grid + solar_used + discharge = demand + charge
        prob += grid[h] + solar_used[h] + discharge[h] == demand[h] + charge[h], f"balance_{h}"

        # Solar usage cannot exceed effective solar
        prob += solar_used[h] <= effective_solar[h], f"solar_cap_{h}"

        # Battery transition
        if h == 0:
            prob += battery_energy[h] == init_e + charge[h] - discharge[h], f"battery_{h}"
        else:
            prob += battery_energy[h] == battery_energy[h - 1] + charge[h] - discharge[h], f"battery_{h}"

        # No-charge constraint
        if h in no_charge_hours:
            prob += charge[h] == 0, f"no_charge_{h}"

        # No-discharge constraint
        if h in no_discharge_hours:
            prob += discharge[h] == 0, f"no_discharge_{h}"

        # Max grid constraint
        if h in max_grid:
            prob += grid[h] <= max_grid[h], f"max_grid_{h}"

    # End-of-day battery neutrality
    prob += battery_energy[23] == init_e, "end_of_day_neutral"

    # Solve
    solver = PULP_CBC_CMD(msg=0)
    status = prob.solve(solver)

    if LpStatus[status] != "Optimal":
        raise ValueError(f"Optimizer failed: {LpStatus[status]}")

    # Extract results
    result = []
    for h in range(HOURS):
        g = grid[h].varValue or 0
        s = solar_used[h].varValue or 0
        c = charge[h].varValue or 0
        d = discharge[h].varValue or 0
        e = battery_energy[h].varValue or 0

        if c > 0.001:
            action = "charge"
            batt_kwh = c
        elif d > 0.001:
            action = "discharge"
            batt_kwh = d
        else:
            action = "idle"
            batt_kwh = 0

        result.append({
            "hour": h,
            "grid_kwh": round(g, 4),
            "solar_used_kwh": round(s, 4),
            "battery_action": action,
            "battery_kwh": round(batt_kwh, 4),
            "battery_energy_after_kwh": round(e, 4),
        })

    total_grid = sum(r["grid_kwh"] for r in result)
    total_cost = sum(r["grid_kwh"] * tariff[h] for h, r in enumerate(result))
    peak_grid = max(r["grid_kwh"] for r in result)

    return {
        "hourly_plan": result,
        "total_grid_kwh": round(total_grid, 4),
        "total_cost_bdt": round(total_cost, 4),
        "peak_grid_kwh": round(peak_grid, 4),
    }
