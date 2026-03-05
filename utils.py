# TODO: review the profit calculation as I don't think it takes into account depreciation of the buildings, especially if they're over level 1
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QModelIndex, QPointF, Slot
from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from PySide6.QtGui import QPainter, QTextLayout, QTextCharFormat, QTextOption
import pandas as pd
from typing import Optional, Tuple, Union, List
import logging
import itertools
import math
import hashlib
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import numpy as np
import pyqtgraph as pg

from api.gameData import GameDataManager
from api.exchange import Exchange
from api.models.gameData import (
    Building,
    MaterialAmount,
    Planet,
    Recipe,
    RecipeType,
    Specialization,
    WorkerType,
)

# Global flag to control consumable calculation behavior
use_all_consumables = False
use_average_price = False

_logger = logging.getLogger(__name__)


class ConsumablesDelegate(QStyledItemDelegate):
    """Custom delegate that renders consumables with rejected items (in parentheses) in red."""

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        """Paint the cell with mixed-color text rendering."""
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return super().paint(painter, option, index)

        # Let Qt draw the background (selection, hover, alternating rows, etc.)
        self.initStyleOption(option, index)
        # Clear the text so drawControl only draws background, not text
        option.text = ""
        style = option.widget.style() if option.widget else QStyle()
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget
        )

        painter.save()

        # Enable text anti-aliasing for smooth rendering
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # Use the option's font to match rendering
        font = option.font

        # Parse text to find parenthesized sections
        layout = QTextLayout(text, font)
        layout.setTextOption(
            QTextOption(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        )

        # Create formats for red text (parenthesized sections)
        format_ranges = []
        in_parens = False
        paren_start = -1

        for i, char in enumerate(text):
            if char == "(":
                in_parens = True
                paren_start = i
            elif char == ")" and in_parens:
                # Add format for the parenthesized section (including parentheses)
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(255, 0, 0))  # Red
                layout_format = QTextLayout.FormatRange()
                layout_format.start = paren_start
                layout_format.length = i - paren_start + 1
                layout_format.format = fmt
                format_ranges.append(layout_format)
                in_parens = False

        # Apply formats before layout
        layout.setFormats(format_ranges)

        # Create the layout
        layout.beginLayout()
        line = layout.createLine()
        layout.endLayout()

        # Position and draw the layout
        painter.translate(option.rect.left() + 2, option.rect.top())
        layout.draw(
            painter,
            QPointF(0, (option.rect.height() - layout.boundingRect().height()) / 2),
        )

        painter.restore()


class GradientColorDelegate(QStyledItemDelegate):
    """Custom delegate that renders cells with gradient background color based on value.

    Higher values are colored green, lower values are colored red.
    """

    def __init__(self, parent=None, value_transform=None):
        super().__init__(parent)
        self.min_value = 0.0
        self.max_value = 1.0
        self.value_transform = value_transform  # Optional function to transform values before color calculation

    def set_value_range(self, min_value: float, max_value: float) -> None:
        """Set the min and max values for the gradient range."""
        self.min_value = min_value
        self.max_value = max_value if max_value > min_value else min_value + 1.0

    def _get_gradient_color(self, value: float) -> QColor:
        """Get a color based on the value using a red-to-green gradient.

        Args:
            value: The numeric value to convert to a color.

        Returns:
            A QColor interpolated between red (low values) and green (high values).
        """
        # Normalize value to 0-1 range
        if self.max_value == self.min_value:
            normalized = 0.5
        else:
            normalized = (value - self.min_value) / (self.max_value - self.min_value)

        # Clamp to 0-1 range
        normalized = max(0.0, min(1.0, normalized))

        # Interpolate from red (0) to green (1)
        # Red: (255, 0, 0)
        # Green: (0, 255, 0)
        red = int(255 * (1 - normalized))
        green = int(255 * normalized)

        return QColor(red, green, 0)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        """Paint the cell with gradient background color based on value."""
        # Get the numeric value from UserRole
        value = index.data(Qt.ItemDataRole.UserRole)

        # Handle invalid values (None, -1, inf, -inf)
        if value is None or value == -1:
            return super().paint(painter, option, index)

        try:
            numeric_value = float(value)
            # Skip inf and -inf values
            if not math.isfinite(numeric_value):
                return super().paint(painter, option, index)
        except (TypeError, ValueError):
            return super().paint(painter, option, index)

        # Apply value transformation if provided
        if self.value_transform is not None:
            numeric_value = self.value_transform(numeric_value)

        # Get the gradient color for this value
        color = self._get_gradient_color(numeric_value)

        # Initialize style option
        self.initStyleOption(option, index)

        # Draw the background with the gradient color
        painter.save()
        painter.fillRect(option.rect, color)
        painter.restore()

        # Draw the text using the default delegate
        super().paint(painter, option, index)


