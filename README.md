# DStretch for QGIS

Decorrelation stretch applied to whatever is in front of you in the map
canvas. Surfaces that look uniform — forest, water, bare rock, farmland —
reveal vegetation boundaries, old ditches and roads, soil moisture and
traces in the ground that are invisible in the original image.

The method is the one NASA's Jet Propulsion Laboratory developed for
satellite imagery, and which archaeologists use through Jon Harman's
DStretch to recover rock paintings that the naked eye can no longer see:

**[NASA Spinoff — Manipulating Satellite Photos Now Reveals Ancient
Images](https://spinoff.nasa.gov/Manipulating_Satellite_Photos_Now_Reveals_Ancient_Images)**

---

## What it does

A photograph of forest is, in numbers, almost a single color. All the
variation sits on one axis — lightness — while the two color axes are
squeezed flat. Ordinary contrast enhancement stretches everything equally
and that flatness survives.

A decorrelation stretch instead finds the image's *own* color axes,
rescales each one to the same variance, and rotates back. The axis that
carried almost nothing is blown up to the same size as the dominant one,
so differences of one or two units in the original become plainly visible
colors.

The statistics readout in the dialog makes this concrete. On an aerial
photograph of dense forest the covariance eigenvalues typically come out
around `0.9, 7.5, 94` — the weakest color axis holds about 1 % of the
variance of the lightness axis. That ratio is exactly what the stretch
converts into visible signal.

## Installation

**From a release:** download the ZIP from
[Releases](https://github.com/marcuslindh/dstretch/releases), then
*Plugins ▸ Manage and Install Plugins ▸ Install from ZIP*.

**From source:** this repository *is* the plugin package, so clone it
straight into your QGIS profile — the target folder has to be named
`dstretch`:

```bash
# Windows
git clone https://github.com/marcuslindh/dstretch.git \
  "%APPDATA%/QGIS/QGIS3/profiles/default/python/plugins/dstretch"

# Linux
git clone https://github.com/marcuslindh/dstretch.git \
  ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/dstretch
```

Then enable *DStretch – decorrelation stretch* in the plugin manager.
Updating is a `git pull` and a QGIS restart.

No extra libraries are needed; the numpy and GDAL that ship with QGIS are
enough. Requires QGIS 3.22 or newer.

## Using it

Click the toolbar button, or *Raster ▸ DStretch ▸ Decorrelation
stretch…*. The dialog is modeless, so you can pan and zoom while it is
open — the preview follows the map canvas.

### Source

| Choice | What it reads |
| --- | --- |
| **Map canvas** | Renders everything visible, exactly as you see it: several layers, transparency and layer styling all included. Output resolution can be raised to 2× or 4× screen resolution. |
| **Raster layer** | Reads one chosen layer at its own resolution within the current view, and you pick which three bands act as R, G and B. Data that is not already 8 bit gets a 2–98 % percentile stretch first. |

### Presets

Five starting points to step between, with **Ctrl+←** and **Ctrl+→** or
the arrow buttons beside the list. Flipping through all five on the same
view takes a few seconds and is usually the fastest way to find out what
an area has to offer.

| Preset | Color space | Lightness | Gain | For |
| --- | --- | --- | --- | --- |
| Natural | CIELAB | kept | 0.35 | Still reads as an aerial photograph. For presentation. |
| **Vegetation** | CIELAB | kept | 1.00 | Separates conifers, broadleaf, grass and clear-cuts. The default. |
| Ground traces | CIELAB | kept | 2.00 | Ditches, old roads, building foundations, damp streaks. |
| False color | RGB | recomputed | 1.00 | Maximum separation. Least natural, easiest to spot faint structure in. |
| Soft (YUV) | YUV | kept | 1.20 | Middle ground, when CIELAB gives too little and false color too much. |

Change any field and the list switches to **Custom**, keeping your
values. Ctrl+← and Ctrl+→ take you back to the presets, continuing from
the one you started out from.

### Settings

- **Color space** — `CIELAB` is the first choice and corresponds to
  DStretch's LDS mode. `RGB` separates hardest but looks least natural.
  `YUV` sits in between.
- **Keep the original lightness** — only color is stretched, terrain
  structure and shadows are left alone. This is DStretch's LDS and YDS
  modes, and almost always what you want on aerial imagery. At high gain
  some stretched colors land outside the RGB gamut and get clipped, which
  does shift the lightness of those particular pixels — unavoidable, and
  a fair sign that the gain is higher than the image can carry.
- **Gain** — how hard the stretch hits. Around `0.3–0.5` still looks
  like a photograph; `1.0` and above gives clear false colors that are
  better for hunting structures.
- **Target spread** — the standard deviation each principal component is
  rescaled to. Automatic mode sets it to three times the image's own mean
  spread. **Lock it manually when you want to compare several views**:
  in automatic mode the stretch adapts to each view, so two screenshots
  are not directly comparable.
- **Exclude transparent pixels and nodata** — keeps empty areas out of
  the statistics so they do not drag the mean around.

### The statistics readout

Below the preview you get the covariance eigenvalues and the ratio
between the strongest and weakest color axis. A large ratio means the
image is nearly single colored — that is where the stretch has the most
to give. A small ratio means the image already has good color variation,
and a lower gain will usually be more useful than a higher one.

### Result

*Create layer* writes a georeferenced GeoTIFF (DEFLATE compressed, with
an alpha band when there are transparent areas) and adds it to the
project, named after the preset — for example `dstretch_ground_traces`.
Leave the file path empty and it goes to a temporary file.

To get a PNG instead, right-click the result layer ▸ *Export ▸ Save As*.

## How the algorithm works

1. The pixels are treated as points in a 3D color space.
2. The covariance matrix of the color channels is computed.
3. The matrix is diagonalized with the Karhunen–Loève transform,
   `C = V · diag(d) · Vᵀ`. The eigenvectors are the image's own principal
   color axes.
4. Each principal component is rescaled to the same standard deviation,
   `s / √dᵢ`. This is where the correlation between channels disappears
   and small color differences are magnified.
5. The result is rotated back into the original color space and the mean
   is restored.

Steps 3–5 collapse into a single 3×3 matrix, `T = V · diag(s/√dᵢ) · Vᵀ`,
applied per pixel — the same optimization the NASA article describes,
where an expensive algebraic procedure was replaced by plain matrix
multiplication.

Optionally the luminance channel is copied back from the input
afterwards, which is what keeps terrain structure identical to the
original while colors are stretched.

## Using the core standalone

`core.py` has no QGIS dependencies, so it works as an ordinary numpy
module:

```python
import numpy as np
from PIL import Image
from dstretch.core import dstretch

image = np.asarray(Image.open("orthophoto.png").convert("RGB"))
result, info = dstretch(image, space="lab", keep_luma=True, gain=0.35)
print(info["eigenvalues"], info["scale"])
Image.fromarray(result).save("stretched.png")
```

Statistics are computed from an evenly spread subsample (2 million pixels
by default) and the transform is applied in blocks, so memory use stays
bounded on large images.

## Translations

Source strings are English. A Swedish translation ships in `i18n/` and is
picked automatically when QGIS runs in Swedish.

Qt's `lrelease` is not part of the QGIS Windows installation, so
`tools/build_qm.py` compiles `.ts` files into `.qm` instead:

```bash
python tools/build_qm.py              # all .ts files
python tools/build_qm.py i18n/x.ts    # one file
```

The compiled `.qm` is committed, because QGIS installs the plugin
straight from the repository. CI fails the build if it has drifted out of
sync with the `.ts`.

To refresh the string list after editing the code:

```bash
python -m PyQt5.pylupdate_main dialog.py plugin.py qgis_io.py \
    -ts i18n/dstretch_sv.ts
```

Note that `pylupdate5` does not pick up the preset strings — they are
marked with `QT_TRANSLATE_NOOP` in a module-level list — and it files the
`qgis_io.py` strings under `@default` rather than the `DStretch` context
they are actually translated in. Check those by hand after regenerating.

To add another language, copy `dstretch_sv.ts`, change the `language`
attribute and the translations, and run the build script.

## Development

The repository root is the plugin package, so QGIS loads it as-is with no
build step. `tools/` and `.github/` are repository scaffolding and are
left out of the packaged plugin.

```
__init__.py        classFactory, the QGIS entry point
plugin.py          toolbar and menu wiring, translator loading
dialog.py          the dialog, presets, preview
core.py            the algorithm, pure numpy, no QGIS imports
qgis_io.py         map canvas and raster reading, GeoTIFF writing
i18n/              dstretch_sv.ts and the compiled dstretch_sv.qm
tools/
  selftest.py      tests core.py, needs numpy only
  build_qm.py      .ts -> .qm compiler, stands in for lrelease
  build_zip.py     packages the repository for "Install from ZIP"
```

```bash
python tools/selftest.py     # 22 checks, no QGIS required
python tools/build_qm.py     # recompile translations
python tools/build_zip.py    # -> build/dstretch-<version>.zip
```

The self test covers the algorithm rather than the GUI: color space round
trips, that `T·C·Tᵀ` really does come out as `s²·I`, that chunked
processing matches a single pass, that masking excludes pixels from the
statistics, and the lightness guarantee above.

CI runs the self test on every push, checks that the committed `.qm`
matches the `.ts`, and builds the package. Pushing a `v*` tag that
matches `version=` in `metadata.txt` publishes a release with the ZIP
attached.

## License

MIT. See [LICENSE](LICENSE).

## Credits

Decorrelation stretch was developed at NASA's Jet Propulsion Laboratory
for satellite imagery. [DStretch](https://www.dstretch.com/), by Jon
Harman, brought it to rock art research and gave the technique the custom
color spaces this plugin's modes are modelled on. The NASA Spinoff
article linked at the top tells that story.
