# -*- coding: utf-8 -*-
"""The DStretch plugin dialog."""

import os

import numpy as np

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, Qt, QTimer
from qgis.PyQt.QtGui import QImage, QKeySequence, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QShortcut,
    QSpinBox,
    QVBoxLayout,
)
from qgis.core import QgsMapLayerProxyModel, QgsRasterLayer
from qgis.gui import QgsFileWidget, QgsMapLayerComboBox

from .core import SPACES, SPACE_ORDER, dstretch
from . import qgis_io

PREVIEW_WIDTH = 460

# --------------------------------------------------------------------------
# Presets: starting points to step between. Every setting can be changed
# afterwards, which switches the list to "Custom".
#
# The strings are marked with QT_TRANSLATE_NOOP so translation tools pick
# them up here; they are actually translated where they are displayed.
# --------------------------------------------------------------------------
_N = QT_TRANSLATE_NOOP
PRESETS = [
    (_N("DStretchDialog", "Natural"),
     dict(space="lab", keep_luma=True, gain=0.35),
     _N("DStretchDialog",
        "Still looks like an aerial photograph, but vegetation types and "
        "soil moisture become distinguishable. Good when someone else is "
        "going to look at the image.")),
    (_N("DStretchDialog", "Vegetation"),
     dict(space="lab", keep_luma=True, gain=1.00),
     _N("DStretchDialog",
        "Clearly separates conifers, broadleaf, grass and clear-cuts. The "
        "default choice for orthophotos.")),
    (_N("DStretchDialog", "Ground traces"),
     dict(space="lab", keep_luma=True, gain=2.00),
     _N("DStretchDialog",
        "A hard stretch for hunting ditches, old roads, building "
        "foundations and damp streaks in ground that looks uniform. The "
        "colors stop being realistic.")),
    (_N("DStretchDialog", "False color"),
     dict(space="rgb", keep_luma=False, gain=1.00),
     _N("DStretchDialog",
        "Maximum color separation, lightness recomputed as well. The least "
        "natural looking, but often the easiest to spot faint structures "
        "in.")),
    (_N("DStretchDialog", "Soft (YUV)"),
     dict(space="yuv", keep_luma=True, gain=1.20),
     _N("DStretchDialog",
        "A middle ground between CIELAB and RGB. Worth trying when CIELAB "
        "gives too little and false color gives too much.")),
]
DEFAULT_PRESET = 1


