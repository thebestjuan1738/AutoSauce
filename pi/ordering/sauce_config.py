"""
sauce_config.py

Single source of truth for all physical parameters.
These are the only values you touch when tuning the machine.

POSITIONS       — named locations along the rail (in mm from home end)
GRIPPER         — how long to run the gripper motor in each direction
COVERAGE_PROFILES — per-level conveyor speed and extrude/conveyor durations

Scaling to multiple sauces later:
  - Add entries to SAUCE_DOCKS mapping sauce name → dock position mm
  - order_manager.py needs no changes, sauce_config is the only file to update
"""

# ─── Rail positions (mm from the dock end of the rail) ────────────────────────
# Tune these once you have the physical rail assembled.
#
#   DOCK end ─────────────────── HOME ─────────────── DISPENSE end
#   0mm                         ~500mm                ~1000mm
#
POSITIONS = {
    "dock":     0,      # where the sauce dispenser sits when not in use
    "home":     500,    # resting position between orders (middle of rail)
    "dispense": 1000,   # over the conveyor belt / sandwich
}

# ─── Gripper (brushed DC motor, timed open/close) ─────────────────────────────
# The gripper motor runs forward to close, reverse to open.
# Tune these so the gripper fully engages/releases without stalling.
GRIPPER = {
    "close_ms": 500,
    "open_ms":  500,
}

# ─── Coverage profiles ────────────────────────────────────────────────────────
# conveyor_speed : 0–100 abstract speed unit, maps to PWM duty cycle in driver
# extrude_ms     : how long the extruder motor runs
# conveyor_ms    : how long the conveyor runs (longer — sandwich must fully clear)
#
# Conveyor and extruder START together. Extruder finishes first.
# Conveyor keeps running until conveyor_ms elapses, then stops.
#
COVERAGE_PROFILES = {
    "light":  {"conveyor_speed": 80, "extrude_ms":  600, "conveyor_ms": 3000},
    "medium": {"conveyor_speed": 50, "extrude_ms":  600, "conveyor_ms": 4500},
    "heavy":  {"conveyor_speed": 25, "extrude_ms":  600, "conveyor_ms": 6000},
}

# ─── Future: multiple sauce docks ─────────────────────────────────────────────
# When you add more sauces, map each name to its dock position on the rail.
# The rest of the codebase reads from here — nothing else needs to change.
#
# SAUCE_DOCKS = {
#     "mayo":     0,
#     "mustard":  50,
#     "ketchup":  100,
# }

# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_coverage_levels() -> list[str]:
    """Return valid coverage level names, in order. Used by the UI."""
    return list(COVERAGE_PROFILES.keys())


def get_profile(level: str) -> dict:
    """
    Return the coverage profile for a given level.
    Raises ValueError with a clear message if the level is unknown.
    """
    if level not in COVERAGE_PROFILES:
        raise ValueError(
            f"Unknown coverage level '{level}'. "
            f"Valid options: {get_coverage_levels()}"
        )
    return COVERAGE_PROFILES[level]
