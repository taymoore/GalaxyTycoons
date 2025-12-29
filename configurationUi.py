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
from PySide6.QtGui import QCloseEvent, QWheelEvent, QPixmap, QColor, QBrush, QPalette, QTextLayout, QTextCharFormat, QPainter, QTextOption
from PySide6.QtCore import QPointF
import pyqtgraph as pg
import matplotlib.colors as mcolors
import matplotlib.cm as cm

from utils import align_add, find_best_recipe_for_building, ConsumablesDelegate, format_consumables
from api.gameData import get_gamedata, get_item_name, get_building, get_worker
from api.models.gameData import Recipe, BuildingSpecialization, Building, WorkerType
from api.exchange import Exchange
from api.models.exchange import Listing
from recipeWorker import RecipeWorker

_logger = logging.getLogger(__name__)


class PlanetNameDelegate(QStyledItemDelegate):
    """Custom delegate that provides a dropdown/autocomplete for planet names (parent) and building names (child)."""
    
    def __init__(self, planet_names: List[str], building_names: List[str], parent=None):
        super().__init__(parent)
        self.planet_names = sorted(planet_names)
        self.building_names = sorted(building_names)
    
    def createEditor(self, parent, option, index):
        """Create a combobox editor with planet or building names."""
        # Only apply to column 0 (Name)
        if index.column() == 0:
            combo = QComboBox(parent)
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            
            # Check if this is a parent or child item
            model = index.model()
            if model:
                parent_index = index.parent()
                if parent_index.isValid():
                    # This is a child item - use building names
                    combo.addItems(self.building_names)
                else:
                    # This is a parent item - use planet names
                    combo.addItems(self.planet_names)
            
            combo.setMaxVisibleItems(15)
            # Enable autocomplete
            combo.completer().setCompletionMode(combo.completer().CompletionMode.PopupCompletion)
            combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            return combo
        return super().createEditor(parent, option, index)
    
    def setEditorData(self, editor, index):
        """Set the current value in the editor."""
        if isinstance(editor, QComboBox):
            value = index.data(Qt.ItemDataRole.DisplayRole)
            if value:
                idx = editor.findText(value)
                if idx >= 0:
                    editor.setCurrentIndex(idx)
                else:
                    editor.setEditText(value)
        else:
            super().setEditorData(editor, index)
    
    def setModelData(self, editor, model, index):
        """Store the edited value back to the model."""
        if isinstance(editor, QComboBox):
            value = editor.currentText()
            model.setData(index, value, Qt.ItemDataRole.EditRole)
        else:
            super().setModelData(editor, model, index)


class IntegerDelegate(QStyledItemDelegate):
    """Custom delegate that restricts input to integers greater than 0."""
    
    def createEditor(self, parent, option, index):
        """Create a line edit for integer input."""
        from PySide6.QtWidgets import QLineEdit
        from PySide6.QtGui import QIntValidator
        
        editor = QLineEdit(parent)
        validator = QIntValidator(1, 999999, editor)  # Min: 1, Max: 999999
        editor.setValidator(validator)
        return editor
    
    def setEditorData(self, editor, index):
        """Set the current value in the editor."""
        from PySide6.QtWidgets import QLineEdit
        if isinstance(editor, QLineEdit):
            value = index.data(Qt.ItemDataRole.DisplayRole)
            if value:
                editor.setText(str(value))
        else:
            super().setEditorData(editor, index)
    
    def setModelData(self, editor, model, index):
        """Store the edited value back to the model."""
        from PySide6.QtWidgets import QLineEdit
        if isinstance(editor, QLineEdit):
            text = editor.text()
            if text and text.isdigit() and int(text) > 0:
                model.setData(index, text, Qt.ItemDataRole.EditRole)
        else:
            super().setModelData(editor, model, index)


