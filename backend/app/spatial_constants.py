"""Locked ship-1 constants for the spatial music scene / preview engine.

Coordinate convention: right-handed; listener at origin facing +Z (front);
azimuth 0° = front; positive azimuth = left (CCW); elevation + = up.

FOA authority is Python compile only — no JS FOA encoder twin.
Motion changes SpatialMix position only — never note ticks or Transport.bpm.
Fingerprint algorithm is ``composition_snapshot_fingerprint``.
"""

from __future__ import annotations

import logging
import math
from typing import Literal

logger = logging.getLogger(__name__)

ENGINE_VERSION: str = "spatial.preview.v1.0"
SCENE_SCHEMA_VERSION: Literal["spatial.scene.v1"] = "spatial.scene.v1"
PREVIEW_SCHEMA_VERSION: Literal["spatial.preview.v1"] = "spatial.preview.v1"

# Source / motion hard caps (Task 1 freeze).
MAX_SOURCES: int = 32
MAX_MOTION_KEYFRAMES: int = 64

# Distance model.
D_REF: float = 1.0
D_MAX: float = 100.0
DISTANCE_GAIN_MIN: float = 0.05
DISTANCE_GAIN_MAX: float = 1.0

# SpatialMix numeric ranges.
AZIMUTH_DEG_MIN: float = -180.0
AZIMUTH_DEG_MAX: float = 180.0
ELEVATION_DEG_MIN: float = -90.0
ELEVATION_DEG_MAX: float = 90.0
SPREAD_MIN: float = 0.0
SPREAD_MAX: float = 1.0
GAIN_MIN: float = 0.0
GAIN_MAX: float = 1.0

# FOA SN3D W channel.
FOA_W_SN3D: float = 1.0 / math.sqrt(2.0)

# Mild elevation attenuation for stereo path (Task 1 freeze).
# stereo_elevation_factor = 1 - ELEVATION_STEREO_ATTEN * |el|/90
ELEVATION_STEREO_ATTEN: float = 0.15

SourceKind = Literal["track", "stem"]
SOURCE_KINDS: frozenset[str] = frozenset({"track", "stem"})

PresetId = Literal["front_stereo", "circle_ensemble", "close_intimate"]
CATALOG_PRESET_IDS: tuple[str, ...] = (
    "front_stereo",
    "circle_ensemble",
    "close_intimate",
)

# Stem-set fingerprint: sha256 of sorted joined prefixes, hex length.
STEM_SET_FINGERPRINT_HEX_LEN: int = 64


def distance_gain(distance: float) -> float:
    """``clamp(D_REF / max(distance, D_REF), DISTANCE_GAIN_MIN, DISTANCE_GAIN_MAX)``."""
    d = float(distance)
    if d <= 0.0:
        d = D_REF
    raw = D_REF / max(d, D_REF)
    return max(DISTANCE_GAIN_MIN, min(DISTANCE_GAIN_MAX, raw))


def stereo_equal_power_gains(
    azimuth_deg: float,
    elevation_deg: float,
    *,
    distance: float,
    spread: float,
    gain: float = 1.0,
) -> tuple[float, float, float]:
    """Equal-power L/R from azimuth × elevation atten × distance_gain × spread.

    Azimuth 0° = front; +azimuth = left (CCW). Pan angle maps so:
    - front (0°) → equal L/R
    - left (+90°) → full left
    - right (−90°) → full right
    - rear (±180°) → equal L/R (rear image collapses in stereo)

    Spread widens toward center-fill (L/R move toward equal).
    Returns ``(left_gain, right_gain, distance_gain)``.
    """
    az = max(AZIMUTH_DEG_MIN, min(AZIMUTH_DEG_MAX, float(azimuth_deg)))
    el = max(ELEVATION_DEG_MIN, min(ELEVATION_DEG_MAX, float(elevation_deg)))
    sp = max(SPREAD_MIN, min(SPREAD_MAX, float(spread)))
    g = max(GAIN_MIN, min(GAIN_MAX, float(gain)))
    d_gain = distance_gain(distance)

    # Map azimuth so 0° front → pan=0.5; +90° left → pan=0; −90° right → pan=1.
    # pan in [0,1] where 0=full left, 1=full right (Web Audio / equal-power).
    # Using: pan = 0.5 - 0.5 * sin(az)  → left (+90) = 0, right (−90) = 1, front = 0.5
    az_rad = math.radians(az)
    pan = 0.5 - 0.5 * math.sin(az_rad)
    pan = max(0.0, min(1.0, pan))

    # Equal-power: left = cos(pan * π/2), right = sin(pan * π/2)
    angle = pan * (math.pi / 2.0)
    left = math.cos(angle)
    right = math.sin(angle)

    # Mild elevation attenuation (both channels).
    el_factor = 1.0 - ELEVATION_STEREO_ATTEN * (abs(el) / 90.0)
    left *= el_factor
    right *= el_factor

    # Spread → center-fill: blend toward equal (√0.5 each).
    if sp > 0.0:
        center = math.sqrt(0.5)
        left = left * (1.0 - sp) + center * sp
        right = right * (1.0 - sp) + center * sp

    left *= d_gain * g
    right *= d_gain * g
    return left, right, d_gain


def foa_acn_sn3d(
    azimuth_deg: float,
    elevation_deg: float,
    *,
    distance: float,
    spread: float,
    gain: float = 1.0,
) -> tuple[float, float, float, float, float]:
    """First-order Ambisonic ACN/SN3D coefficients (W, Y, Z, X).

    ``W = 1/√2``, ``Y = sin(az) cos(el)``, ``Z = sin(el)``, ``X = cos(az) cos(el)``
    then × distance_gain × source gain. Spread reduces directional energy toward W.
    Returns ``(w, y, z, x, distance_gain)``.
    """
    az = max(AZIMUTH_DEG_MIN, min(AZIMUTH_DEG_MAX, float(azimuth_deg)))
    el = max(ELEVATION_DEG_MIN, min(ELEVATION_DEG_MAX, float(elevation_deg)))
    sp = max(SPREAD_MIN, min(SPREAD_MAX, float(spread)))
    g = max(GAIN_MIN, min(GAIN_MAX, float(gain)))
    d_gain = distance_gain(distance)

    az_rad = math.radians(az)
    el_rad = math.radians(el)
    cos_el = math.cos(el_rad)
    w = FOA_W_SN3D
    y = math.sin(az_rad) * cos_el
    z = math.sin(el_rad)
    x = math.cos(az_rad) * cos_el

    # Spread reduces directional energy toward W (omni).
    if sp > 0.0:
        y *= 1.0 - sp
        z *= 1.0 - sp
        x *= 1.0 - sp

    scale = d_gain * g
    return w * scale, y * scale, z * scale, x * scale, d_gain


def log_engine_version() -> None:
    """DEBUG-only engine_version emission for tests / boot probes."""
    logger.debug(
        "Spatial preview engine_version",
        extra={"engine_version": ENGINE_VERSION},
    )