class BuildingColorDelegate(QStyledItemDelegate):
    """Custom delegate that renders cells with unique background colors for each building type using a colormap."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.building_colors = {}  # Maps building name to QColor
        self.colormap = cm.get_cmap(
            "tab20b"
        )  # Use tab20b colormap with 20 distinct colors

    def _get_building_color(self, building_name: str) -> QColor:
        """Get or assign a color for a building."""
        if building_name not in self.building_colors:
            # Use hash-based approach for consistent colors
            hash_digest = hashlib.md5(building_name.encode()).hexdigest()
            hash_value = int(hash_digest[:8], 16)
            color_index = hash_value % 2000

            # Get normalized value between 0 and 1
            normalized_value = color_index / 2000.0
            # Get color from colormap
            rgba = self.colormap(normalized_value)
            # Convert to QColor (rgba is tuple of 0-1 values)
            self.building_colors[building_name] = QColor(
                int(rgba[0] * 255),
                int(rgba[1] * 255),
                int(rgba[2] * 255),
                int(rgba[3] * 255),
            )
        return self.building_colors[building_name]

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        """Paint the cell with background color based on building name."""
        # Get the text which contains the building name
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return super().paint(painter, option, index)

        building_name = str(text)
        color = self._get_building_color(building_name)

        # Initialize style option
        self.initStyleOption(option, index)

        # Draw the background with the building color
        painter.save()
        painter.fillRect(option.rect, color)
        painter.restore()

        # Draw the text using the default delegate
        super().paint(painter, option, index)


class StatusColorDelegate(QStyledItemDelegate):
    """Custom delegate that renders cells with background colors based on status text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Define status colors
        self.status_colors = {
            "IN PROGRESS": QColor(0, 200, 0),  # Green
            "STANDBY": QColor(255, 165, 0),  # Orange
            "NO MATERIALS": QColor(255, 0, 0),  # Red
        }

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        """Paint the cell with background color based on status text."""
        # Get the text which contains the status
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return super().paint(painter, option, index)

        # Get status text (normalize to uppercase for comparison)
        status_text = str(text).upper()

        # Find matching color
        color = None
        for status_key, status_color in self.status_colors.items():
            if status_key in status_text:
                color = status_color
                break

        if color is None:
            return super().paint(painter, option, index)

        # Initialize style option
        self.initStyleOption(option, index)

        # Draw the background with the status color
        painter.save()
        painter.fillRect(option.rect, color)
        painter.restore()

        # Draw the text using the default delegate
        super().paint(painter, option, index)


