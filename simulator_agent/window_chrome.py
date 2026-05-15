"""Detect the iOS surface bounding box within a captured macOS window image.

Both iPhone Mirroring and the iOS Simulator render the iOS device inside a
macOS window that has some chrome (mirroring app frame; simulated device
bezel). The chrome occupies pixels around the iOS surface; for input
coordinate translation we need the bounding box of the iOS surface within
the captured image.

The two window types have different chrome structures going inward from
an edge:

  iPhone Mirroring:  [light chrome] → [iOS content]
                     (one transition)

  iOS Simulator:     [macOS bg ~56] → [device bezel ~0 black] → [iOS surface]
                     (two transitions — outer shadow/window-bg, then bezel)

A naive "biggest luminance drop within max_inset" finds the outer transition
for sim and stops at the bezel rim instead of the iOS surface. A naive "any
sharp transition" gets confused by the Dynamic Island once it crosses into
the iOS surface.

This detector handles both by:
  1. Determining the chrome color from the first edge pixel.
  2. Walking inward until the chrome color stops (first uniform-chrome run).
  3. Continuing inward through any second chrome region (e.g., bezel),
     looking for the first window where pixels are *distinctly different*
     from chrome AND either varied (content) or sustained-bright (iOS).
  4. Taking the median across many sampled rows/columns for robustness.

Returns (left, top, right, bottom) pixel insets from each edge.
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image


def _lum(p: tuple[int, int, int]) -> float:
    return 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]


def _boundary(line: list[float], max_inset: int) -> int:
    """Find inward position where chrome ends and iOS surface begins.

    Returns 0 if no clear boundary detected within max_inset pixels.

    Robust to two chrome shapes:

      mirror:    [light chrome] → [iOS content]
                 one transition

      simulator: [macOS bg] → [device bezel] → [iOS surface]
                 two transitions; iOS surface is at the SECOND

    The algorithm: enumerate all significant luminance transitions in the
    scan, then return the LAST one that leads into a stable region (next 4
    pixels within ±50 of each other). The "stable region" filter rejects
    transitions that lead into a varied stripe — we want one that leads
    INTO sustained iOS-surface pixels.

    Per-sample this can be misled by iOS UI features inside the surface
    (e.g., the Dynamic Island generates an internal dark→light transition).
    Those are filtered by taking the median across many sampled rows/cols
    — the DI only intrudes on a minority of samples.
    """
    n = min(max_inset, len(line))
    if n < 8:
        return 0

    transitions: list[int] = []
    for i in range(1, n):
        if abs(line[i] - line[i - 1]) > 50:
            transitions.append(i)
    if not transitions:
        return 0

    # Keep transitions that lead into a stable region (next 4 px tight).
    valid: list[int] = []
    for t in transitions:
        end = min(t + 4, n)
        window = line[t:end]
        if len(window) < 2:
            continue
        if max(window) - min(window) <= 50:
            valid.append(t)
    if not valid:
        return 0
    return valid[-1]


def _median_or_zero(xs: list[int]) -> int:
    return int(statistics.median(xs)) if xs else 0


def detect_ios_surface(
    img: "Image.Image",
    max_inset: int = 80,
    sample_step: int = 8,
    edge_margin: int = 30,
) -> tuple[int, int, int, int]:
    """Detect (left, top, right, bottom) iOS-surface insets in a window image.

    Args:
      img: captured macOS window image (any chrome around iOS surface).
      max_inset: how far inward to scan from each edge. Should comfortably
        cover the chrome — 80 pixels is enough for mirror's thin chrome and
        the simulator's bezel (~25–55 px).
      sample_step: scan every Nth row/column for speed.
      edge_margin: skip this many pixels at the perpendicular edges when
        sampling — avoids corner artifacts (rounded device frame).

    Returns (left, top, right, bottom) in pixels. (0,0,0,0) on failure.
    """
    img = img.convert("RGB")
    px = img.load()
    w, h = img.size

    lefts: list[int] = []
    rights: list[int] = []
    tops: list[int] = []
    bottoms: list[int] = []

    for y in range(edge_margin, h - edge_margin, sample_step):
        row_l = [_lum(px[x, y]) for x in range(max_inset)]
        if (b := _boundary(row_l, max_inset)):
            lefts.append(b)
        row_r = [_lum(px[w - 1 - x, y]) for x in range(max_inset)]
        if (b := _boundary(row_r, max_inset)):
            rights.append(b)

    for x in range(edge_margin, w - edge_margin, sample_step):
        col_t = [_lum(px[x, y]) for y in range(max_inset)]
        if (b := _boundary(col_t, max_inset)):
            tops.append(b)
        col_b = [_lum(px[x, h - 1 - y]) for y in range(max_inset)]
        if (b := _boundary(col_b, max_inset)):
            bottoms.append(b)

    return (
        _median_or_zero(lefts),
        _median_or_zero(tops),
        _median_or_zero(rights),
        _median_or_zero(bottoms),
    )
