from typing import List, Dict, Optional, Tuple
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from api.models.company import Base
from api.models.gameData import RecipeType
from PySide6.QtCore import (
    Slot,
    Qt,
    QAbstractTableModel,
    QSortFilterProxyModel,
    QObject,
    QModelIndex,
)
from PySide6.QtWidgets import (
    QHeaderView,
    QTableView,
    QAbstractItemView,
    QVBoxLayout,
    QWidget,
    QLabel,
    QProgressBar,
)
from PySide6.QtGui import QCloseEvent, QWheelEvent, QPixmap, QColor
import pyqtgraph as pg
import matplotlib.colors as mcolors
import matplotlib.cm as cm

from settings import Settings
import utils
from utils import (
    align_add,
    calculate_research_cost,
    find_best_recipe_for_building,
    find_best_recipe_for_technology,
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

            self.base_dict: Dict[int, Base] = {}  # base_id: Base

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

        def populate_investments(self) -> None:
            """Populate the table with all buildings and their ROI calculations."""
            self.beginResetModel()
            self.table_data = []

            tech_profit_dict: Dict[Specialization:float] = {}

            try:
                game_data = GameDataManager.get()

                # Populate buildings
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

                        def _calculate_construction_cost(
                            building: Building,
                        ) -> float:
                            # Calculate building cost from construction materials
                            cost = 0
                            for material in building.constructionMaterials:
                                material_listing = Exchange.get_listing(material.id)
                                material_price = (
                                    material_listing.average_price
                                    if utils.use_average_price
                                    else material_listing.current_price
                                )
                                if material_listing and material_price > 0:
                                    cost += (
                                        material_price * material.am
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
                            return cost

                        cost = _calculate_construction_cost(building)

                        # Add housing cost for worker housing buildings
                        for worker_count, worker_type in zip(
                            building.workersNeeded or [],
                            WorkerType,
                        ):
                            if worker_count > 0:
                                housing_building = GameDataManager.get_worker_housing(
                                    worker_type
                                )
                                housing_capacity = housing_building.workersHousing[
                                    worker_type.value - 1
                                ]
                                # Assumes housing only provides for one type of worker at a time. This will be a bug otherwise.
                                housing_cost = _calculate_construction_cost(
                                    housing_building
                                ) * worker_count / housing_capacity
                                cost += (
                                    housing_cost
                                )


                        # Find best recipe for this building
                        specialization = building.specialization
                        max_tech_level = self.settings.tech_level_filters.get(
                            specialization, float("inf")
                        )
                        res = find_best_recipe_for_building(building.id, max_tech_level)
                        if not res:
                            continue
                        recipe_name, profit_per_hour, _, _ = res

                        # Add to running total for specialization tech profits
                        # Find number of buildings currently in use
                        num_building_levels = 0.0
                        for base in self.base_dict.values():
                            num_building_levels += sum(
                                slot.building.level
                                for slot in base.building_slots
                                if slot.building and slot.building.type == building.id
                            )
                        if num_building_levels > 0:
                            tech_profit_dict[specialization] = (
                                profit_per_hour * num_building_levels
                                + tech_profit_dict.get(specialization, 0.0)
                            )

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
                    except Exception as e:
                        _logger.error(f"Error processing building {building.name}: {e}")

                # Populate technologies
                for (
                    specialization,
                    tech_level,
                ) in self.settings.tech_level_filters.items():
                    # Calculate cost
                    research_amounts = calculate_research_cost(
                        specialization, self.settings.tech_level_filters
                    )
                    cost = 0.0
                    for amount, item_id in zip(research_amounts, (64, 65, 127, 164)):
                        listing = Exchange.get_listing(item_id)
                        listing_price = (
                            listing.average_price
                            if utils.use_average_price
                            else listing.current_price
                        )
                        if not listing or listing_price <= 0:
                            _logger.warning(
                                f"Excluding technology {specialization.name} level {tech_level} due to missing listing for item ID {item_id}"
                            )
                            cost = 0.0
                            break
                        cost += amount * listing_price / 100
                    if cost <= 0:
                        continue

                    # Calculate profit per hour from increased production
                    _, current_profit_per_hour, _, _ = find_best_recipe_for_technology(
                        specialization, tech_level
                    )
                    recipe_name, new_profit_per_hour, _, _ = (
                        find_best_recipe_for_technology(specialization, tech_level + 1)
                    )

                    # Calculate efficiency gained from tech level increase
                    efficiency_gain = tech_profit_dict.get(specialization, 0.0) * 0.05

                    # Calculate ROI
                    profit_increase_per_hour = (
                        efficiency_gain + new_profit_per_hour - current_profit_per_hour
                    )
                    roi_days = (
                        cost / profit_increase_per_hour / 24
                        if profit_increase_per_hour > 0
                        else float("inf")
                    )

                    # Add to table data
                    self.table_data.append(
                        [
                            f"{specialization.name} Tech Level {tech_level + 1} ({recipe_name})",
                            cost,
                            profit_increase_per_hour,
                            roi_days,
                        ]
                    )

            except Exception as e:
                _logger.error(f"Error populating investments table: {e}")

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
        self.investments_table_model.populate_investments()

        # Update status label
        row_count = self.investments_table_model.rowCount()
        self.status_label.setText(
            f"Showing {row_count} buildings with profitable recipes"
        )

        # Set default sort to ROI column (ascending)
        self.investments_table_view.sortByColumn(3, Qt.SortOrder.AscendingOrder)

    @Slot(Specialization, int)
    def handle_tech_slider_changed(self, specialization, tech_level) -> None:
        """Handle changes to the tech level filters."""
        self.perform_recalculation()

    @Slot()
    def handle_exchange_updated(self) -> None:
        """Update the ROI calculations when exchange prices change."""
        _logger.debug("Updating investments table due to exchange update")
        self.perform_recalculation()

    def perform_recalculation(self):
        self.status_label.setText("Recalculating ROI values...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        # Use QTimer to allow UI to update before heavy calculation
        from PySide6.QtCore import QTimer

        QTimer.singleShot(100, self._perform_recalculation)

    def _perform_recalculation(self) -> None:
        """Perform the actual recalculation of ROI values."""
        try:
            self.investments_table_model.populate_investments()
            row_count = self.investments_table_model.rowCount()
            self.status_label.setText(
                f"Showing {row_count} buildings with profitable recipes"
            )
        finally:
            self.progress_bar.setVisible(False)

    @Slot(Base)
    def handle_base_loaded(self, base: Base) -> None:
        """Handle a new base being loaded."""
        self.investments_table_model.base_dict[base.id] = base
        self.investments_table_model.populate_investments()
