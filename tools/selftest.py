# -*- coding: utf-8 -*-
"""
Self test for core.py. Needs numpy only, no QGIS, so it runs in CI.

    python tools/selftest.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core  # noqa: E402  (path has to be set up first)

FAILURES = []


def check(name, condition, detail=""):
    print("  %-52s %s" % (name, "ok" if condition else "FAILED"))
    if detail:
        print("      %s" % detail)
    if not condition:
        FAILURES.append(name)


def sample_image(seed=0, size=128):
    """A nearly single colored image, the case the stretch is made for."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 1, (size, size))
    tint = rng.normal(0, 1, (size, size))
    red = 40 + 18 * base + 1.5 * tint
    green = 58 + 22 * base + 2.5 * tint
    blue = 36 + 15 * base + 0.4 * tint
    return np.clip(np.dstack([red, green, blue]), 0, 255).astype(np.uint8)


def test_color_space_roundtrips():
    rgb = sample_image().reshape(-1, 3).astype(np.float64)
    for name, forward, inverse in (("CIELAB", core.rgb_to_lab, core.lab_to_rgb),
                                   ("YUV", core.rgb_to_yuv, core.yuv_to_rgb)):
        error = np.abs(inverse(forward(rgb)) - rgb).max()
        check("%s round trip is lossless" % name, error < 1e-6,
              "max error %.2e" % error)


def test_decorrelation_is_exact():
    """The whole point: T @ C @ T.T must come out as scale^2 * I."""
    rgb = sample_image()
    data = core.rgb_to_lab(rgb.reshape(-1, 3))
    cov = np.cov(data - data.mean(axis=0), rowvar=False)

    scale = 25.0
    matrix, _ = core.transform_matrix(cov, scale)
    result = matrix @ cov @ matrix.T
    target = np.eye(3) * scale ** 2

    error = np.abs(result - target).max() / scale ** 2
    check("transform decorrelates to equal variance", error < 1e-9,
          "relative error %.2e" % error)

    off_diagonal = np.abs(result - np.diag(np.diag(result))).max()
    check("channels become uncorrelated", off_diagonal / scale ** 2 < 1e-9,
          "largest off-diagonal %.2e" % off_diagonal)


def test_output_shape_and_type():
    rgb = sample_image()
    result, info = core.dstretch(rgb, space="lab")
    check("output keeps shape and dtype",
          result.shape == rgb.shape and result.dtype == np.uint8)
    check("info reports the three eigenvalues",
          info["eigenvalues"].shape == (3,))
    check("info reports pixels used",
          info["pixels_used"] == rgb.shape[0] * rgb.shape[1])


def test_every_space_runs():
    rgb = sample_image()
    for space in core.SPACE_ORDER:
        result, info = core.dstretch(rgb, space=space)
        spread_before = rgb.reshape(-1, 3).astype(float).std(axis=0).mean()
        spread_after = result.reshape(-1, 3).astype(float).std(axis=0).mean()
        check("%s increases color spread" % space.upper(),
              spread_after > spread_before,
              "%.1f -> %.1f" % (spread_before, spread_after))


