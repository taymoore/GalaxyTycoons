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
from PySide6.QtGui import QStandardItemModel, QStandardItem, QAction
from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QTableView,
    QTreeView,
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
    QStyledItemDelegate,
    QComboBox,
    QMenu,
)
from PySide6.QtGui import (
    QCloseEvent,
    QWheelEvent,
    QPixmap,
    QColor,
    QBrush,
    QPalette,
    QTextLayout,
    QTextCharFormat,
    QPainter,
    QTextOption,
)
from PySide6.QtCore import QPointF
import pyqtgraph as pg
import matplotlib.colors as mcolors
import matplotlib.cm as cm

from settings import Settings
from utils import (
    align_add,
    find_best_recipe_for_building,
    ConsumablesDelegate,
    format_consumables,
)
from api.gameData import GameDataManager
from api.models.gameData import Planet, Recipe, Specialization, Building, WorkerType
from api.exchange import Exchange
from api.models.exchange import Listing
from api.company import CompanyDataManager
from api.models.company import Company, Base, BuildingType
from recipeWorker import RecipeWorker

_logger = logging.getLogger(__name__)


class ConfigurationWindow(QWidget):

    class ConfigurationTreeModel(QStandardItemModel):
        def __init__(self, parent: QObject, settings: Settings):
            super().__init__(parent)
            self.settings = settings
            self.setHorizontalHeaderLabels(
                ["Name", "Level", "Best Recipe", "Profit / hr", "Consumables"]
            )

        def populate_from_company(self, company: Company):
            """Populate tree from Company data (creates placeholder rows for bases)."""
            self.clear()
            self.setHorizontalHeaderLabels(
                ["Name", "Level", "Best Recipe", "Profit / hr", "Consumables"]
            )

            game_data = GameDataManager.get()

            # Create a mapping of planet_id to planet name
            planet_map = {}
            for system in game_data.systems:
                if system.planets:
                    for planet in system.planets:
                        planet_map[planet.id] = planet.name

            # Create placeholder rows for each base (buildings will be added via base_loaded signal)
            for base in company.bases:
                planet_name = planet_map.get(
                    base.planet_id, f"Unknown Planet {base.planet_id}"
                )

                # Create parent item for planet/base
                name_item = QStandardItem(f"{planet_name} - {base.name}")
                level_item = QStandardItem("")
                recipe_item = QStandardItem("")
                profit_item = QStandardItem("")
                consumables_item = QStandardItem("")

                # Make parent row non-editable
                name_item.setEditable(False)
                level_item.setEditable(False)
                recipe_item.setEditable(False)
                profit_item.setEditable(False)
                consumables_item.setEditable(False)

                # Store base ID for later lookup
                name_item.setData(base, Qt.ItemDataRole.UserRole)

                self.appendRow(
                    [name_item, level_item, recipe_item, profit_item, consumables_item]
                )

        def populate_base_buildings(self, base: Base):
            """Populate buildings for a specific base."""
            game_data = GameDataManager.get()
            building_map = {building.id: building for building in game_data.buildings}

            # Find the parent item for this base
            parent_item = None
            for row in range(self.rowCount()):
                item = self.item(row, 0)
                if item and item.data(Qt.ItemDataRole.UserRole).id == base.id:
                    parent_item = item
                    break

            if not parent_item:
                _logger.warning(f"Could not find parent item for base {base.id}")
                return

            # Clear any existing children
            parent_item.removeRows(0, parent_item.rowCount())

            # Add buildings as children
            for slot in base.building_slots:
                if slot.building and slot.building.type != BuildingType.UNDEFINED:
                    building_info = building_map.get(slot.building.type)
                    building_name = (
                        building_info.name
                        if building_info
                        else f"Building {slot.building.type}"
                    )

                    child_name = QStandardItem(building_name)
                    child_level = QStandardItem(str(slot.building.level))
                    child_recipe = QStandardItem("")
                    child_profit = QStandardItem("")
                    child_consumables = QStandardItem("")

                    # Make all child items non-editable
                    child_name.setEditable(False)
                    child_level.setEditable(False)
                    child_recipe.setEditable(False)
                    child_profit.setEditable(False)
                    child_consumables.setEditable(False)

                    # Store building ID and level for recipe calculation
                    child_name.setData(slot.building.type, Qt.ItemDataRole.UserRole)
                    child_level.setData(
                        slot.building.level, Qt.ItemDataRole.UserRole + 1
                    )

                    parent_item.appendRow(
                        [
                            child_name,
                            child_level,
                            child_recipe,
                            child_profit,
                            child_consumables,
                        ]
                    )

                    # Calculate best recipe for this building
                    self._update_best_recipe_for_building(
                        slot.building.type,
                        child_recipe,
                        child_profit,
                        child_consumables,
                        base.planet_id,
                    )

        def _update_best_recipe_for_building(
            self,
            building_type: int,
            recipe_item: QStandardItem,
            profit_item: QStandardItem,
            consumables_item: QStandardItem,
            planet_id: int,
        ):
            """Calculate and update the best recipe for a building."""
            try:
                game_data = GameDataManager.get()
                building = next(
                    (b for b in game_data.buildings if b.id == building_type), None
                )

                if not building:
                    return

                tech_level = self.settings.tech_level_filters.get(
                    building.specialization, float("inf")
                )

                # Calculate best recipe
                planet = GameDataManager.get_planet(planet_id)
                result = find_best_recipe_for_building(building.id, tech_level, planet)

                if result is None:
                    return

                recipe_name, profit, consumables_preferred, consumables_rejected = (
                    result
                )

                consumables_text = format_consumables(
                    consumables_preferred, consumables_rejected
                )

                # Update the items
                if recipe_item:
                    recipe_item.setText(recipe_name)
                if profit_item:
                    profit_item.setText(f"{profit:,.2f}")
                if consumables_item:
                    consumables_item.setText(consumables_text)
                    # Set tooltip
                    if consumables_rejected or consumables_preferred:
                        tooltip_parts = []
                        if consumables_preferred:
                            tooltip_parts.append(
                                f"Preferred: {', '.join(sorted(GameDataManager.get_item_name(c_id) for c_id in consumables_preferred))}"
                            )
                        if consumables_rejected:
                            tooltip_parts.append(
                                f"Rejected: {', '.join(sorted(GameDataManager.get_item_name(c_id) for c_id in consumables_rejected))}"
                            )
                        consumables_item.setToolTip("\n".join(tooltip_parts))
                    else:
                        consumables_item.setToolTip("")
                    # Reset foreground color to default (delegate handles coloring)
                    consumables_item.setForeground(QBrush())

            except Exception as e:
                _logger.error(f"Error updating best recipe: {e}")

        def recalculate_all_recipes(self):
            """Recalculate best recipes for all buildings in the tree."""
            for parent_row in range(self.rowCount()):
                parent_item = self.item(parent_row, 0)
                base = parent_item.data(Qt.ItemDataRole.UserRole)
                if parent_item:
                    for child_row in range(parent_item.rowCount()):
                        child_name = parent_item.child(child_row, 0)
                        child_level = parent_item.child(child_row, 1)
                        child_recipe = parent_item.child(child_row, 2)
                        child_profit = parent_item.child(child_row, 3)
                        child_consumables = parent_item.child(child_row, 4)

                        if child_name and child_level:
                            building_type = child_name.data(Qt.ItemDataRole.UserRole)
                            building_level = child_level.data(
                                Qt.ItemDataRole.UserRole + 1
                            )

                            if building_type and building_level:
                                self._update_best_recipe_for_building(
                                    building_type,
                                    child_recipe,
                                    child_profit,
                                    child_consumables,
                                    base.planet_id,
                                )

    class ConfigurationTreeView(QTreeView):
        def __init__(self, parent):
            super().__init__(parent)
            # Set resize mode for all columns to ResizeToContents initially
            # Will override column 0 after model is set
            self.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            # Make tree view read-only
            self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.setExpandsOnDoubleClick(True)
            self.setRootIsDecorated(True)
            self.setAlternatingRowColors(True)

        def setModel(self, model: QStandardItemModel) -> None:
            """Override setModel to connect signals for dynamic resizing."""
            super().setModel(model)
            if model:
                model.dataChanged.connect(self.adjust_tree_width)
                model.rowsInserted.connect(self.adjust_tree_width)
                # Expand all items by default
                self.expandAll()
                # Set first column to Interactive mode to allow minimum width
                self.header().setSectionResizeMode(
                    0, QHeaderView.ResizeMode.Interactive
                )
                # Keep other columns as ResizeToContents
                self.header().setSectionResizeMode(
                    1, QHeaderView.ResizeMode.ResizeToContents
                )
                self.header().setSectionResizeMode(
                    2, QHeaderView.ResizeMode.ResizeToContents
                )
                self.header().setSectionResizeMode(
                    3, QHeaderView.ResizeMode.ResizeToContents
                )
                self.header().setSectionResizeMode(
                    4, QHeaderView.ResizeMode.ResizeToContents
                )

        def adjust_tree_width(self):
            """Adjust the tree width to fit the contents."""
            self.resizeColumnToContents(0)
            self.resizeColumnToContents(1)
            self.resizeColumnToContents(2)
            self.resizeColumnToContents(3)
            self.resizeColumnToContents(4)

            # Calculate the total width of all columns
            total_tree_width = sum(
                self.header().sectionSize(i) for i in range(self.header().count())
            )

            # Add width for vertical scrollbar (if present)
            if self.verticalScrollBar().isVisible():
                total_tree_width += self.verticalScrollBar().width()

            # Notify the parent splitter to adjust sizes
            if self.parent() and isinstance(self.parent(), QSplitter):
                self.parent().setSizes(
                    [total_tree_width, self.parent().width() - total_tree_width]
                )

    class ConfigurationTableProxyModel(QSortFilterProxyModel):
        def __init__(self, parent: QObject | None):
            super().__init__(parent)
            # self.setDynamicSortFilter(True)
            # self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            # self.sort(1, Qt.SortOrder.DescendingOrder)

    def __init__(
        self,
        parent: QWidget | None = None,
        settings=None,
        recipe_worker=None,
        company_manager=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.recipe_worker = recipe_worker
        self.company_manager = company_manager

        # Main layout
        self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)

        # Configuration tree
        self.configuration_tree_model = ConfigurationWindow.ConfigurationTreeModel(
            self, settings
        )
        self.configuration_tree_view = ConfigurationWindow.ConfigurationTreeView(self)
        self.configuration_tree_view.setModel(self.configuration_tree_model)

        # Set custom delegate for Consumables column (mixed-color text)
        consumables_delegate = ConsumablesDelegate(self)
        self.configuration_tree_view.setItemDelegateForColumn(4, consumables_delegate)

        self.main_layout.addWidget(self.configuration_tree_view)

        # Connect to company manager's signals
        if self.company_manager:
            self.company_manager.company_loaded.connect(self.handle_company_loaded)
            self.company_manager.base_loaded.connect(self.handle_base_loaded)

        # Connect to recipe worker's exchange update signal
        if self.recipe_worker:
            self.recipe_worker.exchange_updated_signal.connect(
                self.handle_exchange_updated
            )

    @Slot(Company)
    def handle_company_loaded(self, company: Company) -> None:
        """Handle company data loaded from CompanyDataManager."""
        self.configuration_tree_model.populate_from_company(company)
        self.configuration_tree_view.expandAll()

    @Slot(Base)
    def handle_base_loaded(self, base: Base) -> None:
        """Handle individual base data loaded from CompanyDataManager."""
        self.configuration_tree_model.populate_base_buildings(base)
        self.configuration_tree_view.expandAll()

    @Slot()
    def handle_exchange_updated(self) -> None:
        """Recalculate all building recipes when exchange listings are updated."""
        self.configuration_tree_model.recalculate_all_recipes()
