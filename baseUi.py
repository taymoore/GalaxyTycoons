from enum import IntEnum
import logging
import math
from typing import Dict, List, Optional, Tuple

import matplotlib.cm as cm
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd
import pyqtgraph as pg
from pydantic import BaseModel, Field, field_validator
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPointF,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QBrush,
    QCloseEvent,
    QColor,
    QIntValidator,
    QPainter,
    QPalette,
    QTextCharFormat,
    QTextLayout,
    QTextOption,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QToolBox,
    QVBoxLayout,
    QWidget,
)

from api.exchange import Exchange
from api.gameData import GameDataManager
from api.models.company import (
    Base,
    BuildingType,
    Company,
    FlightType,
    ProductionOrder,
    Technology,
)
from api.models.exchange import Listing
from api.models.gameData import Building, Recipe, Specialization, WorkerType
from recipeWorker import RecipeWorker
from settings import Settings
from utils import (
    BuildingColorDelegate,
    ConsumablesDelegate,
    GradientColorDelegate,
    PriceGraph,
    SpecializationColorDelegate,
    StatusColorDelegate,
    align_add,
    calculate_profit_and_consumables,
    format_consumables,
)

DAYS_CONSUMABLE_BUFFER = 2

_logger = logging.getLogger(__name__)


class IntegerDelegate(QStyledItemDelegate):
    """Delegate that restricts input to non-negative integers only. Empty string is treated as 0."""

    class AllowEmptyIntValidator(QIntValidator):
        def validate(
            self, input_str: str, pos: int
        ) -> Tuple[QIntValidator.State, str, int]:
            if input_str == "":
                return QIntValidator.State.Acceptable
            return super().validate(input_str, pos)

    def createEditor(
        self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QWidget:
        """Create a line edit with integer validation."""
        editor = QLineEdit(parent)
        validator = IntegerDelegate.AllowEmptyIntValidator(
            0, 2147483647, editor
        )  # Non-negative integers
        editor.setValidator(validator)
        return editor

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:
        """Set the editor's data from the model."""
        if isinstance(editor, QLineEdit):
            value = index.data(Qt.ItemDataRole.DisplayRole)
            editor.setText(str(value) if value else "")

    def setModelData(
        self, editor: QWidget, model: QAbstractTableModel, index: QModelIndex
    ) -> None:
        """Set the model's data from the editor. Empty string is treated as 0."""
        if isinstance(editor, QLineEdit):
            text = editor.text().strip()
            if text:
                model.setData(index, int(text), Qt.ItemDataRole.EditRole)
            else:
                # Empty string is treated as 0
                model.setData(index, 0, Qt.ItemDataRole.EditRole)


class BaseWindow(QWidget):

    class RecipeStatus(IntEnum):
        IN_PROGRESS = 1
        STANDBY = 2
        NO_MATERIALS = 3

    class ProductTableModel(QAbstractTableModel):
        class ProductTableItem(BaseModel):
            material_id: int
            material_name: str
            amount: float
            current_price: float
            average_price: float

            @property
            def price_delta(self) -> float:
                return self.current_price - self.average_price

        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self._data: List[BaseWindow.ProductTableModel.ProductTableItem] = []
            self._headers = ["Material", "Amount", "Price Δ"]
            self._sort_column = 2  # Default sort by Price
            self._sort_order = Qt.SortOrder.DescendingOrder

            # Create delegate for price delta column
            self.price_delta_delegate = GradientColorDelegate(parent)

        def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
            return len(self._data)

        def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
            return len(self._headers)

        def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
            if not index.isValid():
                return None

            row = index.row()
            col = index.column()

            if role == Qt.ItemDataRole.DisplayRole:
                if col == 0:  # Material name
                    return self._data[row].material_name
                elif col == 1:  # Amount
                    return self._data[row].amount
                elif col == 2:  # Price delta
                    price_delta = self._data[row].price_delta
                    average_price = self._data[row].average_price / 100
                    return (
                        f"{price_delta / average_price:+.1f}% ({price_delta / 100:+.2f})"
                        if price_delta != 0
                        else "0.00"
                    )

            elif role == Qt.ItemDataRole.UserRole:
                if col == 2:  # Price delta - provide raw numeric value for delegate
                    return self._data[row].price_delta / self._data[row].average_price

            return None

        def headerData(
            self,
            section: int,
            orientation: Qt.Orientation,
            role: int = Qt.ItemDataRole.DisplayRole,
        ):
            if role == Qt.ItemDataRole.DisplayRole:
                if orientation == Qt.Orientation.Horizontal:
                    return self._headers[section]
            return None

        def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder):
            """Sort table by given column and order."""
            self.layoutAboutToBeChanged.emit()

            self._sort_column = column
            self._sort_order = order

            reverse = order == Qt.SortOrder.DescendingOrder

            if column == 0:  # Material name
                self._data.sort(key=lambda x: x.material_name.lower(), reverse=reverse)
            elif column == 1:  # Amount
                self._data.sort(key=lambda x: x.amount, reverse=reverse)
            elif column == 2:  # Price delta
                self._data.sort(
                    key=lambda x: x.price_delta / x.average_price, reverse=reverse
                )

            self.layoutChanged.emit()

        def set_materials(
            self,
            materials_dict: Dict[int, int],
        ):
            """Populate the table with materials from the dictionary."""
            self.beginResetModel()
            self._data.clear()

            for material_id, amount in materials_dict.items():
                # Skip materials with zero or negative amounts
                if amount <= 0:
                    continue
                material = GameDataManager.get_material_by_id(material_id)
                if material is None:
                    _logger.warning(
                        f"Material with ID {material_id} not found in game data, skipping."
                    )
                    continue
                listing = Exchange.listings.get(material_id)

                self._data.append(
                    self.ProductTableItem(
                        material_id=material_id,
                        material_name=material.name,
                        amount=amount,
                        current_price=listing.current_price if listing else 0.0,
                        average_price=listing.average_price if listing else 0.0,
                    )
                )

            # Apply current sort order
            self.sort(self._sort_column, self._sort_order)

            # Update gradient delegate value range based on price deltas
            if self._data:
                price_deltas = [
                    row.price_delta / row.average_price for row in self._data
                ]
                min_delta = min(price_deltas)
                max_delta = max(price_deltas)
                # Ensure range is centered around 0 for proper red/green gradient
                abs_max = max(abs(min_delta), abs(max_delta))
                self.price_delta_delegate.set_value_range(-abs_max, abs_max)

            self.endResetModel()

    class ProductTableView(QTableView):
        recipe_clicked = Signal(Recipe)

        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
            self.setSortingEnabled(True)
            self.technology_levels: Dict[int, int] = {}
            self._column_sort_state: Dict[int, Qt.SortOrder] = {}

            self.clicked.connect(self.handle_table_clicked)
            self.horizontalHeader().sortIndicatorChanged.connect(
                self._on_sort_indicator_changed
            )

        def _on_sort_indicator_changed(
            self, logical_index: int, order: Qt.SortOrder
        ) -> None:
            """Override sort behavior to default to descending on first click."""
            # If this column hasn't been clicked before, force descending order
            if logical_index not in self._column_sort_state:
                self._column_sort_state[logical_index] = Qt.SortOrder.DescendingOrder
                self.sortByColumn(logical_index, Qt.SortOrder.DescendingOrder)
            else:
                # Toggle between ascending and descending for subsequent clicks
                self._column_sort_state[logical_index] = order

        @Slot(QModelIndex)
        def handle_table_clicked(self, index: QModelIndex) -> None:
            if not index.isValid():
                return
            # Get the material from the model
            source_model = self.model()
            assert isinstance(source_model, BaseWindow.ProductTableModel)
            row = index.row()
            if row < len(source_model._data):
                material_id = source_model._data[row].material_id

                # Find the most profitable recipe that produces this material
                best_recipe = None
                best_profit = float("-inf")

                for recipe in GameDataManager.get().recipes:
                    if recipe.output.id != material_id:
                        continue

                    # Get tech level for this recipe's building
                    building = GameDataManager.get_building(recipe.producedIn)
                    if building is None:
                        continue
                    tech_level = self.technology_levels.get(building.specialization, 0)

                    # Calculate profit for this recipe
                    result = calculate_profit_and_consumables(recipe, tech_level)
                    if result is None:
                        continue

                    profit, _, _ = result
                    if profit > best_profit:
                        best_profit = profit
                        best_recipe = recipe

                # Emit the best recipe if found
                if best_recipe is not None:
                    self.recipe_clicked.emit(best_recipe)

        def setModel(self, model: "BaseWindow.ProductTableModel") -> None:
            """Override setModel to configure delegates from the model."""
            super().setModel(model)
            if model:
                self.setItemDelegateForColumn(2, model.price_delta_delegate)

    class BuildingTableModel(QAbstractTableModel):
        class BuildingItem(BaseModel):
            building_name: str
            total_duration: (
                float  # Total duration in hours for all recipes in this building
            )

        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self._data: List[BaseWindow.BuildingTableModel.BuildingItem] = []
            self._headers = ["Building", "Total Duration"]

            # Create delegates
            self.building_delegate = BuildingColorDelegate(parent)
            self.duration_delegate = GradientColorDelegate(parent)

        def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
            return len(self._data)

        def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
            return len(self._headers)

        def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
            if not index.isValid():
                return None

            row = index.row()
            col = index.column()

            if role == Qt.ItemDataRole.DisplayRole:
                if col == 0:  # Building name
                    return self._data[row].building_name
                elif col == 1:  # Total duration
                    return f"{self._data[row].total_duration:.2f}h"

            elif role == Qt.ItemDataRole.UserRole:
                if col == 0:  # Building name - for sorting
                    return self._data[row].building_name.lower()
                elif col == 1:  # Total duration - for sorting
                    return self._data[row].total_duration

            return None

        def headerData(
            self,
            section: int,
            orientation: Qt.Orientation,
            role: int = Qt.ItemDataRole.DisplayRole,
        ):
            if role == Qt.ItemDataRole.DisplayRole:
                if orientation == Qt.Orientation.Horizontal:
                    return self._headers[section]
            return None

        def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder):
            """Sort table by given column and order."""
            self.layoutAboutToBeChanged.emit()

            reverse = order == Qt.SortOrder.DescendingOrder

            if column == 0:  # Building name
                self._data.sort(key=lambda x: x.building_name.lower(), reverse=reverse)
            elif column == 1:  # Total duration
                self._data.sort(key=lambda x: x.total_duration, reverse=reverse)

            self.layoutChanged.emit()

        def set_building_durations(
            self,
            building_durations: Dict[int, float],
        ):
            """Populate the table with building names and their total recipe durations."""
            self.beginResetModel()
            self._data.clear()

            for building_id, total_duration in building_durations.items():
                self._data.append(
                    self.BuildingItem(
                        building_name=GameDataManager.get_building(building_id).name,
                        total_duration=total_duration,
                    )
                )

            # Sort by total duration descending by default
            self._data.sort(key=lambda x: x.total_duration, reverse=True)

            # Update gradient delegate value range based on durations
            if self._data:
                durations = [row.total_duration for row in self._data]
                min_duration = min(durations)
                max_duration = max(durations)
                self.duration_delegate.set_value_range(min_duration, max_duration)

            self.endResetModel()

    class BuildingTableView(QTableView):
        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
            self.setSortingEnabled(True)
            self._column_sort_state: Dict[int, Qt.SortOrder] = {}

            self.horizontalHeader().sortIndicatorChanged.connect(
                self._on_sort_indicator_changed
            )

        def _on_sort_indicator_changed(
            self, logical_index: int, order: Qt.SortOrder
        ) -> None:
            """Override sort behavior to default to descending on first click."""
            # If this column hasn't been clicked before, force descending order
            if logical_index not in self._column_sort_state:
                self._column_sort_state[logical_index] = Qt.SortOrder.DescendingOrder
                self.sortByColumn(logical_index, Qt.SortOrder.DescendingOrder)
            else:
                # Toggle between ascending and descending for subsequent clicks
                self._column_sort_state[logical_index] = order

        def setModel(self, model: "BaseWindow.BuildingTableModel") -> None:
            """Override setModel to configure delegates from the model."""
            super().setModel(model)
            if model:
                self.setItemDelegateForColumn(0, model.building_delegate)
                self.setItemDelegateForColumn(1, model.duration_delegate)

    class RecipeTableModel(QAbstractTableModel):
        # Signal emitted when 'To buy' value changes: (recipe_id, new_value)
        to_buy_changed = Signal(int, object)

        class RecipeTableItem(BaseModel):
            recipe_id: int
            output_name: Optional[str] = None
            amount_to_buy: Optional[int] = None
            amount: Optional[int]
            amount_over_ordered: Optional[float] = None
            profit_per_hour: Optional[float] = None
            current_output_price: Optional[float] = None
            average_output_price: Optional[float] = None
            building_name: Optional[str] = None
            status: "BaseWindow.RecipeStatus"
            duration: Optional[float] = (
                None  # Duration in hours, calculated from recipe time and building speed
            )

            @property
            def price_delta(self) -> Optional[float]:
                if (
                    self.current_output_price is not None
                    and self.average_output_price is not None
                ):
                    return self.current_output_price - self.average_output_price
                return None

        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self._data: List[BaseWindow.RecipeTableModel.RecipeTableItem] = []
            self._headers = [
                "Recipe",
                "To buy",
                "Amount",
                "Duration",
                "Profit/hr",
                "Price Δ",
                "In Progress",
                "Building",
            ]

            # Create delegates
            self.integer_delegate = IntegerDelegate(parent)
            self.duration_delegate = GradientColorDelegate(parent)
            self.profit_delegate = GradientColorDelegate(parent)
            self.price_delta_delegate = GradientColorDelegate(parent)
            self.status_delegate = StatusColorDelegate(parent)
            self.building_delegate = BuildingColorDelegate(parent)

        def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
            return len(self._data)

        def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
            return len(self._headers)

        def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
            if not index.isValid():
                return None

            row = index.row()
            col = index.column()

            if role == Qt.ItemDataRole.DisplayRole:
                if col == 0:  # Recipe name (output)
                    return self._data[row].output_name
                elif col == 1:  # To buy
                    return (
                        self._data[row].amount_to_buy
                        if self._data[row].amount_to_buy
                        else ""
                    )
                elif col == 2:  # Amount
                    return (
                        self._data[row].amount
                        if self._data[row].amount_over_ordered is None
                        else f"{self._data[row].amount} (+{self._data[row].amount_over_ordered:.1f})"
                    )
                elif col == 3:  # Duration
                    return (
                        f"{self._data[row].duration:.2f}h"
                        if self._data[row].duration
                        else ""
                    )
                elif col == 4:  # Profit/hr
                    profit_per_hour = self._data[row].profit_per_hour
                    if profit_per_hour is not None:
                        return f"{profit_per_hour:,.0f}"
                    return "N/A"
                elif col == 5:  # Price Δ
                    price_delta = self._data[row].price_delta
                    average_price = self._data[row].average_output_price
                    if (
                        price_delta is not None
                        and average_price is not None
                        and average_price != 0
                    ):
                        return (
                            f"{price_delta / average_price:+.1%} ({price_delta / 100:+.2f})"
                            if price_delta != 0
                            else "0.00"
                        )
                    return "N/A"
                elif col == 6:  # In progress
                    return self._data[row].status.name.replace("_", " ").title()
                elif col == 7:  # Building name
                    return self._data[row].building_name

            elif role == Qt.ItemDataRole.UserRole:
                if col == 0:  # Recipe name - provide output name for sorting
                    return (
                        self._data[row].output_name.lower()
                        if self._data[row].output_name
                        else ""
                    )
                elif col == 1:  # To buy - provide raw numeric value for sorting
                    return (
                        self._data[row].amount_to_buy
                        if self._data[row].amount_to_buy
                        else 0
                    )
                elif col == 2:  # Amount - provide raw numeric value for sorting
                    return self._data[row].amount
                elif (
                    col == 3
                ):  # Duration - provide raw numeric value for sorting and delegate
                    # Only show duration for IN_PROGRESS recipes, sort others as if they have infinite duration to push them to the bottom
                    return (
                        self._data[row].duration
                        if self._data[row].duration is not None
                        and self._data[row].status
                        == BaseWindow.RecipeStatus.IN_PROGRESS
                        else float("inf")
                    )
                elif col == 4:  # Profit/hr - provide raw numeric value for delegate
                    return self._data[row].profit_per_hour
                elif col == 5:  # Price Δ - provide raw numeric value for delegate
                    return (
                        self._data[row].price_delta
                        / self._data[row].average_output_price
                        if self._data[row].price_delta is not None
                        and self._data[row].average_output_price
                        else None
                    )
                elif col == 6:
                    return self._data[row].status.value
                elif col == 7:
                    return self._data[row].building_name

            return None

        def headerData(
            self,
            section: int,
            orientation: Qt.Orientation,
            role: int = Qt.ItemDataRole.DisplayRole,
        ):
            if role == Qt.ItemDataRole.DisplayRole:
                if orientation == Qt.Orientation.Horizontal:
                    return self._headers[section]
            return None

        def flags(self, index: QModelIndex) -> Qt.ItemFlag:
            """Return item flags, making column 1 (To buy) editable."""
            if not index.isValid():
                return Qt.ItemFlag.NoItemFlags

            default_flags = super().flags(index)

            # Make column 1 (To buy) editable
            if index.column() == 1:
                return default_flags | Qt.ItemFlag.ItemIsEditable

            return default_flags

        def setData(
            self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole
        ) -> bool:
            """Handle data changes for editable cells. Empty string is treated as 0."""
            if not index.isValid() or role != Qt.ItemDataRole.EditRole:
                return False

            row = index.row()
            col = index.column()

            # Only allow editing column 1 (To buy)
            if col == 1:
                # Convert to integer, handle empty strings and invalid input
                try:
                    if value == "" or value is None:
                        # Empty string or None is treated as 0
                        int_value = 0
                    else:
                        int_value = int(value)
                        # Ensure non-negative values
                        if int_value < 0:
                            return False
                except (ValueError, TypeError):
                    return False

                # Update the data
                self._data[row].amount_to_buy = int_value
                self.dataChanged.emit(index, index, [role])

                # Emit signal with recipe_id and new value
                recipe_id = self._data[row].recipe_id
                self.to_buy_changed.emit(recipe_id, int_value)

                return True

            return False

        def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder):
            """Sort table by given column and order."""
            self.layoutAboutToBeChanged.emit()

            reverse = order == Qt.SortOrder.DescendingOrder

            if column == 0:  # Recipe name
                self._data.sort(key=lambda x: x.output_name.lower(), reverse=reverse)
            elif column == 1:  # To buy
                self._data.sort(
                    key=lambda x: (
                        x.amount_to_buy is None,
                        x.amount_to_buy if x.amount_to_buy is not None else 0,
                    ),
                    reverse=reverse,
                )
            elif column == 2:  # Amount
                self._data.sort(
                    key=lambda x: (
                        x.amount is None,
                        x.amount if x.amount is not None else 0,
                    ),
                    reverse=reverse,
                )
            elif column == 3:  # Duration
                self._data.sort(
                    key=lambda x: (
                        x.duration is None,
                        x.duration if x.duration is not None else float("inf"),
                    ),
                    reverse=reverse,
                )
            elif column == 4:  # Profit/hr
                self._data.sort(
                    key=lambda x: (
                        x.profit_per_hour is None,
                        (
                            x.profit_per_hour
                            if x.profit_per_hour is not None
                            else float("-inf")
                        ),
                    ),
                    reverse=reverse,
                )
            elif column == 5:  # Price Δ
                self._data.sort(
                    key=lambda x: (
                        x.price_delta is None and x.average_output_price is not None,
                        (
                            x.price_delta / x.average_output_price
                            if x.price_delta is not None
                            and x.average_output_price is not None
                            else float("-inf")
                        ),
                    ),
                    reverse=reverse,
                )
            elif column == 6:  # In Progress (status)
                self._data.sort(key=lambda x: x.status.name.lower(), reverse=reverse)
            elif column == 7:  # Building name
                self._data.sort(key=lambda x: x.building_name.lower(), reverse=reverse)

            self.layoutChanged.emit()

        def set_recipes(
            self,
            recipes: List[RecipeTableItem],
            technology_levels: Dict[int, int],
            building_count: Dict[int, int],
        ):
            """Populate the table with production orders."""
            self.beginResetModel()
            self._data.clear()

            for recipe_item in recipes:
                recipe = GameDataManager.get_recipe_by_id(recipe_item.recipe_id)

                if recipe is None:
                    _logger.warning(
                        f"Recipe with ID {recipe_item.recipe_id} not found in game data, skipping."
                    )
                    continue
                building = GameDataManager.get_building(recipe.producedIn)
                if building is None:
                    _logger.warning(
                        f"Building with ID {recipe.producedIn} not found in game data for recipe {GameDataManager.get_item_name(recipe.output.id)} [{recipe.id}], skipping."
                    )
                    continue
                tech_level = technology_levels.get(building.specialization, 0)

                if recipe_item.output_name is None:
                    recipe_item.output_name = GameDataManager.get_item_name(
                        recipe.output.id
                    )

                if recipe_item.duration is None and recipe_item.amount is not None:
                    recipe_item.duration = BaseWindow.calculate_recipe_duration(
                        recipe,
                        tech_level,
                        building_count[recipe.producedIn],
                        recipe_item.amount,
                    )

                if recipe_item.building_name is None:
                    recipe_item.building_name = building.name if building else "Unknown"

                if recipe_item.profit_per_hour is None:
                    result = calculate_profit_and_consumables(recipe, tech_level)
                    if result is not None:
                        recipe_item.profit_per_hour, _, _ = result
                    else:
                        recipe_item.profit_per_hour = None

                listing = Exchange.listings.get(recipe.output.id)
                recipe_item.current_output_price = (
                    listing.current_price if listing else None
                )
                recipe_item.average_output_price = (
                    listing.average_price if listing else None
                )

                self._data.append(recipe_item)

            # Sort by Profit/hr (column 2) descending by default
            self._data.sort(
                key=lambda x: (
                    x.profit_per_hour is None,
                    (
                        x.profit_per_hour
                        if x.profit_per_hour is not None
                        else float("-inf")
                    ),
                ),
                reverse=True,
            )

            self.endResetModel()
            self.update_delegate_ranges()

        def update_delegate_ranges(self):
            """Update the min/max ranges for gradient delegates based on all rows."""
            profit_values = []
            duration_values = []
            price_delta_values = []

            # Iterate through all rows in the model
            for row_data in self._data:
                profit_val = row_data.profit_per_hour
                if (
                    profit_val is not None
                    and isinstance(profit_val, (int, float))
                    and math.isfinite(profit_val)
                ):
                    profit_values.append(profit_val)

                duration_val = row_data.duration
                if (
                    row_data.status == BaseWindow.RecipeStatus.IN_PROGRESS
                    and duration_val is not None
                    and isinstance(duration_val, (int, float))
                    and math.isfinite(duration_val)
                ):
                    duration_values.append(duration_val)

                price_delta_val = (
                    row_data.price_delta / row_data.average_output_price
                    if row_data.price_delta is not None
                    and row_data.average_output_price
                    else None
                )
                if (
                    price_delta_val is not None
                    and isinstance(price_delta_val, (int, float))
                    and math.isfinite(price_delta_val)
                ):
                    price_delta_values.append(price_delta_val)

            # Update profit delegate range
            if profit_values:
                profit_min = min(profit_values)
                profit_max = max(profit_values)
                if profit_max == profit_min:
                    profit_max = profit_min + 1.0
            else:
                profit_min = profit_max = 0.0

            self.profit_delegate.set_value_range(profit_min, profit_max)

            # Update duration delegate range
            if duration_values:
                duration_min = min(duration_values)
                duration_max = max(duration_values)
                if duration_max == duration_min:
                    duration_max = duration_min + 1.0
            else:
                duration_min = duration_max = 0.0

            self.duration_delegate.set_value_range(duration_min, duration_max)

            # Update price delta delegate range
            if price_delta_values:
                price_delta_min = min(price_delta_values)
                price_delta_max = max(price_delta_values)
                if price_delta_max == price_delta_min:
                    price_delta_max = price_delta_min + 1.0
            else:
                price_delta_min = price_delta_max = 0.0

            self.price_delta_delegate.set_value_range(price_delta_min, price_delta_max)

    class RecipeTableView(QTableView):
        recipe_clicked = Signal(Recipe)

        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
            self.setSortingEnabled(True)
            self._column_sort_state: Dict[int, Qt.SortOrder] = {}

            self.clicked.connect(self.handle_table_clicked)
            self.horizontalHeader().sortIndicatorChanged.connect(
                self._on_sort_indicator_changed
            )

        def _on_sort_indicator_changed(
            self, logical_index: int, order: Qt.SortOrder
        ) -> None:
            """Override sort behavior to default to descending on first click.
            Profit/hr (column 4) and Price Δ (column 5) always sort descending."""
            # Columns that should always sort descending: Profit/hr (4) and Price Δ (5)
            always_descending_columns = {4, 5}

            if logical_index in always_descending_columns:
                # Always force descending order for these columns
                self._column_sort_state[logical_index] = Qt.SortOrder.DescendingOrder
                self.sortByColumn(logical_index, Qt.SortOrder.DescendingOrder)
            elif logical_index not in self._column_sort_state:
                # If this column hasn't been clicked before, force descending order
                self._column_sort_state[logical_index] = Qt.SortOrder.DescendingOrder
                self.sortByColumn(logical_index, Qt.SortOrder.DescendingOrder)
            else:
                # Toggle between ascending and descending for subsequent clicks
                self._column_sort_state[logical_index] = order

        @Slot(QModelIndex)
        def handle_table_clicked(self, index: QModelIndex) -> None:
            if not index.isValid():
                return
            # Get the recipe from the model
            source_model = self.model()
            assert isinstance(source_model, BaseWindow.RecipeTableModel)
            row = index.row()
            if row < len(source_model._data):
                recipe_id = source_model._data[row].recipe_id
                recipe = GameDataManager.get_recipe_by_id(recipe_id)
                if recipe:
                    self.recipe_clicked.emit(recipe)

        def setModel(self, model: "BaseWindow.RecipeTableModel") -> None:
            """Override setModel to configure delegates from the model."""
            super().setModel(model)
            if model:
                self.setItemDelegateForColumn(1, model.integer_delegate)
                self.setItemDelegateForColumn(3, model.duration_delegate)
                self.setItemDelegateForColumn(4, model.profit_delegate)
                self.setItemDelegateForColumn(5, model.price_delta_delegate)
                self.setItemDelegateForColumn(6, model.status_delegate)
                self.setItemDelegateForColumn(7, model.building_delegate)

                # Connect to model data changes to update delegate ranges
                model.dataChanged.connect(model.update_delegate_ranges)
                model.layoutChanged.connect(model.update_delegate_ranges)
                model.modelReset.connect(model.update_delegate_ranges)

    update_tab_name_signal = Signal(int, str)

    def __init__(self, parent: QWidget, base_id: int, company: Company | None = None):
        super().__init__(parent)

        self.base_id = base_id
        self.technology_levels: Dict[int, int] = (
            {technology.id: technology.level for technology in company.technologies}
            if company
            else {}
        )
        self.ships = company.ships if company else []

        # Create main layout
        main_layout = QVBoxLayout(self)

        # Create main horizontal splitter
        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        main_layout.addWidget(main_splitter)

        # Create left side with vertical splitter for recipe table and building summary
        left_splitter = QSplitter(Qt.Orientation.Vertical, self)

        # Create Recipe table (top-left)
        self.recipe_table_model = BaseWindow.RecipeTableModel(self)
        self.recipe_table_view = BaseWindow.RecipeTableView(self)
        self.recipe_table_view.setModel(self.recipe_table_model)

        left_splitter.addWidget(self.recipe_table_view)

        # Create bottom left splitter for building summary and control panel
        bottom_left_splitter = QSplitter(Qt.Orientation.Horizontal, self)

        left_splitter.addWidget(bottom_left_splitter)

        # Create Building Summary table (bottom-left)
        self.building_summary_model = BaseWindow.BuildingTableModel(self)
        self.building_summary_view = BaseWindow.BuildingTableView(self)
        self.building_summary_view.setModel(self.building_summary_model)

        bottom_left_splitter.addWidget(self.building_summary_view)

        # Create Control Panel
        control_widget = QWidget(self)
        control_layout = QVBoxLayout()

        # Create total-weight label
        self.total_weight_label = QLabel("Total weight: N/A", self)
        control_layout.addWidget(self.total_weight_label)

        control_layout.addStretch()  # Push controls to the top

        # Set the layout on the control widget and add to splitter
        control_widget.setLayout(control_layout)
        bottom_left_splitter.addWidget(control_widget)

        # Set stretch factors for bottom left splitter
        bottom_left_splitter.setStretchFactor(0, 2)  # Building summary gets more space
        bottom_left_splitter.setStretchFactor(1, 1)  # Control panel

        # Set stretch factors for left splitter
        left_splitter.setStretchFactor(0, 4)  # Recipe table gets more space
        left_splitter.setStretchFactor(1, 1)  # Building summary table

        main_splitter.addWidget(left_splitter)

        # Create Product table (center)
        self.product_table_model = BaseWindow.ProductTableModel(self)
        self.product_table_view = BaseWindow.ProductTableView(self)
        self.product_table_view.setModel(self.product_table_model)
        self.product_table_view.technology_levels = self.technology_levels

        main_splitter.addWidget(self.product_table_view)

        # Create PriceGraph (right)
        self.price_graph = PriceGraph(self)
        main_splitter.addWidget(self.price_graph)

        # Connect recipe table clicks to update the price graph
        self.recipe_table_view.recipe_clicked.connect(self.price_graph.plot_recipe)
        # Connect product table clicks to update the price graph
        self.product_table_view.recipe_clicked.connect(self.price_graph.plot_recipe)

        # Connect to 'To buy' column changes
        self.recipe_table_model.to_buy_changed.connect(self.handle_to_buy_changed)

        # Set stretch factors for main splitter
        main_splitter.setStretchFactor(0, 2)  # Left side (recipe + building summary)
        main_splitter.setStretchFactor(1, 1)  # Product table (center)
        main_splitter.setStretchFactor(2, 5)  # Price graph (right) gets most space

    @Slot()
    def calculate_total_weight(self) -> float:
        """Calculate and update the total weight of materials to buy."""
        total_weight = 0.0
        for item in self.recipe_table_model._data:
            if item.amount_to_buy and item.amount_to_buy > 0:
                recipe = GameDataManager.get_recipe_by_id(item.recipe_id)
                if recipe:
                    for consumable in recipe.inputs:
                        material = GameDataManager.get_material_by_id(consumable.id)
                        if material:
                            total_weight += (
                                material.weight * consumable.am * item.amount_to_buy
                            )

        if total_weight > 0:
            self.total_weight_label.setText(f"Total weight: {total_weight:.2f}")
        else:
            self.total_weight_label.setText("Total weight: N/A")
        return total_weight

    @Slot(int, object)
    def handle_to_buy_changed(self, recipe_id: int, amount: int) -> None:
        """
        Handle changes to the 'To buy' column.

        Args:
            recipe_id: The ID of the recipe whose 'To buy' value changed
            amount: The new amount value (empty string/None is treated as 0)
        """
        self.calculate_total_weight()

    @Slot(Company)
    def handle_company_loaded(self, company: Company):
        self.technology_levels = {
            technology.id: technology.level for technology in company.technologies
        }
        self.ships = company.ships
        # Update technology levels in product table view
        self.product_table_view.technology_levels = self.technology_levels

    @Slot(Base)
    def handle_base_loaded(self, base: Base):
        if base.id != self.base_id:
            return

        if base.warehouse is None:
            _logger.warning(f"Base {base.name} has no warehouse data.")
            return

        materials_dict: Dict[int, int] = {
            mat.id: mat.amount for mat in base.warehouse.materials
        }

        # Add ship warehouse arriving to destination
        for ship in self.ships:
            if (
                ship.flight is not None
                and ship.flight.dest_planet_id == base.planet_id
                and ship.flight.type == FlightType.NORMAL
                and ship.warehouse is not None
            ):
                for mat in ship.warehouse.materials:
                    materials_dict[mat.id] = materials_dict.get(mat.id, 0) + mat.amount

        # Subtract consumables from workforce
        if base.workforce is not None:
            for consumable in base.workforce.consumption_materials:
                materials_dict[consumable.material_id] = (
                    materials_dict.get(consumable.material_id, 0)
                    - consumable.rate * DAYS_CONSUMABLE_BUFFER
                )

        # Find all active recipes
        recipes_dict: Dict[int, BaseWindow.RecipeTableModel.RecipeTableItem] = {}
        for order in base.production_orders:
            recipe = GameDataManager.get_recipe_by_id(order.recipe_id)

            # Amount already removed from materials_dict from previous orders of same recipe
            amount_prev = (
                recipes_dict[order.recipe_id].amount
                if order.recipe_id in recipes_dict
                else 0
            )

            potential_amount = self.find_potential_amount_for_recipe(
                recipe, materials_dict
            )
            amount_to_consume = (
                potential_amount if potential_amount < order.amount else order.amount
            )

            # Subtract active recipe consumables from warehouse materials
            for consumable in recipe.inputs:
                materials_dict[consumable.id] = (
                    materials_dict.get(consumable.id, 0)
                    - consumable.am * amount_to_consume
                )

            # Add active recipes to recipes_dict, summing amounts if multiple orders for same recipe
            recipes_dict[order.recipe_id] = BaseWindow.RecipeTableModel.RecipeTableItem(
                recipe_id=order.recipe_id,
                status=BaseWindow.RecipeStatus.IN_PROGRESS,
                amount=amount_prev + amount_to_consume,
                amount_over_ordered=(
                    order.amount - amount_to_consume
                    if order.amount > amount_to_consume
                    else None
                ),
            )

        recipes = list(recipes_dict.values())

        # Find all buildings
        building_types: Dict[BuildingType, int] = {}  # Dict of building type to count
        for building_slot in base.building_slots:
            if building_slot.building is None:
                continue
            building_types[building_slot.building.type] = (
                building_types.get(building_slot.building.type, 0)
                + building_slot.building.level
            )

        # Calculate durations for IN_PROGRESS only
        building_durations: Dict[str, float] = {}
        for recipe_item in recipes:
            recipe = GameDataManager.get_recipe_by_id(recipe_item.recipe_id)
            duration = self.calculate_recipe_duration(
                recipe,
                self.technology_levels.get(
                    GameDataManager.get_building(recipe.producedIn).specialization, 0
                ),
                building_types[recipe.producedIn],
                recipe_item.amount,
            )
            building_durations[recipe.producedIn] = duration + building_durations.get(
                recipe.producedIn, 0
            )
            recipe_item.duration = duration

        # Populate the BuildingSummaryTable with building durations
        self.building_summary_model.set_building_durations(building_durations)

        # Find potential recipes that we have materials for but are not currently producing (standby)
        for recipe in sorted(
            GameDataManager.get().recipes,
            key=lambda r: (
                result[0]
                if (
                    result := calculate_profit_and_consumables(
                        r,
                        self.technology_levels.get(
                            GameDataManager.get_building(r.producedIn).specialization, 0
                        ),
                    )
                )
                is not None
                else float("-inf")
            ),
            reverse=True,
        ):
            if recipe.producedIn not in building_types:
                continue
            tech_level = self.technology_levels.get(
                GameDataManager.get_building(recipe.producedIn).specialization, 0
            )
            if recipe.reqTech > tech_level:
                continue
            potential_amount = self.find_potential_amount_for_recipe(
                recipe, materials_dict
            )
            if potential_amount == 0:
                continue
            for input_mat in recipe.inputs:
                materials_dict[input_mat.id] = (
                    materials_dict[input_mat.id] - input_mat.am * potential_amount
                )
                assert (
                    materials_dict[input_mat.id] >= 0
                ), f"Material {input_mat.id} went negative for recipe {recipe.id} standby calculation."
            recipes.append(
                BaseWindow.RecipeTableModel.RecipeTableItem(
                    recipe_id=recipe.id,
                    status=BaseWindow.RecipeStatus.STANDBY,
                    amount=potential_amount,
                )
            )

        # Remove materials with zero amounts
        for mat_id in [
            mat_id
            for mat_id, amount in materials_dict.items()
            if amount > -1 and amount < 1
        ]:
            materials_dict.pop(mat_id)

        # Add potential recipes
        for recipe in GameDataManager.get().recipes:
            # Skip recipes already added
            if any(recipe.id == r.recipe_id for r in recipes):
                continue
            if recipe.producedIn not in building_types:
                continue
            tech_level = self.technology_levels.get(
                GameDataManager.get_building(recipe.producedIn).specialization, 0
            )
            if recipe.reqTech > tech_level:
                continue
            recipes.append(
                BaseWindow.RecipeTableModel.RecipeTableItem(
                    recipe_id=recipe.id,
                    status=BaseWindow.RecipeStatus.NO_MATERIALS,
                    amount=None,
                )
            )

        # Populate the RecipeTable with production orders
        self.recipe_table_model.set_recipes(
            recipes, self.technology_levels, building_types
        )

        # Populate the ProductTable with materials
        self.product_table_model.set_materials(materials_dict)

        # Update tab name with production time
        tab_name = f"{base.name} - {min(building_durations.values()):.1f}h"
        self.update_tab_name_signal.emit(base.id, tab_name)

    @staticmethod
    def find_potential_amount_for_recipe(
        recipe: Recipe, materials_dict: Dict[int, int]
    ) -> int:
        """
        Calculate the potential amount of a recipe that can be produced with the given materials dictionary.
        """
        potential_amount = math.inf
        for input_mat in recipe.inputs:
            if materials_dict.get(input_mat.id, 0) < input_mat.am:
                return 0
            potential_amount = min(
                potential_amount,
                materials_dict.get(input_mat.id, 0) // input_mat.am,
            )
        if potential_amount == math.inf:
            _logger.warning(
                f"Recipe {GameDataManager.get_item_name(recipe.output.id)} [{recipe.id}] has no inputs, skipping potential amount calculation."
            )
            return 0
        return potential_amount

    @staticmethod
    def calculate_recipe_duration(
        recipe: Recipe, tech_level: int, building_count: int, amount: int = 1
    ) -> float:
        """
        Calculate the duration of a recipe in hours
        """
        return (
            recipe.timeMinutes
            * amount
            / ((1 + 0.05 * tech_level) * building_count * 60)
        )

    def update_delegate_ranges(self):
        """Update the min/max ranges for gradient delegates based on all rows."""
        profit_values = []

        # Iterate through all rows in the model
        for row in range(self.recipe_table_model.rowCount()):
            # Get profit value (column 2 in display, position 3 in data tuple)
            profit_val = self.recipe_table_model._data[row].profit_per_hour
            if (
                profit_val is not None
                and isinstance(profit_val, (int, float))
                and math.isfinite(profit_val)
            ):
                profit_values.append(profit_val)

        # Update profit delegate range
        if profit_values:
            profit_min = min(profit_values)
            profit_max = max(profit_values)
            if profit_max == profit_min:
                profit_max = profit_min + 1.0
        else:
            profit_min = profit_max = 0.0

        self.profit_delegate.set_value_range(profit_min, profit_max)

        # Trigger a repaint of the affected columns
        if self.recipe_table_model.rowCount() > 0:
            self.recipe_table_view.viewport().update()
