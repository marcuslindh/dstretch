# -*- coding: utf-8 -*-
"""Hooks into the QGIS user interface."""

import os

from qgis.PyQt.QtCore import QCoreApplication, QTranslator
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsSettings

from .dialog import DStretchDialog

MENU = "&DStretch"


class DStretchPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None
        self.translator = None
        self._install_translator()

    def _install_translator(self):
        """Load the translation matching the QGIS interface language."""
        locale = QgsSettings().value("locale/userLocale") or ""
        path = os.path.join(os.path.dirname(__file__), "i18n",
                            "dstretch_%s.qm" % locale[:2])
        if os.path.exists(path):
            self.translator = QTranslator()
            if self.translator.load(path):
                QCoreApplication.installTranslator(self.translator)
            else:
                self.translator = None

    def tr(self, message):
        return QCoreApplication.translate("DStretchPlugin", message)

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, self.tr("Decorrelation stretch..."),
                              self.iface.mainWindow())
        self.action.setToolTip(self.tr(
            "Decorrelation stretch on the map canvas - brings out color "
            "differences that are invisible in the original"))
        self.action.triggered.connect(self.run)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToRasterMenu(MENU, self.action)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginRasterMenu(MENU, self.action)
        if self.dialog is not None:
            self.dialog.disconnect_canvas()
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        if self.translator is not None:
            QCoreApplication.removeTranslator(self.translator)
            self.translator = None
        self.action = None

    def run(self):
        # The dialog is deliberately modeless: you should be able to pan and
        # zoom the map while it is open and rerun on the new view. The same
        # instance is reused so the settings are still there.
        if self.dialog is None:
            self.dialog = DStretchDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.dialog.update_preview()
