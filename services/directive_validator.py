from typing import List

VALID_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


def validate_hours(hours: list[int], field_name: str = "hours") -> tuple[bool, str]:
    """Validate hours array: must be unique integers 0-23 in ascending order."""
    if not isinstance(hours, list):
        return False, f"{field_name} must be a list"

    for h in hours:
        if not isinstance(h, int):
            return False, f"All {field_name} must be integers, got {type(h).__name__}"
        if h < 0 or h > 23:
            return False, f"Hour {h} out of range 0-23"

    if len(hours) != len(set(hours)):
        return False, f"{field_name} must contain unique values"

    if hours != sorted(hours):
        return False, f"{field_name} must be in ascending order"

    return True, "ok"


def validate_directive(directive: dict, note_index: int) -> tuple[bool, str]:
    """Validate a single directive interpretation entry."""
    # Check required fields
    required = ["note_index", "applies", "directive_type", "structured_adjustment", "explanation"]
    for field in required:
        if field not in directive:
            return False, f"Missing field: {field}"

    # Validate note_index
    if directive["note_index"] != note_index:
        return False, f"note_index mismatch: expected {note_index}, got {directive['note_index']}"

    # Validate directive_type
    dt = directive["directive_type"]
    if dt not in VALID_DIRECTIVE_TYPES:
        return False, f"Invalid directive_type: {dt}"

    applies = directive["applies"]
    adj = directive["structured_adjustment"]

    # no_op must have applies=false and adjustment=null
    if dt == "no_op":
        if applies is not False:
            return False, "no_op must have applies=false"
        if adj is not None:
            return False, "no_op must have structured_adjustment=null"
        return True, "ok"

    # All other directives must have applies=true
    if applies is not True:
        return False, f"{dt} must have applies=true"

    if adj is None:
        return False, f"{dt} must have structured_adjustment (not null)"

    # Validate by type
    if dt == "solar_reduction":
        if "hours" not in adj or "factor" not in adj:
            return False, "solar_reduction requires hours and factor"
        ok, msg = validate_hours(adj["hours"])
        if not ok:
            return False, msg
        factor = adj["factor"]
        if not isinstance(factor, (int, float)) or factor < 0 or factor > 1:
            return False, f"factor must be 0-1, got {factor}"

    elif dt == "minimum_battery_reserve":
        if "hours" not in adj or "minimum_energy_kwh" not in adj:
            return False, "minimum_battery_reserve requires hours and minimum_energy_kwh"
        ok, msg = validate_hours(adj["hours"])
        if not ok:
            return False, msg
        min_kwh = adj["minimum_energy_kwh"]
        if not isinstance(min_kwh, (int, float)) or min_kwh < 0:
            return False, f"minimum_energy_kwh must be non-negative, got {min_kwh}"

    elif dt == "no_charge_window":
        if "hours" not in adj:
            return False, "no_charge_window requires hours"
        ok, msg = validate_hours(adj["hours"])
        if not ok:
            return False, msg

    elif dt == "no_discharge_window":
        if "hours" not in adj:
            return False, "no_discharge_window requires hours"
        ok, msg = validate_hours(adj["hours"])
        if not ok:
            return False, msg

    elif dt == "max_grid_window":
        if "hours" not in adj or "max_grid_kwh" not in adj:
            return False, "max_grid_window requires hours and max_grid_kwh"
        ok, msg = validate_hours(adj["hours"])
        if not ok:
            return False, msg
        max_kw = adj["max_grid_kwh"]
        if not isinstance(max_kw, (int, float)) or max_kw < 0:
            return False, f"max_grid_kwh must be non-negative, got {max_kw}"

    return True, "ok"


def validate_directives(directives: list[dict], num_notes: int) -> tuple[bool, str, list[dict]]:
    """Validate all directive interpretations. Returns (valid, error_msg, cleaned_directives)."""
    if len(directives) != num_notes:
        return False, f"Expected {num_notes} directives, got {len(directives)}", []

    cleaned = []
    for i, directive in enumerate(directives):
        ok, msg = validate_directive(directive, i)
        if not ok:
            return False, f"Note {i}: {msg}", []
        cleaned.append(directive)

    return True, "ok", cleaned
