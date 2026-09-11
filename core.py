# -*- coding: utf-8 -*-
"""
Decorrelation stretch.

Pure numpy implementation with no QGIS dependencies, so it can be used and
tested standalone. This is the decorrelation stretch NASA's Jet Propulsion
Laboratory developed for satellite imagery, and which DStretch (Jon Harman)
applies to rock art:

  1. The image is treated as N points in a 3D color space (RGB, CIELAB, YUV).
  2. The covariance matrix C of the color channels is computed.
  3. C is diagonalized (the Karhunen-Loeve transform): C = V @ diag(d) @ V.T.
  4. Each principal component is rescaled to the same standard deviation,
     scale / sqrt(d_i). The correlation between channels disappears and small
     color differences are magnified.
  5. Rotate back and restore the mean. The whole chain collapses into a
     single 3x3 matrix applied per pixel.

See https://spinoff.nasa.gov/Manipulating_Satellite_Photos_Now_Reveals_Ancient_Images
"""

import numpy as np

# --------------------------------------------------------------------------
# Color spaces
# --------------------------------------------------------------------------
_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]])
_M_XYZ2RGB = np.linalg.inv(_M_RGB2XYZ)
_WHITE = np.array([0.95047, 1.00000, 1.08883])          # D65

_M_RGB2YUV = np.array([[0.299, 0.587, 0.114],
                       [-0.14713, -0.28886, 0.436],
                       [0.615, -0.51499, -0.10001]])
_M_YUV2RGB = np.linalg.inv(_M_RGB2YUV)


def _srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def rgb_to_lab(rgb):
    """rgb: (N,3) in [0,255] -> CIELAB (L 0..100, a/b roughly -128..127)."""
    lin = _srgb_to_linear(np.asarray(rgb, dtype=np.float64) / 255.0)
    xyz = lin @ _M_RGB2XYZ.T / _WHITE
    eps, kappa = 216 / 24389, 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    fx, fy, fz = f[:, 0], f[:, 1], f[:, 2]
    return np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], axis=1)


def lab_to_rgb(lab):
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    fy = (L + 16) / 116
    f = np.stack([fy + a / 500, fy, fy - b / 200], axis=1)
    eps, kappa = 216 / 24389, 24389 / 27
    f3 = f ** 3
    xyz = np.where(f3 > eps, f3, (116 * f - 16) / kappa) * _WHITE
    return _linear_to_srgb(xyz @ _M_XYZ2RGB.T) * 255.0


def rgb_to_yuv(rgb):
    return np.asarray(rgb, dtype=np.float64) @ _M_RGB2YUV.T


def yuv_to_rgb(yuv):
    return yuv @ _M_YUV2RGB.T


def _rgb_identity(rgb):
    return np.asarray(rgb, dtype=np.float64)


# key: (forward, inverse, luminance channel index, label)
SPACES = {
    "rgb": (_rgb_identity, lambda x: x, None, "RGB"),
    "lab": (rgb_to_lab, lab_to_rgb, 0, "CIELAB"),
    "yuv": (rgb_to_yuv, yuv_to_rgb, 0, "YUV"),
}
SPACE_ORDER = ["lab", "rgb", "yuv"]


# --------------------------------------------------------------------------
# The stretch itself
# --------------------------------------------------------------------------
def transform_matrix(cov, scale):
    """Return (T, eigenvalues) where T is the complete decorrelation matrix."""
    evals, evecs = np.linalg.eigh(cov)            # covariance is symmetric
    safe = np.maximum(evals, 1e-12)               # guard against flat axes
    stretch = np.diag(scale / np.sqrt(safe))      # equal variance per PC
    return evecs @ stretch @ evecs.T, evals       # rotate -> stretch -> back


def dstretch(rgb, space="lab", keep_luma=True, gain=1.0, scale=None,
             mask=None, sample_limit=2_000_000, chunk=1_000_000):
    """
    rgb           HxWx3 uint8 (or anything castable to it)
    space         'rgb' | 'lab' | 'yuv'
    keep_luma     preserve the original lightness (L or Y), giving the
                  DStretch LDS/YDS modes: structure is untouched and only
                  the color is stretched
    gain          multiplier applied to the target spread
    scale         target standard deviation per channel; None = automatic
    mask          HxW bool, True = pixel takes part in the statistics
    sample_limit  how many pixels the statistics are computed from
    chunk         pixels processed per block, to bound memory use

    Returns (HxWx3 uint8, info dict).
    """
    if space not in SPACES:
        raise ValueError("unknown color space: %s" % space)
    forward, inverse, luma_index, label = SPACES[space]

    rgb = np.asarray(rgb)
    height, width = rgb.shape[0], rgb.shape[1]
    flat = rgb.reshape(-1, 3)
    count = flat.shape[0]

    # ---- 1. pick the pixels the statistics are based on -----------------
    if mask is not None:
        index = np.flatnonzero(mask.reshape(-1))
    else:
        index = np.arange(count)
    if index.size == 0:
        raise ValueError("no valid pixels to compute statistics from")
    if index.size > sample_limit:                 # evenly spread subset
        index = index[np.linspace(0, index.size - 1,
                                  sample_limit).astype(np.int64)]

    sample = forward(flat[index])

    # ---- 2-3. mean, covariance, diagonalization -------------------------
    mean = sample.mean(axis=0)
    cov = np.cov(sample - mean, rowvar=False)
    if scale is None:                             # auto: 3x the own spread
        scale = float(np.mean(sample.std(axis=0))) * 3.0
    scale = float(scale) * float(gain)

    matrix, evals = transform_matrix(cov, scale)

    # ---- 4-5. apply, in blocks to keep memory use down ------------------
    out = np.empty((count, 3), dtype=np.uint8)
    for start in range(0, count, chunk):
        stop = min(start + chunk, count)
        block = forward(flat[start:stop])
        result = (block - mean) @ matrix.T + mean
        if keep_luma and luma_index is not None:
            result[:, luma_index] = block[:, luma_index]
        out[start:stop] = np.clip(inverse(result), 0, 255).astype(np.uint8)

    info = {
        "space": label,
        "scale": scale,
        "eigenvalues": evals,
        "mean": mean,
        "matrix": matrix,
        "pixels_used": int(index.size),
    }
    return out.reshape(height, width, 3), info


def stretch_to_byte(band, mask=None, low=2.0, high=98.0):
    """
    Scale an arbitrary raster band to 0-255 using percentile clipping.
    Used when the source raster is not already 8 bit, so that the
    decorrelation stretch gets sensible input.
    """
    band = np.asarray(band, dtype=np.float64)
    valid = band[mask] if mask is not None else band[np.isfinite(band)]
    if valid.size == 0:
        return np.zeros(band.shape, dtype=np.uint8)
    lo, hi = np.percentile(valid, [low, high])
    if hi <= lo:
        hi = lo + 1.0
    return np.clip((band - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