class SpecializationColorDelegate(QStyledItemDelegate):
    """Custom delegate that renders cells with unique background colors for each specialization using a colormap."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.specialization_colors = {}  # Maps specialization name to QColor
        self.colormap = cm.get_cmap(
            "tab20"
        )  # Use tab20 colormap with 20 distinct colors

    def set_specialization_color(self, specialization_name: str, color: QColor) -> None:
        """Set a specific color for a specialization."""
        self.specialization_colors[specialization_name] = color

    def _get_specialization_color(self, specialization_name: str) -> QColor:
        """Get or assign a color for a specialization using the BuildingSpecialization enum value."""
        if specialization_name not in self.specialization_colors:
            # Try to get the BuildingSpecialization enum from the name
            try:
                from api.models.gameData import Specialization

                specialization_enum = Specialization[
                    specialization_name.upper().replace(" ", "_")
                ]
                color_index = int(specialization_enum) % 20
            except (KeyError, ValueError):
                # Fallback to hash-based approach if enum lookup fails
                import hashlib

                _logger.warning(
                    f"Unknown specialization '{specialization_name}', using hash-based color assignment."
                )
                hash_digest = hashlib.md5(specialization_name.encode()).hexdigest()
                hash_value = int(hash_digest[:8], 16)
                color_index = hash_value % 20

            # Get normalized value between 0 and 1
            normalized_value = color_index / 20.0
            # Get color from colormap
            rgba = self.colormap(normalized_value)
            # Convert to QColor (rgba is tuple of 0-1 values)
            self.specialization_colors[specialization_name] = QColor(
                int(rgba[0] * 255),
                int(rgba[1] * 255),
                int(rgba[2] * 255),
                int(rgba[3] * 255),
            )
        return self.specialization_colors[specialization_name]

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        """Paint the cell with background color based on specialization."""
        # Get the text which contains "SPECIALIZATION LEVEL"
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return super().paint(painter, option, index)

        # Extract specialization name (everything before the space and level number)
        parts = str(text).rsplit(
            " ", 1
        )  # Split on the last space to separate name from level
        if len(parts) == 2:
            specialization_name = parts[0]
            color = self._get_specialization_color(specialization_name)
        else:
            return super().paint(painter, option, index)

        # Initialize style option
        self.initStyleOption(option, index)

        # Draw the background with the specialization color
        painter.save()
        painter.fillRect(option.rect, color)
        painter.restore()

        # Draw the text using the default delegate
        super().paint(painter, option, index)


def align_and_interpolate(
    *args: Union[pd.Series, pd.DataFrame],
) -> Tuple[Union[pd.Series, pd.DataFrame], ...]:
    """
    Does not work with bool!
    """

    def __align_and_interpolate(
        a: Union[pd.Series, pd.DataFrame], b: Union[pd.Series, pd.DataFrame]
    ) -> Tuple[Union[pd.Series, pd.DataFrame], ...]:
        a, b = a.align(b, axis=0)
        return (
            a.interpolate(method="index", limit_area="inside"),
            b.interpolate(method="index", limit_area="inside"),
        )

    arg_list = [arg.copy() for arg in args]
    for a_index, a_series in enumerate(arg_list):
        for b_index, b_series in enumerate(arg_list[a_index + 1 :]):
            (
                arg_list[a_index],
                arg_list[b_index + a_index + 1],
            ) = __align_and_interpolate(a_series, b_series)
    return tuple(arg_list)


def align_add(
    a: Union[pd.Series, pd.DataFrame],
    b: Union[pd.Series, pd.DataFrame],
    by_column_index: bool = False,
) -> Union[pd.Series, pd.DataFrame]:
    """
    Return a + b filling in missing indexes by interpolation
    by_column_index allows subtracting DataFrame columns by column position instead of name
    """
    if by_column_index:
        b = b.copy()
        b.columns = a.columns
    aligned_data = [
        data.interpolate(method="index", limit_area="inside")
        for data in a.align(b, axis=0)
    ]
    return aligned_data[0].add(aligned_data[1], axis="index")


def calculate_profit_and_consumables(
    recipe: Recipe, tech_level: int = 0, abundance: float = 1.0
) -> None | tuple[float, tuple[int, ...], tuple[int, ...]]:
    """
    Calculate the profit per hour and the preferred consumable combination for a given recipe.

    Args:
        recipe: The recipe for which to calculate the profit and consumables.

    Returns:
        None | tuple[float, tuple[int, ...], tuple[int, ...]]:
            Returns a tuple[profit per hour, tuple[preferred consumable IDs], tuple[rejected consumable IDs]
            if the calculation is successful. Returns None if the calculation cannot be performed.
    """
    try:
        building = GameDataManager.get_building(recipe.producedIn)
        listing = Exchange.get_listing(recipe.output.id)
        listing_price = (
            listing.average_price if use_average_price else listing.current_price
        )
        base_profit_per_hour = listing_price * recipe.output.am / 100

        for material_amount in recipe.inputs:
            material_price = (
                Exchange.get_listing(material_amount.id).average_price / 100
                if use_average_price
                else Exchange.get_listing(material_amount.id).current_price / 100
            )
            if material_price < 1:
                return None
            base_profit_per_hour -= material_price * material_amount.am

        base_profit_per_hour = (
            base_profit_per_hour
            * (1 + 0.05 * tech_level)
            * abundance
            / (recipe.timeMinutes / 60)
        )

        # Calculate worker cost
        optimal_profit_per_hour = float("-inf")
        # Get list of consumables
        consumable_id_set: set[int] = set()
        for worker_type, worker_count in enumerate(
            building.workersNeeded or [], start=1
        ):
            if worker_count == 0:
                continue
            worker = GameDataManager.get_worker(worker_type)
            consumable_id_set.update(
                [consumable.matId for consumable in worker.consumables]
            )
        if len(consumable_id_set) == 0:
            _logger.debug(
                f"No workers needed for building {building.name} ({building.id})."
            )

        # If USE_ALL_CONSUMABLES is True, use all consumables instead of finding optimal combination
        if use_all_consumables and consumable_id_set:
            consumable_preferred_combination = tuple(consumable_id_set)
            consumable_rejected_combination = ()

            # Calculate worker cost with all consumables
            worker_cost_per_hour = 0.0
            worker_count_satisfaction_list = []

            for worker_type, worker_count in enumerate(
                building.workersNeeded or [], start=1
            ):
                if worker_count == 0:
                    continue
                worker = GameDataManager.get_worker(worker_type)
                worker_type_satisfaction = 1.0  # All consumables = 100% satisfaction

                for consumable in worker.consumables:
                    consumable_listing = Exchange.get_listing(consumable.matId)
                    consumable_price = (
                        consumable_listing.average_price
                        if use_average_price
                        else consumable_listing.current_price
                    )
                    if consumable_price < 1:
                        _logger.warning(
                            f"Invalid price for consumable {consumable.matId}"
                        )
                        continue
                    worker_cost_per_hour += (
                        consumable_price  # in cents
                        * consumable.amount  # daily consumption per 1000 workers
                        * worker_count  # number of workers
                        / 24  # hours per day
                        / 1000  # per 1000 workers
                        / 100  # convert cents to dollars
                    )

                worker_count_satisfaction_list.append(
                    (worker_count, worker_type_satisfaction)
                )

            total_worker_count = sum(
                worker_count for worker_count, _ in worker_count_satisfaction_list
            )
            total_worker_satisfaction = (
                sum(
                    worker_count * worker_satisfaction / total_worker_count
                    for worker_count, worker_satisfaction in worker_count_satisfaction_list
                )
                if total_worker_count > 0
                else 0.1
            )

            optimal_profit_per_hour = (
                base_profit_per_hour * total_worker_satisfaction - worker_cost_per_hour
            )
        else:
            # Try all combinations of consumables to find lowest cost
            consumable_preferred_combination = None
            for combination_size in range(len(consumable_id_set or []) + 1):
                for consumable_list in itertools.combinations(
                    consumable_id_set or [], combination_size
                ):
                    worker_cost_per_hour = 0.0
                    worker_count_satisfaction_list: List[tuple[float, float]] = (
                        []
                    )  # worker_count, worker_satisfaction
                    for worker_type, worker_count in enumerate(
                        building.workersNeeded or [], start=1
                    ):
                        if worker_count == 0:
                            continue
                        consumable_optional_missed_count = 0
                        consumable_essential_missed_count = 0
                        combination_valid = True
                        worker = GameDataManager.get_worker(worker_type)
                        for consumable in worker.consumables:
                            # If consumable is in this combination, calculate its cost
                            if consumable.matId in consumable_list:
                                consumable_listing = Exchange.get_listing(
                                    consumable.matId
                                )
                                consumable_price = (
                                    consumable_listing.average_price
                                    if use_average_price
                                    else consumable_listing.current_price
                                )
                                if consumable_price < 1:
                                    combination_valid = False
                                    break
                                worker_cost_per_hour += (
                                    consumable_price  # in cents
                                    * consumable.amount  # daily consumption per 1000 workers
                                    * worker_count  # number of workers
                                    / 24  # hours per day
                                    / 1000  # per 1000 workers
                                    / 100  # convert cents to dollars
                                )
                            # If consumable is not in this combination, apply satisfaction penalty
                            else:
                                if consumable.essential:
                                    consumable_essential_missed_count += 1
                                else:
                                    consumable_optional_missed_count += 1
                        if not combination_valid:
                            break
                        worker_type_satisfaction = 1.0
                        worker_type_satisfaction -= (
                            0.1 * consumable_optional_missed_count
                        )
                        worker_type_satisfaction *= (
                            0.6**consumable_essential_missed_count
                        )
                        worker_type_satisfaction = max(worker_type_satisfaction, 0.1)
                        worker_count_satisfaction_list.append(
                            (worker_count, worker_type_satisfaction)
                        )
                    total_worker_count = sum(
                        worker_count
                        for worker_count, _ in worker_count_satisfaction_list
                    )
                    total_worker_satisfaction = (
                        sum(
                            worker_count * worker_satisfaction / total_worker_count
                            for worker_count, worker_satisfaction in worker_count_satisfaction_list
                        )
                        if total_worker_count > 0
                        else 0.1
                    )
                    assert 0.0 < total_worker_satisfaction <= 1.0
                    configuration_profit_per_hour = (
                        base_profit_per_hour * total_worker_satisfaction
                        - worker_cost_per_hour
                    )
                    if configuration_profit_per_hour > optimal_profit_per_hour:
                        optimal_profit_per_hour = configuration_profit_per_hour
                        consumable_preferred_combination = consumable_list

            if optimal_profit_per_hour == float("-inf"):
                _logger.debug(
                    f"Could not calculate profit for recipe {GameDataManager.get_item_name(recipe.output.id)} ({recipe.id}). Base profit/hr: {base_profit_per_hour:,.2f}."
                )
                return None

            if consumable_preferred_combination is None:
                _logger.error(
                    f"No valid consumable combination found for recipe {GameDataManager.get_item_name(recipe.output.id)} ({recipe.id}). This should not happen."
                )
                return None
            consumable_rejected_combination = tuple(
                consumable_id_set.difference(consumable_preferred_combination)
            )

        # Find building depreciation cost per hour
        building_depreciation_per_hour = 0.0
        for material in building.constructionMaterials:
            material_listing = Exchange.get_listing(material.id)
            material_price = (
                material_listing.average_price
                if use_average_price
                else material_listing.current_price
            )
            if material_price < 1:
                _logger.error(
                    f"Cannot calculate building depreciation for {building.name} ({building.id}) due to missing material price."
                )
                return None
            building_depreciation_per_hour += (
                material_price  # in cents
                * material.am  # amount of material
                / (40 * 24)  # 40 days depreciation period
                / 100  # convert cents to dollars
            )
        for worker_type, worker_count in enumerate(
            building.workersNeeded or [], start=1
        ):
            if worker_count == 0:
                continue
            worker_housing_building = GameDataManager.get_worker_housing(
                WorkerType(worker_type)
            )
            for material in worker_housing_building.constructionMaterials:
                material_listing = Exchange.get_listing(material.id)
                material_price = (
                    material_listing.average_price
                    if use_average_price
                    else material_listing.current_price
                )
                if material_price < 1:
                    _logger.error(
                        f"Cannot calculate worker housing depreciation for {building.name} ({building.id}) due to missing material price."
                    )
                    return None
                building_depreciation_per_hour += (
                    material_price  # in cents
                    * material.am  # amount of material
                    * (
                        worker_count
                        / worker_housing_building.workersHousing[worker_type - 1]
                    )  # scale by worker count
                    / (40 * 24)  # 40 days depreciation period
                    / 100  # convert cents to dollars
                )

        return (
            optimal_profit_per_hour - building_depreciation_per_hour,
            consumable_preferred_combination,
            consumable_rejected_combination,
        )
    except Exception as e:
        _logger.error(
            f"Error calculating profit for recipe {GameDataManager.get_item_name(recipe.output.id)} ({recipe.id}): {e}"
        )
        return None


def format_consumables(preferred: tuple[int, ...], rejected: tuple[int, ...]) -> str:
    """
    Format consumables text with preferred items first and rejected items in parentheses.

    Args:
        preferred: Tuple of preferred consumable IDs
        rejected: Tuple of rejected consumable IDs

    Returns:
        str: Formatted consumables text (e.g., "Item1, Item2 (Item3, Item4)")
    """
    parts = []

    if preferred:
        preferred_names = sorted(
            GameDataManager.get_item_name(c_id) for c_id in preferred
        )
        parts.append(", ".join(preferred_names))

    if rejected:
        rejected_names = sorted(
            GameDataManager.get_item_name(c_id) for c_id in rejected
        )
        parts.append(f"({', '.join(rejected_names)})")

    return " ".join(parts) if parts else "None"


def find_best_recipe_for_technology(
    specialization: Specialization, max_tech_level: int
) -> None | Tuple[str, float, tuple[int, ...], tuple[int, ...]]:
    try:
        best_recipe = None
        best_profit = float("-inf")
        best_preferred = None
        best_rejected = None

        for recipe in GameDataManager.get().recipes:
            if recipe.reqTech > max_tech_level:
                continue
            building = GameDataManager.get_building(recipe.producedIn)
            if building.specialization != specialization:
                continue
            result = calculate_profit_and_consumables(recipe, tech_level=max_tech_level)
            if result is None:
                continue
            profit, preferred, rejected = result
            if profit > best_profit:
                best_profit = profit
                best_recipe = recipe
                best_preferred = preferred
                best_rejected = rejected
        if best_recipe is None:
            return None
        return (
            GameDataManager.get_item_name(best_recipe.output.id),
            best_profit,
            best_preferred,
            best_rejected,
        )
    except Exception as e:
        _logger.error(
            f"Error finding best recipe for specialization {specialization} at tech level {max_tech_level}: {e}"
        )
        return None


def find_best_recipe_for_building(
    building_id: int, tech_level: int = None, planet: Optional[Planet] = None
) -> None | Tuple[Recipe, float, tuple[int, ...], tuple[int, ...]]:
    """
    Find the best recipe (highest profit/hr) for a given building and tech level.

    Args:
        building_id: ID of the building
        tech_level: Technology level filter
        planet: Optional planet for resource abundance consideration

    Returns:
        None | tuple[Recipe, float, tuple[int, ...], tuple[int, ...]]: Recipe, profit/hr, preferred consumables, and rejected consumables
    """
    try:
        game_data = GameDataManager.get()

        best_recipe = None
        best_profit = float("-inf")
        best_preferred = None
        best_rejected = None

        # Find all recipes for this building
        for recipe in game_data.recipes:
            if recipe.producedIn != building_id:
                continue
            if tech_level is not None and recipe.reqTech > tech_level:
                continue
            if recipe.type == RecipeType.PRODUCTION:
                result = calculate_profit_and_consumables(
                    recipe, tech_level=0 if tech_level is None else tech_level
                )

                if result is None:
                    continue

                profit, preferred, rejected = result
                if profit > best_profit:
                    best_profit = profit
                    best_recipe = recipe
                    best_preferred = preferred
                    best_rejected = rejected
            elif recipe.type == RecipeType.EXTRACTION and planet is not None:
                # Check if planet has the resource
                planet_material = next(
                    (mat for mat in planet.mats if mat.id == recipe.output.id), None
                )
                if planet_material is None:
                    continue
                result = calculate_profit_and_consumables(
                    recipe,
                    tech_level=0 if tech_level is None else tech_level,
                    abundance=planet_material.ab / 100.0,
                )
                if result is None:
                    continue

                profit, preferred, rejected = result
                if profit > best_profit:
                    best_profit = profit
                    best_recipe = recipe
                    best_preferred = preferred
                    best_rejected = rejected

        if best_recipe is None:
            return None

        return (
            best_recipe,
            best_profit,
            best_preferred,
            best_rejected,
        )
    except Exception as e:
        _logger.error(f"Error finding best recipe for building {building_id}: {e}")
        return None


def calculate_research_cost(
    specialization: Specialization, tech_levels: dict[Specialization, int]
) -> Tuple[int, int, int, int]:
    """
    Calculate the research cost for a given specialization and tech levels.
    Args:
        specialization: The specialization for which to calculate the research cost.
        tech_levels: A dictionary mapping each specialization to its current tech level.
    Returns:
        A tuple containing the research costs for T1, T2, T3, and T4 technologies.
        T1 item is Research Data (ID 64)
        T2 item is Advanced Research Data (ID 65)
        T3 item is Apex Research Data (ID 127)
        T4 item is Quantum Research Data (ID 164)
    """
    # assert 1 <= tech_levels[specialization] <= 25
    value_multiplier = ((tech_levels[specialization] / 4) + 1) ** 3
    total_technologies = sum(tech_level for tech_level in tech_levels.values())
    tech_penalty = (total_technologies + 1) ** 1.015 - total_technologies
    tech_flat = total_technologies * 3_000
    total_value = (value_multiplier * 8_000) * tech_penalty + tech_flat

    tier_part = (tech_levels[specialization] + 1) / 5.0
    tier_percentages = [0.0] * 4  # T1, T2, T3, T4
    if tier_part <= 1.0:
        # levels 1-5: Pure T1
        tier_percentages[0] = 1.0
    elif tier_part <= 2.0:
        # levels 6-10: T1 to T2
        progress = tier_part - 1.0  # 0.0 at level 6, 1.0 at level 10
        tier_percentages[0] = 0.8 - (0.6 * progress)  # 0.8 at level 6, 0.2 at level 10
        tier_percentages[1] = 0.2 + (0.6 * progress)  # 0.2 at level 6, 0.8 at level 10
    elif tier_part <= 3.0:
        # levels 11-15: T2 to T3
        progress = tier_part - 2.0  # 0.0 at level 11, 1.0 at level 15
        tier_percentages[1] = 0.8 - (0.6 * progress)  # 0.8 at level 11, 0.2 at level 15
        tier_percentages[2] = 0.2 + (0.6 * progress)  # 0.2 at level 11, 0.8 at level 15
    elif tier_part <= 4.0:
        # levels 16-20: T3 to T4
        progress = tier_part - 3.0  # 0.0 at level 16, 1.0 at level 20
        tier_percentages[2] = 0.8 - (0.6 * progress)  # 0.8 at level 16, 0.2 at level 20
        tier_percentages[3] = 0.2 + (0.6 * progress)  # 0.2 at level 16, 0.8 at level 20
    else:
        # levels 21+: Pure T4
        tier_percentages[3] = 1.0

    tier_amounts = [None] * 4  # T1, T2, T3, T4
    for tier_index, tier_divisor in zip(range(4), (1_100, 3_000, 6_000, 10_000)):
        tier_amounts[tier_index] = math.ceil(
            (total_value * tier_percentages[tier_index]) / tier_divisor
        )
    return tier_amounts


class PriceGraph(pg.PlotWidget):
    """Reusable price graph widget for displaying recipe profit and quantity data over time."""

    class FmtAxesItem(pg.AxisItem):
        def tickStrings(self, values, scale, spacing):
            return [f"{v:,.0f}" for v in values]

    def __init__(self, parent=None, background="default", plotItem=None, **kargs):
        kargs["axisItems"] = {
            "bottom": pg.DateAxisItem(),
            "left": PriceGraph.FmtAxesItem(orientation="left"),
        }
        super().__init__(parent, background, plotItem, **kargs)

        self.p1 = self.plotItem
        assert isinstance(self.p1, pg.PlotItem)
        self.p1.getAxis("left").setLabel("Profit/hr", color="#00ff00")
        # Prevent negative X range across all linked viewboxes
        self.p1.vb.setLimits(xMin=0)

        # Create additional y-axes for quantity data
        self.p2 = pg.ViewBox()
        self.p3 = pg.ViewBox()
        self.p1.scene().addItem(self.p2)
        self.p1.scene().addItem(self.p3)

        # Ensure secondary viewboxes inherit the non-negative X constraint
        self.p2.setLimits(xMin=0)
        self.p3.setLimits(xMin=0)

        # Link ViewBoxes to the main plot's viewbox geometry
        self.p2.setGeometry(self.p1.vb.sceneBoundingRect())
        self.p3.setGeometry(self.p1.vb.sceneBoundingRect())

        axis2 = pg.AxisItem("right")
        axis3 = pg.AxisItem("right")
        self.p1.layout.addItem(axis2, 2, 3)
        self.p1.layout.addItem(axis3, 2, 4)
        axis2.linkToView(self.p2)
        axis3.linkToView(self.p3)
        axis2.setLabel("Total Qty Available", color="#0088ff")
        axis3.setLabel("Qty Sold Daily", color="#ff8800")
        self.p2.setXLink(self.p1)
        self.p3.setXLink(self.p1)

        # Update geometry when viewbox changes
        self.p1.vb.sigStateChanged.connect(self._update_viewbox_geometry)

        # Label to display the nearest point's value
        self.label = pg.LabelItem(justify="right", color="#00ff00")
        scene = self.scene()
        assert isinstance(scene, pg.GraphicsScene)
        scene.addItem(self.label)

        # Enable mouse tracking
        scene.sigMouseMoved.connect(self.on_mouse_moved)

        # Store plotted data points for nearest-point calculation
        self.data_points = []
        self.listing_price = []

    def _update_viewbox_geometry(self):
        """Update p2 and p3 geometry when p1 viewbox changes."""
        self.p2.setGeometry(self.p1.vb.sceneBoundingRect())
        self.p3.setGeometry(self.p1.vb.sceneBoundingRect())

    def wheelEvent(self, ev, axis=None):
        super().wheelEvent(ev)
        vb = self.p1.vb
        if axis in (0, 1):
            mask = [False, False]
            mask[axis] = vb.state["mouseEnabled"][axis]
        else:
            mask = vb.state["mouseEnabled"][:]
        s = 1.02 ** (
            (ev.angleDelta().y() - ev.angleDelta().x()) * vb.state["wheelScaleFactor"]
        )  # actual scaling factor
        s = [(None if m is False else s) for m in mask]
        center = pg.Point(
            pg.functions.invertQTransform(vb.childGroup.transform()).map(ev.position())
        )

        vb._resetTarget()
        vb.scaleBy(s, center)
        ev.accept()
        vb.sigRangeChangedManually.emit(mask)

    @Slot(Recipe)
    def plot_recipe(self, recipe: Recipe) -> None:
        try:
            self.p1.clear()
            self.p2.clear()
            self.p3.clear()
            self.p1.vb.enableAutoRange()
            # p2/p3 use manual Y ranges derived from their own data; X is linked to p1

            listing = Exchange.get_listing(recipe.output.id)
            if not listing or listing.dataframe.empty:
                _logger.warning(
                    f"No listing data available for recipe output {recipe.output.id}"
                )
                return

            df = listing.dataframe.copy()
            df.sort_index(inplace=True)

            avg_price_df = df[["average_price"]].dropna()
            if avg_price_df.empty:
                _logger.warning(f"No average price data for recipe {recipe.id}")
                return

            output_average_price_index = (
                pd.to_datetime(avg_price_df.index).astype("int64") // 10**9
            )
            output_average_price = (
                pd.to_numeric(avg_price_df["average_price"], errors="coerce")
                * recipe.output.am
                / (100 * recipe.timeMinutes / 60)
            )

            curr_price_df = df[["current_price"]].dropna()
            output_current_price_index = (
                pd.to_datetime(curr_price_df.index).astype("int64") // 10**9
            )
            output_current_price = (
                pd.to_numeric(curr_price_df["current_price"], errors="coerce")
                * recipe.output.am
                / (100 * recipe.timeMinutes / 60)
            )

            self.data_points = list(
                zip(output_average_price_index, output_average_price)
            )
            self.listing_price = avg_price_df["average_price"].to_numpy() / 100

            self.p1.plot(
                x=np.asarray(output_average_price_index),
                y=np.asarray(output_average_price),
                pen=pg.mkPen(color="#00ff00", width=2),
                name="Average Profit",
            )

            self.p1.plot(
                x=np.asarray(output_current_price_index),
                y=np.asarray(output_current_price),
                pen=pg.mkPen(color="#00ff00", width=1, style=Qt.PenStyle.DashLine),
                name="Current Profit",
            )

            qty_available_df = df[["total_quantity_available"]].dropna()
            if not qty_available_df.empty:
                qty_available_index = (
                    pd.to_datetime(qty_available_df.index).astype("int64") // 10**9
                )
                qty_available_data = pd.to_numeric(
                    qty_available_df["total_quantity_available"], errors="coerce"
                )
                plot_item2 = pg.PlotDataItem(
                    x=np.asarray(qty_available_index),
                    y=np.asarray(qty_available_data),
                    pen=pg.mkPen(color="#0088ff", width=1, style=Qt.PenStyle.DashLine),
                    name="Total Qty Available",
                )
                self.p2.addItem(plot_item2)

                # Add moving average line
                qty_available_data.index = pd.to_datetime(qty_available_df.index)
                qty_available_ma = qty_available_data.rolling(window="7D").mean()
                if not qty_available_ma.empty:
                    valid_mask = ~qty_available_ma.isna()
                    if valid_mask.any():
                        ma_line2 = pg.PlotDataItem(
                            x=np.asarray(qty_available_index)[valid_mask],
                            y=np.asarray(qty_available_ma.values)[valid_mask],
                            pen=pg.mkPen(color="#0088ff", width=2),
                            name="Qty Available MA",
                        )
                        self.p2.addItem(ma_line2)

                # Set Y range for p2 based on data to avoid autorange side effects on p1
                y_min = (
                    np.nanmin(qty_available_data.to_numpy())
                    if len(qty_available_data)
                    else np.nan
                )
                y_max = (
                    np.nanmax(qty_available_data.to_numpy())
                    if len(qty_available_data)
                    else np.nan
                )
                # Always include 0 on the y-axis
                if np.isfinite(y_min) and np.isfinite(y_max):
                    y_min = min(y_min, 0)
                    y_max = max(y_max, 0)
                    span = y_max - y_min
                    if span == 0:
                        span = max(abs(y_max), 1)
                    pad = span * 0.05
                    self.p2.setYRange(y_min - pad, y_max + pad, padding=0)

                # Add label at latest point anchored to moving average
                last_x = qty_available_index[-1]
                last_y_current = qty_available_data.iloc[-1]
                last_y_ma = qty_available_ma.iloc[-1]
                label2 = pg.TextItem(
                    text=f"Qty Available: {last_y_current:,.0f}\nMA: {last_y_ma:,.0f}",
                    color="#0088ff",
                    anchor=(1, 0),
                )
                label2.setZValue(10)
                label2.setPos(last_x, last_y_ma)
                self.p2.addItem(label2)
            else:
                _logger.debug(
                    f"No total quantity available data for recipe {recipe.id}"
                )

            qty_sold_df = (
                df[["quantity_sold"]].dropna()
                if "quantity_sold" in df.columns
                else pd.DataFrame()
            )
            if not qty_sold_df.empty:
                qty_sold_index = (
                    pd.to_datetime(qty_sold_df.index).astype("int64") // 10**9
                )
                qty_sold_data = pd.to_numeric(
                    qty_sold_df["quantity_sold"], errors="coerce"
                )
                plot_item3 = pg.PlotDataItem(
                    x=np.asarray(qty_sold_index),
                    y=np.asarray(qty_sold_data),
                    pen=pg.mkPen(color="#ff8800", width=1, style=Qt.PenStyle.DashLine),
                    name="Qty Sold Daily",
                )
                self.p3.addItem(plot_item3)

                # Add moving average line
                qty_sold_data.index = pd.to_datetime(qty_sold_df.index)
                qty_sold_ma = qty_sold_data.rolling(window="7D").mean()
                if not qty_sold_ma.empty:
                    valid_mask = ~qty_sold_ma.isna()
                    if valid_mask.any():
                        ma_line3 = pg.PlotDataItem(
                            x=np.asarray(qty_sold_index)[valid_mask],
                            y=np.asarray(qty_sold_ma.values)[valid_mask],
                            pen=pg.mkPen(color="#ff8800", width=2),
                            name="Qty Sold MA",
                        )
                        self.p3.addItem(ma_line3)

                # Set Y range for p3 based on data to avoid autorange side effects on p1
                y_min = (
                    np.nanmin(qty_sold_data.to_numpy())
                    if len(qty_sold_data)
                    else np.nan
                )
                y_max = (
                    np.nanmax(qty_sold_data.to_numpy())
                    if len(qty_sold_data)
                    else np.nan
                )
                # Always include 0 on the y-axis
                if np.isfinite(y_min) and np.isfinite(y_max):
                    y_min = min(y_min, 0)
                    y_max = max(y_max, 0)
                    span = y_max - y_min
                    if span == 0:
                        span = max(abs(y_max), 1)
                    pad = span * 0.05
                    self.p3.setYRange(y_min - pad, y_max + pad, padding=0)

                # Add label at latest point anchored to moving average
                last_x = qty_sold_index[-1]
                last_y_current = qty_sold_data.iloc[-1]
                last_y_ma = qty_sold_ma.iloc[-1]
                label3 = pg.TextItem(
                    text=f"Qty Sold: {last_y_current:,.0f}\nMA: {last_y_ma:,.0f}",
                    color="#ff8800",
                    anchor=(1, 0),
                )
                label3.setZValue(10)
                label3.setPos(last_x, last_y_ma)
                self.p3.addItem(label3)
            else:
                _logger.debug(f"No quantity sold data for recipe {recipe.id}")

            ingredient_average_price_subtotal = None
            ingredient_current_price_subtotal = None
            colormap = cm.get_cmap("tab10")
            num_ingredients = len(recipe.inputs)
            ingredient_colors = [
                mcolors.to_hex(colormap(i / num_ingredients))
                for i in range(num_ingredients)
            ]

            for material_idx, material_amount in enumerate(recipe.inputs):
                material_listing = Exchange.get_listing(material_amount.id)
                if not material_listing or material_listing.dataframe.empty:
                    _logger.debug(f"No data for ingredient {material_amount.id}")
                    continue

                material_df = material_listing.dataframe.copy()
                material_df.sort_index(inplace=True)

                input_average_price = material_df[["average_price"]].dropna().copy()
                if input_average_price.empty:
                    continue
                input_average_price.index = (
                    pd.to_datetime(input_average_price.index).astype("int64") // 10**9
                )
                input_average_price["price"] = (
                    input_average_price["average_price"]
                    * material_amount.am
                    / (100 * recipe.timeMinutes / 60)
                )

                input_current_price = material_df[["current_price"]].dropna().copy()
                if input_current_price.empty:
                    continue
                input_current_price.index = (
                    pd.to_datetime(input_current_price.index).astype("int64") // 10**9
                )
                input_current_price["price"] = (
                    input_current_price["current_price"]
                    * material_amount.am
                    / (100 * recipe.timeMinutes / 60)
                )

                ingredient_color = ingredient_colors[material_idx]

                self.p1.plot(
                    x=input_average_price.index.to_numpy(),
                    y=input_average_price["price"].to_numpy(),
                    pen=pg.mkPen(color=ingredient_color, width=1),
                    name=f"Average Ingredient: {material_listing.name}",
                )
                self.p1.plot(
                    x=input_current_price.index.to_numpy(),
                    y=input_current_price["price"].to_numpy(),
                    pen=pg.mkPen(
                        color=ingredient_color, width=1, style=Qt.PenStyle.DashLine
                    ),
                    name=f"Current Ingredient: {material_listing.name}",
                )

                if not input_current_price.empty:
                    label = pg.TextItem(
                        text=f"{material_listing.name}\nAv Cost/hr: {input_average_price['price'].iloc[-1]:,.2f}\nCurr Cost/hr: {input_current_price['price'].iloc[-1]:,.2f}",
                        color=ingredient_color,
                        anchor=(1, 1),
                    )
                    label.setPos(
                        input_current_price.index[-1],
                        input_current_price["price"].iloc[-1],
                    )
                    self.p1.addItem(label)

                ingredient_average_price_subtotal = (
                    align_add(
                        ingredient_average_price_subtotal,
                        input_average_price["price"],
                    )
                    if ingredient_average_price_subtotal is not None
                    else input_average_price["price"]
                )
                ingredient_current_price_subtotal = (
                    align_add(
                        ingredient_current_price_subtotal,
                        input_current_price["price"],
                    )
                    if ingredient_current_price_subtotal is not None
                    else input_current_price["price"]
                )

            if ingredient_average_price_subtotal is not None:
                ingredient_average_price_subtotal.dropna(inplace=True)
                if not ingredient_average_price_subtotal.empty:
                    self.p1.plot(
                        x=ingredient_average_price_subtotal.index.to_numpy(),
                        y=ingredient_average_price_subtotal.to_numpy(),
                        pen=pg.mkPen(color="#ff0000", width=2),
                        name="Average Ingredient Total",
                    )

            if ingredient_current_price_subtotal is not None:
                ingredient_current_price_subtotal.dropna(inplace=True)
                if not ingredient_current_price_subtotal.empty:
                    self.p1.plot(
                        x=ingredient_current_price_subtotal.index.to_numpy(),
                        y=ingredient_current_price_subtotal.to_numpy(),
                        pen=pg.mkPen(
                            color="#ff0000", width=1, style=Qt.PenStyle.DashLine
                        ),
                        name="Current Ingredient Total",
                    )
                    if len(ingredient_current_price_subtotal) > 0:
                        label = pg.TextItem(
                            text=f"Average Cost/hr: {ingredient_average_price_subtotal.iloc[-1]:,.2f}\nCurrent Cost/hr: {ingredient_current_price_subtotal.iloc[-1]:,.2f}",
                            color="#ff0000",
                            anchor=(1, 1),
                        )
                        label.setPos(
                            ingredient_current_price_subtotal.index[-1],
                            ingredient_current_price_subtotal.iloc[-1],
                        )
                        self.p1.addItem(label)

            # Constrain X to non-negative values with slight padding on the right
            self.p1.vb.autoRange()

            LIMIT_X_AXIS = True
            if LIMIT_X_AXIS:
                # Limit x-axis width to 1 month maximum, showing latest data
                one_month_seconds = 30 * 24 * 60 * 60
                x_min, x_max = self.p1.vb.viewRange()[0]
                x_range = x_max - x_min
                if x_range > one_month_seconds:
                    x_min = x_max - one_month_seconds
                    self.p1.vb.setXRange(x_min, x_max, padding=0)
        except Exception as e:
            _logger.error(f"Error plotting recipe {recipe.id}: {e}", exc_info=True)

    def on_mouse_moved(self, pos):
        # Convert mouse position to plot coordinates
        mouse_point = self.p1.vb.mapSceneToView(pos)
        mouse_x, mouse_y = mouse_point.x(), mouse_point.y()

        # Find the nearest data point
        if not self.data_points:
            return

        distances = [
            (mouse_x - x) ** 2 + (mouse_y - y) ** 2 for x, y in self.data_points
        ]
        nearest_index = np.argmin(distances)
        nearest_x, nearest_y = self.data_points[nearest_index]

        # Update the label with the nearest point's value
        self.label.setText(
            f"Market Price: {self.listing_price[nearest_index]:,.2f}<br>Profit/hr: {nearest_y:,.2f}<br>Time: {pd.to_datetime(nearest_x, unit='s')}"
        )
        view_point = self.p1.vb.mapViewToScene(pg.Point(nearest_x, nearest_y))
        self.label.setPos(view_point - self.label.boundingRect().topRight())


def calculate_construction_cost(
    building: Building, current_level: int | None = None
) -> float:
    # Level is the current level, not what level you're building to. So level 0 means building from scratch, level 1 means upgrading from level 1 to level 2, etc.
    # Calculate building cost from construction materials
    if current_level is None or current_level == 0:
        growth_factor = 1.0
    elif current_level < 0:
        _logger.warning(
            f"Invalid building level {current_level} for {building.name} ({building.id}). Level cannot be negative."
        )
        growth_factor = 1.0
    elif current_level < 9:
        growth_factor = 0.1 * current_level + 1.07**current_level
    else:
        growth_factor = (
            0.7
            + (1.07**7)
            + ((current_level - 6) ** 1.03)
            - (0.95 * (current_level - 6))
        )
    cost = 0
    for material in building.constructionMaterials:
        material_listing = Exchange.get_listing(material.id)
        material_price = (
            material_listing.average_price
            if use_average_price
            else material_listing.current_price
        )
        material_amount = math.ceil(material.am * growth_factor)
        if material_listing and material_price > 0:
            cost += (material_price * material_amount) / 100  # Convert cents to dollars
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


def calculate_maintenance_cost(
    recipe: Recipe, building: Building, level: int = 1
) -> float | None:
    # Calculate building maintenance cost
    # Buildings degrade over 40 days of use, so maintainance cost is 1/40th of construction cost per day, or 1/960th per hour
    # Returns the maintainance cost per hour in dollars
    construction_cost = calculate_construction_cost(building, level - 1)
    return construction_cost / 960 if construction_cost > 0 else None
