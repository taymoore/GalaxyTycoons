from typing import List, Dict, Optional, Tuple
import logging
import itertools
from pathlib import Path
import pickle
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from api.models.gameData import RecipeType
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
    QProgressBar,
)
from PySide6.QtGui import QCloseEvent, QWheelEvent, QPixmap, QColor
import pyqtgraph as pg
import matplotlib.colors as mcolors
import matplotlib.cm as cm

from settings import Settings
from utils import (
    align_add,
    find_best_recipe_for_building,
    calculate_profit_and_consumables,
)
from api.gameData import GameDataManager
from api.models.gameData import Recipe, Specialization, Building, WorkerType
from api.exchange import Exchange
from api.models.exchange import Listing
from recipeWorker import RecipeWorker

_logger = logging.getLogger(__name__)


class InvestmentsWindow(QWidget):
    class InvestmentsTableModel(QAbstractTableModel):
        def __init__(self, parent: QObject, settings: Settings):
            super().__init__(parent)
            self.settings = settings
            self.table_data: List[List[str]] = []
            self.buildings_data: List[Building] = []
            self.header_data: List[str] = [
                "Building & Recipe",
                "Construction Cost",
                "Profit/hr",
                "ROI (days)",
            ]
            # Track min/max values for gradient coloring
            self.profit_min = 0.0
            self.profit_max = 1.0
            self.roi_min = 0.0
            self.roi_max = 1000.0  # Default max ROI in days

        def rowCount(self, /, parent=None) -> int:
            return len(self.table_data)

        def columnCount(self, /, parent=None) -> int:
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
            index,
            role: Qt.ItemDataRole = Qt.ItemDataRole.DisplayRole,
        ) -> object:
            row = index.row()
            column = index.column()

            if row >= len(self.table_data) or column >= len(self.table_data[row]):
                return None

            data = self.table_data[row][column]

            if role == Qt.ItemDataRole.DisplayRole:
                if column == 1:  # Cost column
                    return f"${data:,.2f}" if isinstance(data, (int, float)) else data
                elif column == 2:  # Profit column
                    return (
                        f"${data:,.2f}/hr" if isinstance(data, (int, float)) else data
                    )
                elif column == 3:  # ROI column
                    if isinstance(data, (int, float)):
                        if data == float("inf"):
                            return "Never"
                        elif data > 365:
                            return f"{data/365:.1f} years"
                        else:
                            return f"{data:.1f} days"
                    return data
                return data
            elif role == Qt.ItemDataRole.UserRole:
                return data
            elif role == Qt.ItemDataRole.BackgroundRole:
                # Color ROI column based on value
                if (
                    column == 3
                    and isinstance(data, (int, float))
                    and data != float("inf")
                ):
                    # Green for quick ROI, red for slow ROI
                    normalized = 1.0 - min(
                        1.0,
                        max(0.0, (data - self.roi_min) / (self.roi_max - self.roi_min)),
                    )
                    return QColor(
                        int(255 * (1 - normalized)),  # Red component
                        int(255 * normalized),  # Green component
                        0,  # Blue component
                    )
                # Color profit column based on value
                elif column == 2 and isinstance(data, (int, float)):
                    normalized = min(
                        1.0,
                        max(
                            0.0,
                            (data - self.profit_min)
                            / (self.profit_max - self.profit_min),
                        ),
                    )
                    return QColor(
                        int(255 * (1 - normalized)),  # Red component
                        int(255 * normalized),  # Green component
                        0,  # Blue component
                    )
            return None

        def populate_buildings(self) -> None:
            """Populate the table with all buildings and their ROI calculations."""
            self.beginResetModel()
            self.table_data = []
            self.buildings_data = []

            try:
                game_data = GameDataManager.get()

                for building in game_data.buildings:
                    try:
                        # Skip worker housing buildings
                        if any(
                            building.id
                            == GameDataManager.get_worker_housing(worker_type).id
                            for worker_type in WorkerType
                        ):
                            continue

                        # Skip buildings without construction materials (can't calculate cost)
                        if (
                            not hasattr(building, "constructionMaterials")
                            or not building.constructionMaterials
                        ):
                            continue

                        # Calculate building cost from construction materials
                        cost = 0
                        for material in building.constructionMaterials:
                            material_listing = Exchange.get_listing(material.id)
                            if material_listing and material_listing.current_price > 0:
                                cost += (
                                    material_listing.current_price * material.am
                                ) / 100  # Convert cents to dollars
                            else:
                                _logger.warning(
                                    f"Excluding building {building.name} due to missing listing for material ID {material.id}"
                                )
                                continue

                        if cost <= 0:
                            _logger.warning(
                                f"Excluding building {building.name} due to invalid construction cost"
                            )
                            continue  # Skip buildings with invalid costs

                        # Find best recipe for this building
                        specialization = building.specialization
                        max_tech_level = self.settings.tech_level_filters.get(
                            specialization, float("inf")
                        )
                        res = find_best_recipe_for_building(building.id, max_tech_level)
                        if not res:
                            continue
                        recipe_name, profit_per_hour, _, _ = res

                        # Calculate ROI in days (cost / profit per hour / 24)
                        roi_days = (
                            cost / profit_per_hour / 24
                            if profit_per_hour > 0
                            else float("inf")
                        )

                        # Add to table data
                        self.table_data.append(
                            [
                                f"{building.name} ({recipe_name})",
                                cost,
                                profit_per_hour,
                                roi_days,
                            ]
                        )
                        self.buildings_data.append(building)
                    except Exception as e:
                        _logger.error(f"Error processing building {building.name}: {e}")
            except Exception as e:
                _logger.error(f"Error populating buildings table: {e}")

            # Update min/max values for gradient coloring
            self._update_min_max_values()
            self.endResetModel()

        def _update_min_max_values(self) -> None:
            """Update min/max values for profit and ROI columns for gradient coloring."""
            if not self.table_data:
                self.profit_min = 0.0
                self.profit_max = 1.0
                self.roi_min = 0.0
                self.roi_max = 1000.0
                return

            # Extract profit values (column 2)
            profit_values = [
                row[2]
                for row in self.table_data
                if isinstance(row[2], (int, float)) and row[2] > 0
            ]
            if profit_values:
                self.profit_min = min(profit_values)
                self.profit_max = max(profit_values)
                if self.profit_max == self.profit_min:
                    self.profit_max = self.profit_min + 1.0

            # Extract ROI values (column 3), excluding infinity
            roi_values = [
                row[3]
                for row in self.table_data
                if isinstance(row[3], (int, float)) and row[3] != float("inf")
            ]
            if roi_values:
                self.roi_min = min(roi_values)
                self.roi_max = max(roi_values)
                if self.roi_max == self.roi_min:
                    self.roi_max = self.roi_min + 1.0
                # Cap ROI max at 365 days (1 year) for better color gradient
                self.roi_max = min(self.roi_max, 365)

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
            self.setAlternatingRowColors(True)

        def setModel(self, model: QAbstractTableModel) -> None:
            """Override setModel to connect signals for dynamic resizing."""
            super().setModel(model)
            if model:
                model.dataChanged.connect(self.adjust_table_width)
                model.rowsInserted.connect(self.adjust_table_width)

        def adjust_table_width(self):
            """Adjust the table width to fit the contents."""
            self.resizeColumnsToContents()

    class InvestmentsTableProxyModel(QSortFilterProxyModel):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setDynamicSortFilter(True)

        def lessThan(self, source_left: QModelIndex, source_right: QModelIndex) -> bool:
            """Custom sorting for numeric columns."""
            if source_left.column() in (1, 2, 3):  # Cost, Profit, ROI columns
                left_data = self.sourceModel().data(
                    source_left, Qt.ItemDataRole.UserRole
                )
                right_data = self.sourceModel().data(
                    source_right, Qt.ItemDataRole.UserRole
                )

                # Handle special cases like infinity
                if left_data == float("inf") and right_data == float("inf"):
                    return False
                elif left_data == float("inf"):
                    return False  # Infinity is greater than any number
                elif right_data == float("inf"):
                    return True  # Any number is less than infinity

                # Normal numeric comparison
                try:
                    return float(left_data) < float(right_data)
                except (ValueError, TypeError):
                    pass

            # Fall back to default comparison
            return super().lessThan(source_left, source_right)

    def __init__(self, parent, settings: Settings) -> None:
        super().__init__(parent)

        # Main layout
        self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)

        # Add header label
        header_label = QLabel("Building Investment ROI Calculator")
        header_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; margin-bottom: 10px;"
        )
        self.main_layout.addWidget(header_label)

        # Add description label
        description_label = QLabel(
            "This table shows the return on investment (ROI) for each building based on its "
            "construction cost and the most profitable recipe it can produce."
        )
        description_label.setWordWrap(True)
        self.main_layout.addWidget(description_label)

        # Investments table
        self.investments_table_model = InvestmentsWindow.InvestmentsTableModel(
            self, settings
        )
        self.investments_table_view = InvestmentsWindow.InvestmentsTableView(self)
        self.investments_table_proxy_model = (
            InvestmentsWindow.InvestmentsTableProxyModel(self)
        )
        self.investments_table_proxy_model.setSourceModel(self.investments_table_model)
        self.investments_table_view.setModel(self.investments_table_proxy_model)
        self.main_layout.addWidget(self.investments_table_view)

        # Status label and progress bar
        self.status_label = QLabel("Loading building data...")
        self.main_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setVisible(False)
        self.main_layout.addWidget(self.progress_bar)

        # Populate the table with buildings data
        self.investments_table_model.populate_buildings()

        # Update status label
        row_count = self.investments_table_model.rowCount()
        self.status_label.setText(
            f"Showing {row_count} buildings with profitable recipes"
        )

        # Set default sort to ROI column (ascending)
        self.investments_table_view.sortByColumn(3, Qt.SortOrder.AscendingOrder)

    @Slot()
    def handle_exchange_updated(self) -> None:
        """Update the ROI calculations when exchange prices change."""
        _logger.debug("Updating investments table due to exchange update")
        self.status_label.setText("Recalculating ROI values...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        # Use QTimer to allow UI to update before heavy calculation
        from PySide6.QtCore import QTimer

        QTimer.singleShot(100, self._perform_recalculation)

    def _perform_recalculation(self) -> None:
        """Perform the actual recalculation of ROI values."""
        try:
            self.investments_table_model.populate_buildings()
            row_count = self.investments_table_model.rowCount()
            self.status_label.setText(
                f"Showing {row_count} buildings with profitable recipes"
            )
        finally:
            self.progress_bar.setVisible(False)