def midtone_image(seed=1, size=128):
    """A mid grey image whose stretched colors stay inside the RGB gamut."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 1, (size, size))
    tint = rng.normal(0, 1, (size, size))
    red = 128 + 6 * base + 1.0 * tint
    green = 130 + 7 * base + 1.6 * tint
    blue = 126 + 5 * base + 0.3 * tint
    return np.clip(np.dstack([red, green, blue]), 0, 255).astype(np.uint8)


def _clipped(rgb):
    flat = rgb.reshape(-1, 3)
    return (flat == 0).any(axis=1) | (flat == 255).any(axis=1)


def test_keep_luma_preserves_lightness():
    # Nothing is clipped here, so only 8 bit rounding stands between the
    # input and output lightness.
    rgb = midtone_image()
    before = core.rgb_to_lab(rgb.reshape(-1, 3))[:, 0]
    stretched, _ = core.dstretch(rgb, space="lab", keep_luma=True, gain=1.0)
    check("the test image stretches without clipping",
          not _clipped(stretched).any())

    error = np.abs(
        core.rgb_to_lab(stretched.reshape(-1, 3))[:, 0] - before).max()
    check("keep_luma preserves lightness exactly", error < 1.0,
          "max L difference %.3f" % error)

    free, _ = core.dstretch(rgb, space="lab", keep_luma=False, gain=1.0)
    free_error = np.abs(
        core.rgb_to_lab(free.reshape(-1, 3))[:, 0] - before).max()
    check("without keep_luma lightness does change", free_error > 1.0,
          "max L difference %.3f" % free_error)


def test_keep_luma_under_clipping():
    """
    A hard stretch pushes colors outside the RGB gamut. Those pixels get
    clipped, which does move their lightness - unavoidable, but the
    guarantee must still hold everywhere else.
    """
    rgb = sample_image()
    before = core.rgb_to_lab(rgb.reshape(-1, 3))[:, 0]
    hard, _ = core.dstretch(rgb, space="lab", keep_luma=True, gain=2.0)

    clipped = _clipped(hard)
    error = np.abs(
        core.rgb_to_lab(hard.reshape(-1, 3))[:, 0] - before)[~clipped].max()
    check("keep_luma holds for every unclipped pixel", error < 1.0,
          "max L difference %.3f across %.1f %% of the image"
          % (error, 100 * (~clipped).mean()))


def test_chunking_does_not_change_result():
    rgb = sample_image()
    whole, _ = core.dstretch(rgb, space="lab", chunk=10 ** 9)
    pieces, _ = core.dstretch(rgb, space="lab", chunk=777)
    check("chunked processing matches single pass",
          np.array_equal(whole, pieces))


def test_mask_excludes_pixels():
    rgb = sample_image()
    mask = np.ones(rgb.shape[:2], dtype=bool)
    mask[:, :40] = False

    polluted = rgb.copy()
    polluted[:, :40] = 255                      # blown out area, masked away

    masked, _ = core.dstretch(polluted, space="lab", mask=mask)
    clean, _ = core.dstretch(rgb, space="lab", mask=mask)
    check("masked pixels do not affect the statistics",
          np.array_equal(masked[:, 40:], clean[:, 40:]))

    unmasked, _ = core.dstretch(polluted, space="lab")
    check("without a mask they do affect it",
          not np.array_equal(unmasked[:, 40:], clean[:, 40:]))


def test_scale_and_gain():
    rgb = sample_image()
    _, fixed = core.dstretch(rgb, space="lab", scale=20.0, gain=1.0)
    check("explicit scale is used as given", abs(fixed["scale"] - 20.0) < 1e-9)

    _, geared = core.dstretch(rgb, space="lab", scale=20.0, gain=0.5)
    check("gain multiplies the target spread",
          abs(geared["scale"] - 10.0) < 1e-9)

    _, auto = core.dstretch(rgb, space="lab")
    check("automatic scale is positive", auto["scale"] > 0,
          "scale %.2f" % auto["scale"])


def test_flat_image_does_not_crash():
    flat = np.full((32, 32, 3), 77, dtype=np.uint8)
    result, _ = core.dstretch(flat, space="lab")
    check("a completely flat image is handled", result.shape == flat.shape)


def test_rejects_bad_input():
    rgb = sample_image(size=16)
    try:
        core.dstretch(rgb, space="hsv")
        check("unknown color space is rejected", False)
    except ValueError:
        check("unknown color space is rejected", True)

    try:
        core.dstretch(rgb, mask=np.zeros(rgb.shape[:2], dtype=bool))
        check("an empty mask is rejected", False)
    except ValueError:
        check("an empty mask is rejected", True)


def test_stretch_to_byte():
    band = np.linspace(-500, 3000, 10000).reshape(100, 100)
    scaled = core.stretch_to_byte(band)
    check("percentile stretch fills the 8 bit range",
          scaled.dtype == np.uint8 and scaled.min() == 0 and scaled.max() == 255,
          "range %d-%d" % (scaled.min(), scaled.max()))

    empty = core.stretch_to_byte(np.full((4, 4), np.nan))
    check("an all-nodata band does not crash", empty.shape == (4, 4))


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        print("%s:" % test.__name__[5:].replace("_", " "))
        test()
        print()

    if FAILURES:
        print("%d check(s) failed: %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
