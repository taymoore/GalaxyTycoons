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

from utils import align_add
from api.gameData import get_gamedata, get_item_name, get_building, get_worker
from api.models.gameData import Recipe, BuildingSpecialization, Building, WorkerType
from api.exchange import Exchange
from api.models.exchange import Listing
from recipeWorker import RecipeWorker

_logger = logging.getLogger(__name__)


class InvestmentsWindow(QWidget):

    class InvestmentInputWidget(QWidget):
        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.layout = QHBoxLayout()
            self.setLayout(self.layout)

    class InvestmentsTableModel(QAbstractTableModel):
        def __init__(self, parent: QObject):
            super().__init__(parent)
            self.table_data: List[List[str]] = []
            self.header_data: List[str] = ["Description", "Cost", "Return", "ROI"]

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
            data = self.table_data[row][column]
            if role == Qt.ItemDataRole.DisplayRole:
                return data
            elif role == Qt.ItemDataRole.UserRole:
                return data


    class InvestmentsTableView(QTableView):
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
            self.sortByColumn(3, Qt.SortOrder.DescendingOrder)

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

    class InvestmentsTableProxyModel(QSortFilterProxyModel):
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

        # Investments table
        self.investments_table_model = InvestmentsWindow.InvestmentsTableModel(self)
        self.investments_table_view = InvestmentsWindow.InvestmentsTableView(self)
        self.investments_table_proxy_model = InvestmentsWindow.InvestmentsTableProxyModel(self)
        self.investments_table_proxy_model.setSourceModel(self.investments_table_model)
        self.investments_table_view.setModel(self.investments_table_proxy_model)
        self.main_layout.addWidget(self.investments_table_view)