class ConfigurationWindow(QWidget):

    class ConfigurationTreeModel(QStandardItemModel):
        def __init__(self, parent: QObject, settings):
            super().__init__(parent)
            self.settings = settings
            self.setHorizontalHeaderLabels(["Name", "Level", "Best Recipe", "Profit / hr", "Consumables"])
            # Load data from settings or add initial empty row
            self._load_from_settings()
            
        def _add_empty_parent_row(self):
            """Add an empty row at parent level."""
            name_item = QStandardItem("")
            level_item = QStandardItem("")
            recipe_item = QStandardItem("")
            profit_item = QStandardItem("")
            consumables_item = QStandardItem("")
            name_item.setEditable(True)
            level_item.setEditable(False)
            recipe_item.setEditable(False)
            profit_item.setEditable(False)
            consumables_item.setEditable(False)
            self.appendRow([name_item, level_item, recipe_item, profit_item, consumables_item])
            
        def _add_empty_child_row(self, parent_item: QStandardItem):
            """Add an empty row at child level."""
            name_item = QStandardItem("")
            level_item = QStandardItem("")
            recipe_item = QStandardItem("")
            profit_item = QStandardItem("")
            consumables_item = QStandardItem("")
            name_item.setEditable(True)
            level_item.setEditable(True)
            recipe_item.setEditable(False)
            profit_item.setEditable(False)
            consumables_item.setEditable(False)
            parent_item.appendRow([name_item, level_item, recipe_item, profit_item, consumables_item])
        
        def _load_from_settings(self):
            """Load tree data from settings."""
            configurations = self.settings.configurations
            if configurations:
                for config in configurations:
                    name_item = QStandardItem(config.get("name", ""))
                    level_item = QStandardItem(config.get("level", ""))
                    recipe_item = QStandardItem(config.get("recipe", ""))
                    profit_item = QStandardItem(config.get("profit", ""))
                    consumables_item = QStandardItem(config.get("consumables", ""))
                    name_item.setEditable(True)
                    level_item.setEditable(False)
                    recipe_item.setEditable(False)
                    profit_item.setEditable(False)
                    consumables_item.setEditable(False)
                    self.appendRow([name_item, level_item, recipe_item, profit_item, consumables_item])
                    
                    # Add children
                    children = config.get("children", [])
                    for child in children:
                        child_name_item = QStandardItem(child.get("name", ""))
                        child_level_item = QStandardItem(child.get("level", ""))
                        child_recipe_item = QStandardItem(child.get("recipe", ""))
                        child_profit_item = QStandardItem(child.get("profit", ""))
                        child_consumables_item = QStandardItem(child.get("consumables", ""))
                        child_name_item.setEditable(True)
                        child_level_item.setEditable(True)
                        child_recipe_item.setEditable(False)
                        child_profit_item.setEditable(False)
                        child_consumables_item.setEditable(False)
                        name_item.appendRow([child_name_item, child_level_item, child_recipe_item, child_profit_item, child_consumables_item])
                    
                    # Recalculate profit for all loaded children
                    for child_row in range(name_item.rowCount()):
                        child_index = name_item.child(child_row, 0).index()
                        if child_index.isValid():
                            # Skip empty rows (no name)
                            child_name = name_item.child(child_row, 0).text()
                            if child_name:
                                self._update_best_recipe(child_index)
                    
                    # Add empty child row for display (non-editable)
                    self._add_empty_child_row(name_item)
            
            # Always add an empty parent row at the end
            self._add_empty_parent_row()
        
        def save_to_settings(self):
            """Save tree data to settings."""
            configurations = []
            for row in range(self.rowCount()):
                parent_name = self.item(row, 0)
                parent_level = self.item(row, 1)
                parent_recipe = self.item(row, 2)
                parent_profit = self.item(row, 3)
                parent_consumables = self.item(row, 4)
                
                # Skip empty parent rows
                if not parent_name.text():
                    continue
                
                config = {
                    "name": parent_name.text(),
                    "level": parent_level.text(),
                    "recipe": parent_recipe.text(),
                    "profit": parent_profit.text(),
                    "consumables": parent_consumables.text(),
                    "children": []
                }
                
                # Add children
                for child_row in range(parent_name.rowCount()):
                    child_name = parent_name.child(child_row, 0)
                    child_level = parent_name.child(child_row, 1)
                    child_recipe = parent_name.child(child_row, 2)
                    child_profit = parent_name.child(child_row, 3)
                    child_consumables = parent_name.child(child_row, 4)
                    
                    # Skip empty child rows
                    if child_name and child_name.text():
                        config["children"].append({
                            "name": child_name.text(),
                            "level": child_level.text() if child_level else "",
                            "recipe": child_recipe.text() if child_recipe else "",
                            "profit": child_profit.text() if child_profit else "",
                            "consumables": child_consumables.text() if child_consumables else ""
                        })
                
                configurations.append(config)
            
            self.settings.configurations = configurations
        
        def _update_best_recipe(self, child_row_index: QModelIndex):
            """Calculate and update the best recipe for a child (building) row."""
            try:
                # Get the building name and level
                name_item = self.itemFromIndex(child_row_index.siblingAtColumn(0))
                
                if not name_item:
                    return
                    
                building_name = name_item.text()
                
                if not building_name:
                    return
                
                # Find building ID by name
                game_data = get_gamedata()
                building_id = None
                for building in game_data.buildings:
                    if building.name == building_name:
                        break
                
                tech_level = self.settings.tech_level_filters.get(building.specialization, float('inf'))
                
                # Calculate best recipe
                result = find_best_recipe_for_building(building.id, tech_level)
                
                if result is None:
                    # Clear the fields if no recipe found
                    recipe_item = self.itemFromIndex(child_row_index.siblingAtColumn(2))
                    profit_item = self.itemFromIndex(child_row_index.siblingAtColumn(3))
                    consumables_item = self.itemFromIndex(child_row_index.siblingAtColumn(4))
                    if recipe_item:
                        recipe_item.setText("")
                    if profit_item:
                        profit_item.setText("")
                    if consumables_item:
                        consumables_item.setText("")
                    return
                
                recipe_name, profit, consumables_preferred, consumables_rejected = result
                
                consumables_text = format_consumables(consumables_preferred, consumables_rejected)
                
                # Update the items
                recipe_item = self.itemFromIndex(child_row_index.siblingAtColumn(2))
                profit_item = self.itemFromIndex(child_row_index.siblingAtColumn(3))
                consumables_item = self.itemFromIndex(child_row_index.siblingAtColumn(4))
                
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
                            tooltip_parts.append(f"Preferred: {', '.join(sorted(get_item_name(c_id) for c_id in consumables_preferred))}")
                        if consumables_rejected:
                            tooltip_parts.append(f"Rejected: {', '.join(sorted(get_item_name(c_id) for c_id in consumables_rejected))}")
                        consumables_item.setToolTip("\n".join(tooltip_parts))
                    else:
                        consumables_item.setToolTip("")
                    # Reset foreground color to default (delegate handles coloring)
                    consumables_item.setForeground(QBrush())
                    
            except Exception as e:
                _logger.error(f"Error updating best recipe: {e}")
            
        def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
            """Override setData to add new empty rows when user enters data."""
            if role == Qt.ItemDataRole.EditRole and value:
                result = super().setData(index, value, role)
                if result:
                    item = self.itemFromIndex(index)
                    parent_item = item.parent()
                    
                    if parent_item is None:
                        # This is a parent-level item
                        # Check if this is the last row
                        row = index.row()
                        if row == self.rowCount() - 1:
                            # Check if any cell in this row has data
                            has_data = False
                            for col in range(self.columnCount()):
                                cell_index = self.index(row, col)
                                if self.data(cell_index, Qt.ItemDataRole.DisplayRole):
                                    has_data = True
                                    break
                            if has_data:
                                self._add_empty_parent_row()
                                # Add an empty child row to the newly filled parent
                                name_item = self.item(row, 0)
                                self._add_empty_child_row(name_item)
                    else:
                        # This is a child-level item
                        child_row = item.row()
                        
                        # If name or level was updated, recalculate best recipe
                        if index.column() in (0, 1):  # Name or Level column
                            self._update_best_recipe(index)
                        
                        # Check if this is the last child row
                        parent_row_count = parent_item.rowCount()
                        if child_row == parent_row_count - 1:
                            # Check if any cell in this child row has data
                            has_data = False
                            for col in range(self.columnCount()):
                                sibling = parent_item.child(child_row, col)
                                if sibling and sibling.text():
                                    has_data = True
                                    break
                            if has_data:
                                self._add_empty_child_row(parent_item)
                    # Save to settings after any data change
                    self.save_to_settings()
                return result
            return super().setData(index, value, role)


    class ConfigurationTreeView(QTreeView):
        def __init__(self, parent):
            super().__init__(parent)
            # Set resize mode for all columns to ResizeToContents initially
            # Will override column 0 after model is set
            self.header().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
            self.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked | 
                QAbstractItemView.EditTrigger.EditKeyPressed
            )
            self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.setExpandsOnDoubleClick(False)
            self.setRootIsDecorated(True)
            self.setAlternatingRowColors(True)
            
            # Enable context menu
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.customContextMenuRequested.connect(self.show_context_menu)

        def show_context_menu(self, position):
            """Display context menu for row deletion."""
            index = self.indexAt(position)
            if not index.isValid():
                return
            
            # Don't show menu for empty rows
            model = self.model()
            if model:
                # Check if row has any data
                has_data = False
                for col in range(model.columnCount()):
                    sibling = index.siblingAtColumn(col)
                    if sibling.data(Qt.ItemDataRole.DisplayRole):
                        has_data = True
                        break
                
                if not has_data:
                    return
            
            menu = QMenu(self)
            delete_action = QAction("Delete", self)
            delete_action.triggered.connect(lambda: self.delete_row(index))
            menu.addAction(delete_action)
            menu.exec(self.viewport().mapToGlobal(position))
        
        def delete_row(self, index):
            """Delete the row at the given index."""
            if not index.isValid():
                return
            
            model = self.model()
            if not model:
                return
            
            # Get the parent index to determine if this is a parent or child row
            parent_index = index.parent()
            
            if parent_index.isValid():
                # This is a child row
                parent_item = model.itemFromIndex(parent_index)
                if parent_item:
                    parent_item.removeRow(index.row())
            else:
                # This is a parent row
                model.removeRow(index.row())
            
            # Save changes to settings
            if hasattr(model, 'save_to_settings'):
                model.save_to_settings()

        def setModel(self, model: QStandardItemModel) -> None:
            """Override setModel to connect signals for dynamic resizing."""
            super().setModel(model)
            if model:
                model.dataChanged.connect(self.adjust_tree_width)
                model.rowsInserted.connect(self.adjust_tree_width)
                # Expand all items by default
                self.expandAll()
                # Set first column to Interactive mode to allow minimum width
                self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
                # Keep other columns as ResizeToContents
                self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
                self.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
                self.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
                self.header().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        def adjust_tree_width(self):
            """Adjust the tree width to fit the contents."""
            self.resizeColumnToContents(0)
            self.resizeColumnToContents(1)
            self.resizeColumnToContents(2)
            self.resizeColumnToContents(3)
            self.resizeColumnToContents(4)

            # Calculate the total width of all columns
            total_tree_width = sum(
                self.header().sectionSize(i)
                for i in range(self.header().count())
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

    def __init__(self, parent: QWidget | None = None, settings=None, recipe_worker=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.recipe_worker = recipe_worker

        # Main layout
        self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)

        # Get all planet names and building names from gameData
        planet_names = self._get_planet_names()
        building_names = self._get_building_names()

        # Configuration tree
        self.configuration_tree_model = ConfigurationWindow.ConfigurationTreeModel(self, settings)
        self.configuration_tree_view = ConfigurationWindow.ConfigurationTreeView(self)
        self.configuration_tree_view.setModel(self.configuration_tree_model)
        
        # Set custom delegate for Name column
        planet_delegate = PlanetNameDelegate(planet_names, building_names, self)
        self.configuration_tree_view.setItemDelegateForColumn(0, planet_delegate)
        
        # Set custom delegate for Level column (integers > 0)
        level_delegate = IntegerDelegate(self)
        self.configuration_tree_view.setItemDelegateForColumn(1, level_delegate)
        
        # Set custom delegate for Consumables column (mixed-color text)
        consumables_delegate = ConsumablesDelegate(self)
        self.configuration_tree_view.setItemDelegateForColumn(4, consumables_delegate)
        
        # Calculate and set minimum width for Name column based on contents
        font_metrics = self.fontMetrics()
        all_names = planet_names + building_names
        max_name_width = 0
        if all_names:
            max_name_width = max(font_metrics.horizontalAdvance(name) for name in all_names)
        
        # Add padding for tree decoration, indentation, and combobox arrow (100px margin)
        required_width = max_name_width + 100
        self.configuration_tree_view.header().setMinimumSectionSize(required_width)
        self.configuration_tree_view.setColumnWidth(0, required_width)
        
        self.main_layout.addWidget(self.configuration_tree_view)
        
        # Connect to recipe worker's exchange update signal
        if self.recipe_worker:
            self.recipe_worker.exchange_updated_signal.connect(self.handle_exchange_updated)
    
    def _get_planet_names(self) -> List[str]:
        """Extract all planet names from gameData."""
        planet_names = []
        game_data = get_gamedata()
        for system in game_data.systems:
            if system.planets:
                for planet in system.planets:
                    planet_names.append(planet.name)
        return planet_names
    
    def _get_building_names(self) -> List[str]:
        """Extract all building names from gameData."""
        game_data = get_gamedata()
        return [building.name for building in game_data.buildings]
    
    @Slot()
    def handle_exchange_updated(self) -> None:
        """Recalculate all child rows when exchange listings are updated."""
        for parent_row in range(self.configuration_tree_model.rowCount()):
            parent_index = self.configuration_tree_model.index(parent_row, 0)
            parent_item = self.configuration_tree_model.itemFromIndex(parent_index)
            
            if parent_item:
                # Iterate through all child rows
                for child_row in range(parent_item.rowCount()):
                    child_index = parent_item.child(child_row, 0).index()
                    self.configuration_tree_model._update_best_recipe(child_index)
