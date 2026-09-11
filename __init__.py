# -*- coding: utf-8 -*-
"""DStretch for QGIS - decorrelation stretch of the map canvas."""


def classFactory(iface):
    from .plugin import DStretchPlugin
    return DStretchPlugin(iface)
