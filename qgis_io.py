# -*- coding: utf-8 -*-
"""Reading from and writing to QGIS: the map canvas, raster layers, GeoTIFF."""

import os
import tempfile
import uuid

import numpy as np

from qgis.PyQt.QtCore import QCoreApplication, QSize
from qgis.PyQt.QtGui import QColor, QImage
from qgis.core import (
    QgsCoordinateTransform,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsProject,
    QgsRasterLayer,
)

from .core import stretch_to_byte

# Qgis.DataType -> numpy. The enum values are stable across QGIS 3.x.
_DTYPES = {
    1: np.uint8,    # Byte
    2: np.uint16,   # UInt16
    3: np.int16,    # Int16
    4: np.uint32,   # UInt32
    5: np.int32,    # Int32
    6: np.float32,  # Float32
    7: np.float64,  # Float64
    14: np.int8,    # Int8
}


def tr(message):
    return QCoreApplication.translate("DStretch", message)


# --------------------------------------------------------------------------
# The map canvas
# --------------------------------------------------------------------------
def read_canvas(canvas, upscale=1, max_width=None):
    """
    Render everything visible in the map canvas into an array.
    max_width is used for fast previews.
    Returns (rgb HxWx3 uint8, alpha HxW uint8, extent, crs).
    """
    settings = QgsMapSettings(canvas.mapSettings())
    size = settings.outputSize()

    factor = float(upscale)
    if max_width and size.width() * factor > max_width:
        factor = max_width / float(size.width())
    if abs(factor - 1.0) > 1e-6:
        settings.setOutputSize(QSize(max(1, int(size.width() * factor)),
                                     max(1, int(size.height() * factor))))
        settings.setOutputDpi(settings.outputDpi() * factor)
    settings.setBackgroundColor(QColor(0, 0, 0, 0))     # transparent backdrop

    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    image = job.renderedImage().convertToFormat(QImage.Format_RGBA8888)

    width, height = image.width(), image.height()
    pointer = image.constBits()
    pointer.setsize(height * image.bytesPerLine())
    # bytesPerLine may contain padding, so trim each row to the real width
    buffer = np.frombuffer(pointer, dtype=np.uint8).reshape(
        height, image.bytesPerLine() // 4, 4)[:, :width, :]

    # visibleExtent(), not extent(), is the area the image actually covers.
    # extent() is merely the requested rectangle; rendering stretches it to
    # the proportions of the map canvas. Georeferencing against extent()
    # squeezes the output layer along one axis.
    return (buffer[:, :, :3].copy(), buffer[:, :, 3].copy(),
            settings.visibleExtent(), settings.destinationCrs())


# --------------------------------------------------------------------------
# Raster layers
# --------------------------------------------------------------------------
def read_raster(layer, canvas, bands=(1, 2, 3), max_pixels=40_000_000):
    """
    Read a raster layer at its own resolution, limited to what is visible in
    the map canvas. Returns (rgb, alpha, extent, crs).
    """
    provider = layer.dataProvider()
    settings = canvas.mapSettings()
    canvas_crs = settings.destinationCrs()
    extent = settings.visibleExtent()          # what is actually on screen

    if canvas_crs != layer.crs():
        transform = QgsCoordinateTransform(canvas_crs, layer.crs(),
                                           QgsProject.instance())
        extent = transform.transformBoundingBox(extent)

    extent = extent.intersect(layer.extent())
    if extent.isEmpty():
        raise ValueError(tr("The layer does not overlap the map canvas. Pan "
                            "to the layer, or use the map canvas as source."))

    pixel_x = layer.rasterUnitsPerPixelX() or (extent.width() / 1000.0)
    pixel_y = layer.rasterUnitsPerPixelY() or (extent.height() / 1000.0)
    width = max(1, int(round(extent.width() / pixel_x)))
    height = max(1, int(round(extent.height() / pixel_y)))

    if width * height > max_pixels:            # scale down proportionally
        factor = (max_pixels / float(width * height)) ** 0.5
        width = max(1, int(width * factor))
        height = max(1, int(height * factor))

    channels, valid, dtype = [], None, None
    for band in bands:
        block = provider.block(band, extent, width, height)
        dtype = _DTYPES.get(int(block.dataType()))
        if dtype is None:
            raise ValueError(tr("Unsupported data type in band %d.") % band)

        data = np.frombuffer(bytes(block.data()), dtype=dtype)
        data = data.reshape(height, width).astype(np.float64)

        ok = np.isfinite(data)
        if provider.sourceHasNoDataValue(band):
            ok &= data != provider.sourceNoDataValue(band)
        valid = ok if valid is None else (valid & ok)
        channels.append(data)

    if dtype is np.uint8:
        rgb = np.stack([c.astype(np.uint8) for c in channels], axis=2)
    else:
        # not 8 bit: percentile stretch each band to 0-255 first
        rgb = np.stack([stretch_to_byte(c, valid) for c in channels], axis=2)

    alpha = np.where(valid, 255, 0).astype(np.uint8)
    return rgb, alpha, extent, layer.crs()


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def write_geotiff(path, rgb, alpha, extent, crs):
    """Write an RGB(A) GeoTIFF and return the path."""
    from osgeo import gdal, osr

    height, width = rgb.shape[:2]
    use_alpha = alpha is not None and bool((alpha < 255).any())
    band_count = 4 if use_alpha else 3

    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(path, width, height, band_count, gdal.GDT_Byte,
                            options=["COMPRESS=DEFLATE", "PREDICTOR=2",
                                     "TILED=YES", "PHOTOMETRIC=RGB"])
    dataset.SetGeoTransform([extent.xMinimum(), extent.width() / width, 0.0,
                             extent.yMaximum(), 0.0, -extent.height() / height])

    srs = osr.SpatialReference()
    if srs.SetFromUserInput(crs.toWkt()) != 0:
        srs.SetFromUserInput(crs.authid())
    dataset.SetProjection(srs.ExportToWkt())

    interpretations = [gdal.GCI_RedBand, gdal.GCI_GreenBand, gdal.GCI_BlueBand]
    for i in range(3):
        band = dataset.GetRasterBand(i + 1)
        band.WriteArray(rgb[:, :, i])
        band.SetColorInterpretation(interpretations[i])
    if use_alpha:
        band = dataset.GetRasterBand(4)
        band.WriteArray(alpha)
        band.SetColorInterpretation(gdal.GCI_AlphaBand)

    dataset.FlushCache()
    dataset = None
    return path


def temp_path(name):
    folder = os.path.join(tempfile.gettempdir(), "dstretch")
    os.makedirs(folder, exist_ok=True)
    # the layer name may contain non-ASCII characters, the file name may not
    safe = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_"
                   for c in name).strip("_") or "dstretch"
    return os.path.join(folder, "%s_%s.tif" % (safe, uuid.uuid4().hex[:8]))


def add_layer(path, name):
    layer = QgsRasterLayer(path, name)
    if not layer.isValid():
        raise ValueError(tr("Could not load the result: %s") % path)
    QgsProject.instance().addMapLayer(layer)
    return layer
