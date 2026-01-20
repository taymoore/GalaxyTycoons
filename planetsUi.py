from typing import List, Dict, Optional
import logging
import itertools
from pathlib import Path
import pickle
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from PySide6.QtCore import (
    Slot,
    Signal,
    QThread,
    QSize,
    Qt,
    QAbstractTableModel,
    QSortFilterProxyModel,
    QObject,
    QModelIndex,
    QPersistentModelIndex,
)
from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QTableView,
    QAbstractItemView,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QWidget,
    QToolBox,
    QSlider,
    QSizePolicy,
    QLabel,
    QCheckBox,
    QSplitter,
    QMenuBar,
    QWidgetAction,
    QMainWindow,
)
from PySide6.QtGui import QCloseEvent, QWheelEvent, QPixmap
import pyqtgraph as pg
import matplotlib.colors as mcolors
import matplotlib.cm as cm

import utils
from utils import align_add
from api.gameData import GameDataManager
from api.models.gameData import Recipe, Specialization, Building, WorkerType
from api.exchange import Exchange
from api.models.exchange import Listing
from recipeWorker import RecipeWorker

_logger = logging.getLogger(__name__)


class PlanetsWindow(QWidget):  # Changed from QMainWindow
    class Map(QWidget):
        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            # self.map = QPixmap()

    class PlanetsTableModel(QAbstractTableModel):
        def __init__(self, parent: QObject):
            super().__init__(parent)
            self.table_data: List[List[str]] = []
            self.header_data: List[str] = ["Name", "Value", "Distance", "Product"]
            self.max_planet_distance = 0
            self.max_planet_value = 0
            self.populate_table()

        def rowCount(self, /, parent: QModelIndex | QPersistentModelIndex) -> int:
            return len(self.table_data)

        def columnCount(self, /, parent=QModelIndex | QPersistentModelIndex) -> int:
            return len(self.header_data)

        def headerData(
            self,
            section: int,
            orientation: Qt.Orientation,
            role: int = Qt.ItemDataRole.DisplayRole,
        ) -> object | None:
            if role == Qt.ItemDataRole.DisplayRole:
                if orientation == Qt.Orientation.Horizontal:
                    return self.header_data[section]

        def data(
            self,
            /,
            index: QModelIndex | QPersistentModelIndex,
            role: Qt.ItemDataRole = Qt.ItemDataRole.DisplayRole,
        ) -> object | None:
            row = index.row()
            column = index.column()
            if column == 3:
                return ""
            data = self.table_data[row][column]
            if role == Qt.ItemDataRole.DisplayRole:
                return data
            elif role == Qt.ItemDataRole.UserRole:
                return data

        def populate_table(self) -> None:
            systems = GameDataManager.get().systems
            # Find location of exchange station
            exchange_loc = None
            for system in systems:
                for planet in system.planets or []:
                    if planet.id == 1022:
                        if planet.name != "Exchange Station":
                            raise ValueError(
                                f"planet id 1022 does not name name 'Exchange Station'. Found name '{planet.name}'"
                            )
                        exchange_loc = (planet.x, planet.y)
                        break
                if exchange_loc is not None:
                    break
            assert exchange_loc is not None

            # Compute base stats for planets
            for system in systems:
                for planet in system.planets or []:
                    planet_value = 0.0
                    for mat in planet.mats:
                        mat_price = (
                            Exchange.get_listing(mat.id).average_price
                            if utils.use_average_price
                            else Exchange.get_listing(mat.id).current_price
                        )
                        planet_value += mat_price * mat.ab / 1000
                    if planet_value > self.max_planet_value:
                        self.max_planet_value = planet_value
                    planet_distance = float(
                        np.sqrt(
                            (planet.x - exchange_loc[0]) ** 2
                            + (planet.y - exchange_loc[1]) ** 2
                        )
                    )
                    if planet_distance > self.max_planet_distance:
                        self.max_planet_distance = planet_distance
                    self.table_data.append([planet.name, planet_value, planet_distance])

    class PlanetsTableView(QTableView):
        def __init__(self, parent):
            super().__init__(parent)
            self.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
            self.verticalHeader().hide()
            self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.setSortingEnabled(True)
            self.sortByColumn(1, Qt.SortOrder.DescendingOrder)

        # TODO: Determine if this is needed here or in recipeUi.py?
        def setModel(self, model: QAbstractTableModel) -> None:
            """Override setModel to connect signals for dynamic resizing."""
            super().setModel(model)
            if model:
                model.dataChanged.connect(self.adjust_table_width)
                model.rowsInserted.connect(self.adjust_table_width)

        def adjust_table_width(self):
            """Adjust the table width to fit the contents."""
            self.resizeColumnsToContents()

            # Calculate the total width of all columns
            total_table_width = sum(
                self.horizontalHeader().sectionSize(i)
                for i in range(self.horizontalHeader().count())
            )

            # Add width for vertical scrollbar (if present)
            if self.verticalScrollBar().isVisible():
                total_table_width += self.verticalScrollBar().width()

            # Notify the parent splitter to adjust sizes
            if self.parent() and isinstance(self.parent(), QSplitter):
                self.parent().setSizes(
                    [total_table_width, self.parent().width() - total_table_width]
                )

    class PlanetsTableProxyModel(QSortFilterProxyModel):
        def __init__(self, parent: QObject | None):
            super().__init__(parent)
            # self.setDynamicSortFilter(True)
            # self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            # self.sort(1, Qt.SortOrder.DescendingOrder)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Main layout
        self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)

        # Planets table
        self.planets_table_model = PlanetsWindow.PlanetsTableModel(self)
        self.planets_table_view = PlanetsWindow.PlanetsTableView(self)
        self.planets_table_proxy_model = PlanetsWindow.PlanetsTableProxyModel(self)
        self.planets_table_proxy_model.setSourceModel(self.planets_table_model)
        self.planets_table_view.setModel(self.planets_table_proxy_model)
        self.main_layout.addWidget(self.planets_table_view)
