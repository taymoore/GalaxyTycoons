import logging
import math
from typing import Dict, List, Tuple

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
from api.models.company import Base, BuildingType, Company, ProductionOrder, Technology
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

    class ProductTableModel(QAbstractTableModel):
        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self._data: List[tuple[int, str, int, float]] = (
                []
            )  # (material_id, name, amount, price_delta)
            self._headers = ["Material", "Amount", "Price Δ"]
            self._sort_column = 1  # Default sort by Amount
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
                        (material_id, material.sName, amount, price_delta / 100)
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
            self._data: List[tuple[int, str, int, str, str, bool]] = (
                []
            )  # (recipe_id, output_name, amount, building_name, inputs, in_progress)
            self._headers = ["Recipe", "Amount", "In Progress", "Building", "Inputs"]

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
                elif col == 2:  # In progress
                    return self._data[row][5]
                elif col == 3:  # Building name
                    return self._data[row][3]
                elif col == 4:  # Inputs
                    return self._data[row][4]

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

        def set_production_orders(
            self, production_orders: List[Tuple[ProductionOrder, bool]]
        ):
            """Populate the table with production orders."""
            self.beginResetModel()
            self._data.clear()

            for order, in_progress in production_orders:
                recipe = GameDataManager.get_recipe_by_id(order.recipe_id)
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
                            order.amount,
                            building_name,
                            inputs_str,
                            in_progress,
                        )
                    )

            self.endResetModel()

    class RecipeTableView(QTableView):
        def __init__(self, parent: QWidget):
            super().__init__(parent)
            self.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )

    update_tab_name_signal = Signal(int, str)

    def __init__(self, parent: QWidget, company: Company | None = None):
        super().__init__(parent)

        self.technology_levels: Dict[int, Technology] = (
            {technology.id: technology.level for technology in company.technologies}
            if company
            else {}
        )

        # Create main layout
        main_layout = QVBoxLayout(self)

        # Create horizontal splitter
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        main_layout.addWidget(splitter)

        # Create Recipe table (left side)
        self.recipe_table_model = BaseWindow.RecipeTableModel(self)
        self.recipe_table_view = BaseWindow.RecipeTableView(self)
        self.recipe_table_view.setModel(self.recipe_table_model)
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

    @Slot(Base)
    def handle_base_loaded(self, base: Base):
        if base.warehouse is None:
            _logger.warning(f"Base {base.name} has no warehouse data.")
            return

        materials_dict: Dict[int, int] = {
            mat.id: mat.amount for mat in base.warehouse.materials
        }

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
                if (
                    materials_dict[consumable.material_id] < 1
                    and materials_dict[consumable.material_id] > -1
                ):
                    materials_dict.pop(consumable.material_id)

        # Populate the ProductTable with materials
        self.product_table_model.set_materials(
            materials_dict, self.price_delta_delegate
        )

        # Populate the RecipeTable with production orders
        self.recipe_table_model.set_production_orders(
            [(order, True) for order in base.production_orders]
        )

        # Find all buildings
        building_types: Dict[BuildingType, int] = {}  # Dict of building type to count
        for building_slot in base.building_slots:
            if building_slot.building is None:
                continue
            building_types[building_slot.building.type] = (
                building_types.get(building_slot.building.type, 0)
                + building_slot.building.level
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
