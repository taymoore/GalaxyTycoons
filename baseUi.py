from enum import IntEnum
import logging
import math
from typing import Dict, List, Optional, Tuple

import matplotlib.cm as cm
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd
import pyqtgraph as pg
from pydantic import BaseModel, Field
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
    SpecializationColorDelegate,
    align_add,
    calculate_profit_and_consumables,
    format_consumables,
)

DAYS_CONSUMABLE_BUFFER = 2

_logger = logging.getLogger(__name__)


class BaseWindow(QWidget):

    class RecipeStatus(IntEnum):
        IN_PROGRESS = 1
        STANDBY = 2
        NO_MATERIALS = 3

    class ProductTableModel(QAbstractTableModel):
        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self._data: List[tuple[int, str, int, float]] = (
                []
            )  # (material_id, name, amount, price_delta)
            self._headers = ["Material", "Amount", "Price Δ"]
            self._sort_column = 2  # Default sort by Price
            self._sort_order = Qt.SortOrder.DescendingOrder

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
                    return self._data[row][1]
                elif col == 1:  # Amount
                    return self._data[row][2]
                elif col == 2:  # Price delta
                    price_delta = self._data[row][3]
                    return f"{price_delta:+.2f}" if price_delta != 0 else "0.00"

            elif role == Qt.ItemDataRole.UserRole:
                if col == 2:  # Price delta - provide raw numeric value for delegate
                    return self._data[row][3]

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
                self._data.sort(key=lambda x: x[1].lower(), reverse=reverse)
            elif column == 1:  # Amount
                self._data.sort(key=lambda x: x[2], reverse=reverse)
            elif column == 2:  # Price delta
                self._data.sort(key=lambda x: x[3], reverse=reverse)

            self.layoutChanged.emit()

        def set_materials(
            self,
            materials_dict: Dict[int, int],
            price_delta_delegate: GradientColorDelegate = None,
        ):
            """Populate the table with materials from the dictionary."""
            self.beginResetModel()
            self._data.clear()

            for material_id, amount in materials_dict.items():
                material = GameDataManager.get_material_by_id(material_id)
                listing = Exchange.listings.get(material_id)
                price_delta = (
                    listing.current_price - listing.average_price if listing else 0
                )
                if material:
                    self._data.append(
                        (material_id, material.name, amount, price_delta / 100)
                    )

            # Apply current sort order
            self.sort(self._sort_column, self._sort_order)

            # Update gradient delegate value range based on price deltas
            if price_delta_delegate and self._data:
                price_deltas = [row[3] for row in self._data]
                min_delta = min(price_deltas)
                max_delta = max(price_deltas)
                # Ensure range is centered around 0 for proper red/green gradient
                abs_max = max(abs(min_delta), abs(max_delta))
                price_delta_delegate.set_value_range(-abs_max, abs_max)

            self.endResetModel()

    class ProductTableView(QTableView):
        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
            self.setSortingEnabled(True)

    class RecipeTableModel(QAbstractTableModel):
        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self._data: List[
                tuple[int, str, Optional[int], Optional[float], str, str, str]
            ] = (
                []
            )  # (recipe_id, output_name, amount, profit_per_hour, building_name, inputs, status_name)
            self._headers = [
                "Recipe",
                "Amount",
                "Profit/hr",
                "In Progress",
                "Building",
                "Inputs",
            ]

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
                    return self._data[row][1]
                elif col == 1:  # Amount
                    return self._data[row][2]
                elif col == 2:  # Profit/hr
                    profit_per_hour = self._data[row][3]
                    if profit_per_hour is not None:
                        return f"{profit_per_hour:,.0f}"
                    return "N/A"
                elif col == 3:  # In progress
                    return self._data[row][6]
                elif col == 4:  # Building name
                    return self._data[row][4]
                elif col == 5:  # Inputs
                    return self._data[row][5]

            elif role == Qt.ItemDataRole.UserRole:
                if col == 2:  # Profit/hr - provide raw numeric value for delegate
                    return self._data[row][3]
                elif col == 1:  # Amount - provide raw numeric value for sorting
                    return self._data[row][2]

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

            if column == 0:  # Recipe name
                self._data.sort(key=lambda x: x[1].lower(), reverse=reverse)
            elif column == 1:  # Amount
                self._data.sort(
                    key=lambda x: (x[2] is None, x[2] if x[2] is not None else 0),
                    reverse=reverse,
                )
            elif column == 2:  # Profit/hr
                self._data.sort(
                    key=lambda x: (
                        x[3] is None,
                        x[3] if x[3] is not None else float("-inf"),
                    ),
                    reverse=reverse,
                )
            elif column == 3:  # In Progress (status)
                self._data.sort(key=lambda x: x[6].lower(), reverse=reverse)
            elif column == 4:  # Building name
                self._data.sort(key=lambda x: x[4].lower(), reverse=reverse)
            elif column == 5:  # Inputs
                self._data.sort(key=lambda x: x[5].lower(), reverse=reverse)

            self.layoutChanged.emit()

        def set_recipes(
            self,
            recipes: List[
                Tuple[Recipe, "BaseWindow.RecipeStatus", Optional[int], Optional[float]]
            ],
        ):
            """Populate the table with production orders."""
            self.beginResetModel()
            self._data.clear()

            for recipe, status, amount, profit_per_hour in recipes:
                if recipe:
                    # Get output name
                    output_name = GameDataManager.get_item_name(recipe.output.id)

                    # Get building name
                    building = GameDataManager.get_building(recipe.producedIn)
                    building_name = building.name if building else "Unknown"

                    # Format inputs
                    inputs_list = []
                    for input_mat in recipe.inputs:
                        mat_name = GameDataManager.get_item_name(input_mat.id)
                        inputs_list.append(f"{mat_name} x{input_mat.am}")
                    inputs_str = ", ".join(inputs_list)

                    self._data.append(
                        (
                            recipe.id,
                            output_name,
                            amount,
                            profit_per_hour,
                            building_name,
                            inputs_str,
                            status.name.replace("_", " ").title(),
                        )
                    )

            # Sort by Profit/hr (column 2) descending by default
            self._data.sort(
                key=lambda x: (
                    x[3] is None,
                    x[3] if x[3] is not None else float("-inf"),
                ),
                reverse=True,
            )

            self.endResetModel()

    class RecipeTableView(QTableView):
        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
            self.setSortingEnabled(True)

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

        # Create horizontal splitter
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        main_layout.addWidget(splitter)

        # Create Recipe table (left side)
        self.recipe_table_model = BaseWindow.RecipeTableModel(self)
        self.recipe_table_view = BaseWindow.RecipeTableView(self)
        self.recipe_table_view.setModel(self.recipe_table_model)

        # Add gradient color delegate for profit_per_hour column
        self.profit_delegate = GradientColorDelegate(self.recipe_table_view)
        self.recipe_table_view.setItemDelegateForColumn(2, self.profit_delegate)

        # Connect to model data changes to update delegate ranges
        self.recipe_table_model.dataChanged.connect(self.update_delegate_ranges)
        self.recipe_table_model.layoutChanged.connect(self.update_delegate_ranges)
        self.recipe_table_model.modelReset.connect(self.update_delegate_ranges)

        splitter.addWidget(self.recipe_table_view)

        # Create Product table (right side)
        self.product_table_model = BaseWindow.ProductTableModel(self)
        self.product_table_view = BaseWindow.ProductTableView(self)
        self.product_table_view.setModel(self.product_table_model)

        # Add gradient color delegate for price_delta column
        self.price_delta_delegate = GradientColorDelegate(self.product_table_view)
        self.product_table_view.setItemDelegateForColumn(2, self.price_delta_delegate)

        splitter.addWidget(self.product_table_view)

        # Set stretch factors (both tables get equal space)
        splitter.setStretchFactor(0, 1)  # Recipe table
        splitter.setStretchFactor(1, 1)  # Product table

    @Slot(Company)
    def handle_company_loaded(self, company: Company):
        self.technology_levels = {
            technology.id: technology.level for technology in company.technologies
        }
        self.ships = company.ships

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

        # Subtract active recipies consumables from warehouse materials
        for order in base.production_orders:
            recipe = GameDataManager.get_recipe_by_id(order.recipe_id)
            for consumable in recipe.inputs:
                materials_dict[consumable.id] = (
                    materials_dict.get(consumable.id, 0) - consumable.am * order.amount
                )

        # Subtract consumables from workforce
        if base.workforce is not None:
            for consumable in base.workforce.consumption_materials:
                materials_dict[consumable.material_id] = (
                    materials_dict.get(consumable.material_id, 0)
                    - consumable.rate * DAYS_CONSUMABLE_BUFFER
                )

        recipes_dict: Dict[
            int, Tuple[Recipe, BaseWindow.RecipeStatus, Optional[int], Optional[float]]
        ] = (
            {}
        )  # Dict of recipe_id to (Recipe, RecipeStatus, amount in progress / available, profit/hr)
        for order in base.production_orders:
            recipe = GameDataManager.get_recipe_by_id(order.recipe_id)
            result = calculate_profit_and_consumables(
                recipe,
                self.technology_levels.get(
                    GameDataManager.get_building(recipe.producedIn).specialization,
                    0,
                ),
            )
            if result:
                profit_per_hour, _, _ = result
            else:
                profit_per_hour = None

            recipes_dict[order.recipe_id] = (
                recipe,
                BaseWindow.RecipeStatus.IN_PROGRESS,
                order.amount + recipes_dict.get(order.recipe_id, (None, None, 0))[2],
                profit_per_hour,
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

        # Find potential recipes that we have materials for but are not currently producing (standby)
        for recipe in GameDataManager.get().recipes:
            if recipe.producedIn not in building_types:
                continue
            tech_level = self.technology_levels.get(
                GameDataManager.get_building(recipe.producedIn).specialization, 0
            )
            if recipe.reqTech > tech_level:
                continue
            potential_amount = math.inf
            for input_mat in recipe.inputs:
                if materials_dict.get(input_mat.id, 0) < input_mat.am:
                    potential_amount = 0
                    break
                potential_amount = min(
                    potential_amount,
                    materials_dict.get(input_mat.id, 0) // input_mat.am,
                )
            if potential_amount == math.inf:
                _logger.warning(
                    f"Recipe {GameDataManager.get_item_name(recipe.output.id)} [{recipe.id}] has no inputs, skipping standby check."
                )
                continue
            if potential_amount == 0:
                continue
            for input_mat in recipe.inputs:
                materials_dict[input_mat.id] = (
                    materials_dict[input_mat.id] - input_mat.am * potential_amount
                )
                assert (
                    materials_dict[input_mat.id] >= 0
                ), f"Material {input_mat.id} went negative for recipe {recipe.id} standby calculation."
            result = calculate_profit_and_consumables(recipe, tech_level)
            if result:
                profit_per_hour, _, _ = result
            else:
                profit_per_hour = None
            recipes.append(
                (
                    recipe,
                    BaseWindow.RecipeStatus.STANDBY,
                    potential_amount,
                    profit_per_hour,
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
            if any(recipe.id == r[0].id for r in recipes):
                continue
            if recipe.producedIn not in building_types:
                continue
            tech_level = self.technology_levels.get(
                GameDataManager.get_building(recipe.producedIn).specialization, 0
            )
            if recipe.reqTech > tech_level:
                continue
            result = calculate_profit_and_consumables(recipe, tech_level)
            if result:
                profit_per_hour, _, _ = result
            else:
                profit_per_hour = None
            recipes.append(
                (
                    recipe,
                    BaseWindow.RecipeStatus.NO_MATERIALS,
                    np.nan,
                    profit_per_hour,
                )
            )

        # Populate the RecipeTable with production orders
        self.recipe_table_model.set_recipes(recipes)

        # Populate the ProductTable with materials
        self.product_table_model.set_materials(
            materials_dict, self.price_delta_delegate
        )

        # Calculate production time
        production_time_minutes: Dict[int, float] = (
            {}
        )  # Dict of building_id to total production time in minutes
        for order in base.production_orders:
            recipe = GameDataManager.get_recipe_by_id(order.recipe_id)
            if recipe:
                if recipe.producedIn not in building_types:
                    _logger.warning(
                        f"Recipe {GameDataManager.get_item_name(GameDataManager.get_recipe_by_id(recipe.id).output.id)} [{recipe.id}] requires building {GameDataManager.get_building(recipe.producedIn).name} [{recipe.producedIn}] which is not present in base {base.name}."
                    )
                    continue
                specialization = GameDataManager.get_building(
                    recipe.producedIn
                ).specialization
                if specialization not in self.technology_levels:
                    _logger.warning(
                        f"Recipe {GameDataManager.get_item_name(GameDataManager.get_recipe_by_id(recipe.id).output.id)} [{recipe.id}] requires technology level {recipe.reqTech} which is not researched in company."
                    )
                production_time_minutes[recipe.producedIn] = (
                    recipe.timeMinutes * order.amount
                ) / (
                    building_types[recipe.producedIn]
                    * (
                        1
                        + (
                            0.05
                            * self.technology_levels.get(
                                specialization,
                                0,
                            )
                        )
                    )
                ) + production_time_minutes.get(
                    recipe.producedIn, 0
                )

        # Update tab name with production time
        production_time_hours = min(production_time_minutes.values()) / 60
        tab_name = f"{base.name} - {production_time_hours:.1f}h"
        self.update_tab_name_signal.emit(base.id, tab_name)

    def update_delegate_ranges(self):
        """Update the min/max ranges for gradient delegates based on all rows."""
        profit_values = []

        # Iterate through all rows in the model
        for row in range(self.recipe_table_model.rowCount()):
            # Get profit value (column 2 in display, position 3 in data tuple)
            profit_val = self.recipe_table_model._data[row][3]
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
