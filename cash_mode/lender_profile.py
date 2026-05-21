"""Backward-compat shim — all names re-exported from staker_profile.

This module was renamed to `cash_mode/staker_profile.py` (vocabulary
unification: staker↔borrower replaces lender↔borrower). Import from
`cash_mode.staker_profile` in new code; this shim keeps old imports
working during the migration window.
"""

from cash_mode.staker_profile import (  # noqa: F401
    BorrowerProfile,
    BORROWER_PROFILE_DEFAULTS,
    compute_default_willingness_threshold,
    LenderProfile,
    LENDER_PROFILE_DEFAULTS,
    StakerProfile,
    STAKER_PROFILE_DEFAULTS,
    WILLINGNESS_THRESHOLD_BASE,
    WILLINGNESS_THRESHOLD_MAX,
    WILLINGNESS_THRESHOLD_MIN,
    WILLINGNESS_THRESHOLD_SLOPE,
)

__all__ = [
    "BorrowerProfile",
    "BORROWER_PROFILE_DEFAULTS",
    "compute_default_willingness_threshold",
    "LenderProfile",
    "LENDER_PROFILE_DEFAULTS",
    "StakerProfile",
    "STAKER_PROFILE_DEFAULTS",
    "WILLINGNESS_THRESHOLD_BASE",
    "WILLINGNESS_THRESHOLD_MAX",
    "WILLINGNESS_THRESHOLD_MIN",
    "WILLINGNESS_THRESHOLD_SLOPE",
]