class DStretchDialog(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self._source_cache = None      # (rgb, alpha) used for the preview
        self._loading = False          # True while a preset is being applied
        self._last_preset = DEFAULT_PRESET

        self.setWindowTitle(self.tr("Decorrelation stretch (DStretch)"))
        self.setMinimumWidth(880)
        self._build_ui()
        self._connect()

        # Recomputes the preview shortly after the last change
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.update_preview)

        self.canvas.extentsChanged.connect(self._invalidate_source)
        self.apply_preset(DEFAULT_PRESET)
        self.update_preview()

    # ------------------------------------------------------------------
    # User interface
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QGridLayout(self)

        # ---- Source ---------------------------------------------------
        source_box = QGroupBox(self.tr("Source"))
        form = QFormLayout(source_box)

        self.rb_canvas = QRadioButton(
            self.tr("Map canvas - everything currently visible"))
        self.rb_canvas.setChecked(True)
        self.rb_layer = QRadioButton(
            self.tr("Raster layer at its own resolution"))
        form.addRow(self.rb_canvas)
        form.addRow(self.rb_layer)

        self.cb_layer = QgsMapLayerComboBox()
        self.cb_layer.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.cb_layer.setEnabled(False)
        form.addRow(self.tr("Layer"), self.cb_layer)

        band_row = QHBoxLayout()
        self.sb_bands = []
        for label in ("R", "G", "B"):
            spin = QSpinBox()
            spin.setRange(1, 999)
            spin.setEnabled(False)
            spin.setMaximumWidth(60)
            band_row.addWidget(QLabel(label))
            band_row.addWidget(spin)
            self.sb_bands.append(spin)
        for i, spin in enumerate(self.sb_bands):
            spin.setValue(i + 1)
        band_row.addStretch()
        form.addRow(self.tr("Bands"), band_row)

        self.cb_upscale = QComboBox()
        self.cb_upscale.addItem(self.tr("1x screen resolution"), 1)
        self.cb_upscale.addItem(self.tr("2x screen resolution"), 2)
        self.cb_upscale.addItem(self.tr("4x screen resolution"), 4)
        form.addRow(self.tr("Output resolution"), self.cb_upscale)

        # ---- Settings -------------------------------------------------
        setting_box = QGroupBox(self.tr("Settings"))
        sform = QFormLayout(setting_box)

        preset_row = QHBoxLayout()
        self.btn_prev_preset = QPushButton("◀")
        self.btn_next_preset = QPushButton("▶")
        for button in (self.btn_prev_preset, self.btn_next_preset):
            button.setMaximumWidth(30)
            button.setToolTip(
                self.tr("Step through the presets (Ctrl+Left and Ctrl+Right)"))
        self.cb_preset = QComboBox()
        for name, _, _ in PRESETS:
            self.cb_preset.addItem(self.tr(name))
        self.cb_preset.addItem(self.tr("Custom"))
        preset_row.addWidget(self.btn_prev_preset)
        preset_row.addWidget(self.cb_preset, 1)
        preset_row.addWidget(self.btn_next_preset)
        sform.addRow(self.tr("Preset"), preset_row)

        self.lbl_preset = QLabel()
        self.lbl_preset.setWordWrap(True)
        self.lbl_preset.setStyleSheet("color:#666;font-style:italic;")
        sform.addRow("", self.lbl_preset)

        self.cb_space = QComboBox()
        for key in SPACE_ORDER:
            self.cb_space.addItem(SPACES[key][3], key)
        sform.addRow(self.tr("Color space"), self.cb_space)

        self.chk_keep_luma = QCheckBox(
            self.tr("Keep the original lightness (stretch color only)"))
        self.chk_keep_luma.setChecked(True)
        self.chk_keep_luma.setToolTip(self.tr(
            "Equivalent to the DStretch LDS and YDS modes: terrain structure "
            "and shadows are left untouched while color differences are "
            "magnified."))
        sform.addRow("", self.chk_keep_luma)

        self.sp_gain = QDoubleSpinBox()
        self.sp_gain.setRange(0.05, 5.0)
        self.sp_gain.setSingleStep(0.05)
        self.sp_gain.setDecimals(2)
        self.sp_gain.setValue(1.00)
        self.sp_gain.setToolTip(self.tr(
            "How hard the stretch hits. Below 1 keeps the result natural "
            "looking, above 1 gives strong false colors."))
        sform.addRow(self.tr("Gain"), self.sp_gain)

        scale_row = QHBoxLayout()
        self.chk_auto_scale = QCheckBox(self.tr("automatic"))
        self.chk_auto_scale.setChecked(True)
        self.sp_scale = QDoubleSpinBox()
        self.sp_scale.setRange(0.1, 500.0)
        self.sp_scale.setSingleStep(1.0)
        self.sp_scale.setValue(30.0)
        self.sp_scale.setEnabled(False)
        self.sp_scale.setToolTip(self.tr(
            "Lock this to compare several map views against each other. "
            "Automatic mode adapts to each view, so two views are not "
            "directly comparable."))
        scale_row.addWidget(self.chk_auto_scale)
        scale_row.addWidget(self.sp_scale)
        scale_row.addStretch()
        sform.addRow(self.tr("Target spread"), scale_row)

        self.chk_mask = QCheckBox(
            self.tr("Exclude transparent pixels and nodata from statistics"))
        self.chk_mask.setChecked(True)
        sform.addRow("", self.chk_mask)

        # ---- Result ---------------------------------------------------
        out_box = QGroupBox(self.tr("Result"))
        oform = QFormLayout(out_box)

        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText(self.tr("leave empty to name it "
                                                "after the preset"))
        oform.addRow(self.tr("Layer name"), self.ed_name)

        self.fw_output = QgsFileWidget()
        self.fw_output.setStorageMode(QgsFileWidget.SaveFile)
        self.fw_output.setFilter(self.tr("GeoTIFF (*.tif)"))
        self.fw_output.setDialogTitle(self.tr("Save the result as"))
        oform.addRow(self.tr("File"), self.fw_output)
        oform.addRow("", QLabel("<i>%s</i>" % self.tr(
            "An empty path writes to a temporary file.")))

        # ---- Preview --------------------------------------------------
        preview_box = QGroupBox(self.tr("Preview"))
        preview_layout = QVBoxLayout(preview_box)
        self.lbl_preview = QLabel(self.tr("Computing..."))
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        self.lbl_preview.setMinimumSize(PREVIEW_WIDTH, 300)
        self.lbl_preview.setStyleSheet(
            "background:#202020;color:#bbb;border:1px solid #555;")
        preview_layout.addWidget(self.lbl_preview)

        self.btn_refresh = QPushButton(self.tr("Refresh from the map canvas"))
        preview_layout.addWidget(self.btn_refresh)

        self.lbl_stats = QLabel()
        self.lbl_stats.setWordWrap(True)
        self.lbl_stats.setTextFormat(Qt.RichText)
        preview_layout.addWidget(self.lbl_stats)

        # ---- Buttons --------------------------------------------------
        self.buttons = QDialogButtonBox()
        self.btn_run = self.buttons.addButton(self.tr("Create layer"),
                                              QDialogButtonBox.AcceptRole)
        self.buttons.addButton(self.tr("Close"),
                               QDialogButtonBox.RejectRole)

        left = QVBoxLayout()
        left.addWidget(source_box)
        left.addWidget(setting_box)
        left.addWidget(out_box)
        left.addStretch()

        layout.addLayout(left, 0, 0)
        layout.addWidget(preview_box, 0, 1)
        layout.addWidget(self.buttons, 1, 0, 1, 2)
        layout.setColumnStretch(1, 1)

    def _connect(self):
        self.rb_layer.toggled.connect(self._source_changed)
        self.cb_layer.layerChanged.connect(self._invalidate_source)
        for spin in self.sb_bands:
            spin.valueChanged.connect(self._invalidate_source)
        self.chk_mask.toggled.connect(self._schedule)
        # Changing any of these switches the preset list to "Custom"
        self.cb_space.currentIndexChanged.connect(self._setting_changed)
        self.chk_keep_luma.toggled.connect(self._setting_changed)
        self.sp_gain.valueChanged.connect(self._setting_changed)
        self.sp_scale.valueChanged.connect(self._setting_changed)
        self.chk_auto_scale.toggled.connect(self._auto_scale_toggled)
        self.btn_refresh.clicked.connect(self._invalidate_source)
        self.buttons.accepted.connect(self.run)
        self.buttons.rejected.connect(self.close)

        # activated fires only on a choice made by the user, not when the
        # index is set from code
        self.cb_preset.activated.connect(self._preset_chosen)
        self.btn_prev_preset.clicked.connect(lambda: self.step_preset(-1))
        self.btn_next_preset.clicked.connect(lambda: self.step_preset(1))
        QShortcut(QKeySequence("Ctrl+Left"), self,
                  activated=lambda: self.step_preset(-1))
        QShortcut(QKeySequence("Ctrl+Right"), self,
                  activated=lambda: self.step_preset(1))

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------
    def apply_preset(self, index):
        """Fill the fields from a preset without marking them as custom."""
        name, values, description = PRESETS[index]
        self._loading = True
        try:
            self.cb_space.setCurrentIndex(SPACE_ORDER.index(values["space"]))
            self.chk_keep_luma.setChecked(values["keep_luma"])
            self.sp_gain.setValue(values["gain"])
            self.chk_auto_scale.setChecked(values.get("scale") is None)
            if values.get("scale") is not None:
                self.sp_scale.setValue(values["scale"])
        finally:
            self._loading = False

        self._last_preset = index
        self.cb_preset.setCurrentIndex(index)
        self.lbl_preset.setText(self.tr(description))
        self._schedule()

    def step_preset(self, delta):
        """Next or previous preset, wrapping around the list."""
        current = self.cb_preset.currentIndex()
        if current >= len(PRESETS):        # on "Custom" - continue from the
            current = self._last_preset    # preset the values came from
        self.apply_preset((current + delta) % len(PRESETS))

    def _preset_chosen(self, index):
        if index < len(PRESETS):
            self.apply_preset(index)
        else:                              # the user picked "Custom" itself
            self.cb_preset.setCurrentIndex(self._last_preset)

    def _mark_custom(self):
        if self._loading or self.cb_preset.currentIndex() >= len(PRESETS):
            return
        self.cb_preset.setCurrentIndex(len(PRESETS))
        self.lbl_preset.setText(self.tr(
            "Custom values, starting from «%s». Ctrl+Left and "
            "Ctrl+Right take you back to the presets."
        ) % self.tr(PRESETS[self._last_preset][0]))

    def _setting_changed(self, *args):
        self._mark_custom()
        self._schedule()

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------
    def _source_changed(self, layer_mode):
        self.cb_layer.setEnabled(layer_mode)
        for spin in self.sb_bands:
            spin.setEnabled(layer_mode)
        self.cb_upscale.setEnabled(not layer_mode)
        self._invalidate_source()

    def _auto_scale_toggled(self, auto):
        self.sp_scale.setEnabled(not auto)
        self._setting_changed()

    def _invalidate_source(self, *args):
        self._source_cache = None
        self._schedule()

    def _schedule(self, *args):
        if self.isVisible():
            self._timer.start()

    # ------------------------------------------------------------------
    # Fetching data
    # ------------------------------------------------------------------
    def _settings(self):
        return dict(
            space=self.cb_space.currentData(),
            keep_luma=self.chk_keep_luma.isChecked(),
            gain=self.sp_gain.value(),
            scale=(None if self.chk_auto_scale.isChecked()
                   else self.sp_scale.value()),
        )

    def _read_source(self, preview):
        """Return (rgb, alpha, extent, crs)."""
        if self.rb_layer.isChecked():
            layer = self.cb_layer.currentLayer()
            if not isinstance(layer, QgsRasterLayer):
                raise ValueError(self.tr("Select a raster layer."))
            bands = tuple(spin.value() for spin in self.sb_bands)
            if max(bands) > layer.bandCount():
                raise ValueError(
                    self.tr("The layer only has %d bands.") % layer.bandCount())
            limit = 400_000 if preview else 40_000_000
            return qgis_io.read_raster(layer, self.canvas, bands,
                                       max_pixels=limit)

        upscale = 1 if preview else self.cb_upscale.currentData()
        return qgis_io.read_canvas(
            self.canvas, upscale=upscale,
            max_width=PREVIEW_WIDTH if preview else None)

    def _mask_from_alpha(self, alpha):
        if not self.chk_mask.isChecked() or alpha is None:
            return None
        mask = alpha > 200
        return mask if mask.any() else None

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------
    def update_preview(self):
        try:
            if self._source_cache is None:
                rgb, alpha, _, _ = self._read_source(preview=True)
                self._source_cache = (rgb, alpha)
            rgb, alpha = self._source_cache

            result, info = dstretch(rgb, mask=self._mask_from_alpha(alpha),
                                    sample_limit=300_000, **self._settings())
            self._show_preview(result, alpha)
            self._show_stats(info)
        except Exception as error:                     # reported in-dialog
            self.lbl_preview.setText(
                self.tr("Cannot preview:\n%s") % error)
            self.lbl_stats.clear()

    def _show_preview(self, rgb, alpha):
        height, width = rgb.shape[:2]
        rgba = np.dstack([rgb, alpha if alpha is not None
                          else np.full((height, width), 255, np.uint8)])
        rgba = np.ascontiguousarray(rgba)
        image = QImage(rgba.data, width, height, 4 * width,
                       QImage.Format_RGBA8888).copy()
        pixmap = QPixmap.fromImage(image)
        if pixmap.width() > PREVIEW_WIDTH:
            pixmap = pixmap.scaledToWidth(PREVIEW_WIDTH,
                                          Qt.SmoothTransformation)
        self.lbl_preview.setPixmap(pixmap)

    def _show_stats(self, info):
        evals = np.sort(info["eigenvalues"])[::-1]
        ratio = evals[0] / max(evals[-1], 1e-9)
        hint = (self.tr("A large ratio means the image is nearly single "
                        "colored, which is where the stretch has the most to "
                        "give.")
                if ratio > 20 else
                self.tr("This image already has plenty of color variation. A "
                        "lower gain usually gives the most useful result."))
        self.lbl_stats.setText(
            "<b>%s:</b> %s &nbsp; <b>%s:</b> %.1f<br>"
            "<b>%s:</b> %s<br><b>%s:</b> %.0fx<br><i>%s</i>" % (
                self.tr("Color space"), info["space"],
                self.tr("target spread"), info["scale"],
                self.tr("Eigenvalues"),
                ", ".join("%.2f" % value for value in evals),
                self.tr("Spread between strongest and weakest color axis"),
                ratio, hint))

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def _default_name(self, settings):
        """Name the layer after the preset, otherwise after the settings."""
        index = self.cb_preset.currentIndex()
        if index < len(PRESETS):
            base = self.tr(PRESETS[index][0])
        else:
            base = "%s_%g" % (settings["space"], settings["gain"])
        clean = "".join(c if c.isalnum() else "_" for c in base.lower())
        return "dstretch_" + clean.strip("_").replace("__", "_")

    def run(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rgb, alpha, extent, crs = self._read_source(preview=False)
            settings = self._settings()
            result, info = dstretch(rgb, mask=self._mask_from_alpha(alpha),
                                    **settings)

            name = self.ed_name.text().strip() or self._default_name(settings)
            path = self.fw_output.filePath().strip()
            if not path:
                path = qgis_io.temp_path(name)
            else:
                if not path.lower().endswith((".tif", ".tiff")):
                    path += ".tif"
                path = os.path.abspath(path)
                os.makedirs(os.path.dirname(path), exist_ok=True)

            qgis_io.write_geotiff(path, result, alpha, extent, crs)
            qgis_io.add_layer(path, name)

            self._show_stats(info)
            self.iface.messageBar().pushSuccess("DStretch", self.tr(
                "Layer «%s» created (%d x %d px, %s, target spread "
                "%.1f).") % (name, result.shape[1], result.shape[0],
                             info["space"], info["scale"]))
        except Exception as error:
            self.iface.messageBar().pushWarning("DStretch", str(error))
        finally:
            QApplication.restoreOverrideCursor()

    def disconnect_canvas(self):
        """Called when the plugin is unloaded."""
        try:
            self.canvas.extentsChanged.disconnect(self._invalidate_source)
        except (TypeError, RuntimeError):
            pass
