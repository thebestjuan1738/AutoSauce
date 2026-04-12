"""
sauce_config.py

Single source of truth for all physical parameters.
These are the only values you touch when tuning the machine.

POSITIONS         — named locations along the rail (in mm from home end)
COVERAGE_PROFILES — per-level conveyor speed and duration

Scaling to multiple sauces later:
  - Add entries to SAUCE_DOCKS mapping sauce name → dock position mm
  - order_manager.py needs no changes, sauce_config is the only file to update
"""

# ─── Rail positions (mm from the dock end of the rail) ────────────────────────
# Tune these once you have the physical rail assembled.
#
#   DOCK end ─────────────── HOME ──────────── DISPENSE end
#   0mm                     ~175mm             ~350mm
#
POSITIONS = {
    "dock":     0,      # where the sauce dispenser sits when not in use
    "home":     175,    # resting position between orders (middle of rail)
    "dispense": 350,    # over the conveyor belt / sandwich
}

# ─── Gripper (encoder-based, no timing config needed) ────────────────────────
# Tune _CLOSE_TARGET_TICKS in pi/motion/gripper.py if grip depth needs adjusting.

# ─── Coverage profiles ────────────────────────────────────────────────────────
# conveyor_speed : 0–100 abstract speed unit, maps to PWM duty cycle in driver
# conveyor_ms    : how long the conveyor runs (sandwich must fully clear)
#
# Extruder dispenses a fixed encoder-position amount (see _EXTRUDER_DISPENSE_TARGET_TICKS
# in pi/motion/extruder.py). Extruder finishes first; conveyor keeps running until conveyor_ms.
#
COVERAGE_PROFILES = {
    "light":  {"conveyor_speed": 80, "conveyor_ms": 3000},
    "medium": {"conveyor_speed": 50, "conveyor_ms": 4500},
    "heavy":  {"conveyor_speed": 25, "conveyor_ms": 6000},
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
