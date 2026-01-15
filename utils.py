from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QModelIndex, QPointF
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
from api.gameData import GameDataManager
from api.exchange import Exchange
from api.models.gameData import Planet, WorkerType

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

                _logger.warn(
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
    recipe, abundance: float = 1.0
) -> None | tuple[float, tuple[int, ...], tuple[int, ...]]:
    """
    Calculate the profit per hour and the preferred consumable combination for a given recipe.

    Args:
        recipe: The recipe for which to calculate the profit and consumables.

    Returns:
        None | tuple[float, tuple[int, ...], tuple[int, ...]]:
            Returns a tuple containing the profit per hour, a tuple of preferred consumable IDs, and a tuple of rejected consumable IDs
            if the calculation is successful. Returns None if the calculation cannot be performed.
    """
    try:
        building = GameDataManager.get_building(recipe.producedIn)
        listing = Exchange.get_listing(recipe.output.id)
        base_profit_per_hour = listing.current_price * recipe.output.am / 100

        for material_amount in recipe.inputs:
            material_price = (
                Exchange.get_listing(material_amount.id).current_price / 100
            )
            if material_price < 1:
                return None
            base_profit_per_hour -= material_price * material_amount.am

        base_profit_per_hour = (
            base_profit_per_hour * abundance / (recipe.timeMinutes / 60)
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
                            consumable_listing = Exchange.get_listing(consumable.matId)
                            if consumable_listing.current_price < 1:
                                combination_valid = False
                                break
                            worker_cost_per_hour += (
                                consumable_listing.current_price  # in cents
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
                    worker_type_satisfaction -= 0.1 * consumable_optional_missed_count
                    worker_type_satisfaction *= 0.6**consumable_essential_missed_count
                    worker_type_satisfaction = max(worker_type_satisfaction, 0.1)
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
        consumable_rejected_combination = consumable_id_set.difference(
            consumable_preferred_combination
        )

        # Find building depreciation cost per hour
        building_depreciation_per_hour = 0.0
        for material in building.constructionMaterials:
            material_listing = Exchange.get_listing(material.id)
            if material_listing.current_price < 1:
                _logger.error(
                    f"Cannot calculate building depreciation for {building.name} ({building.id}) due to missing material price."
                )
                return None
            building_depreciation_per_hour += (
                material_listing.current_price  # in cents
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
                if material_listing.current_price < 1:
                    _logger.error(
                        f"Cannot calculate worker housing depreciation for {building.name} ({building.id}) due to missing material price."
                    )
                    return None
                building_depreciation_per_hour += (
                    material_listing.current_price  # in cents
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


def find_best_recipe_for_building(
    building_id: int, tech_level: int = float("inf")
) -> None | Tuple[str, float, tuple[int, ...], tuple[int, ...]]:
    """
    Find the best recipe (highest profit/hr) for a given building and tech level.

    Args:
        building_id: ID of the building
        tech_level: Technology level filter

    Returns:
        None | tuple[str, float, tuple[int, ...], tuple[int, ...]]: Recipe name, profit/hr, preferred consumables, and rejected consumables
    """
    try:
        game_data = GameDataManager.get()
        building = GameDataManager.get_building(building_id)

        best_recipe = None
        best_profit = float("-inf")
        best_preferred = None
        best_rejected = None

        # Find all recipes for this building
        for recipe in game_data.recipes:
            if recipe.producedIn != building_id:
                continue
            if recipe.reqTech > tech_level:
                continue
            if len(recipe.inputs) == 0:
                continue

            result = calculate_profit_and_consumables(recipe)

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
        _logger.error(f"Error finding best recipe for building {building_id}: {e}")
        return None


def find_resource_extraction_value(planet: Planet, building: Optional[int]):
    resource_values = []
    for material in planet.mats:
        recipes = GameDataManager.get_recipe_by_output(material.id)
        for recipe in recipes:
            if building is not None and recipe.producedIn != building:
                continue
            extraction_value = calculate_profit_and_consumables(
                recipe, abundance=material.ab
            )
            if extraction_value is not None:
                profit, _, _ = extraction_value
                resource_values.append((material.id, profit))
    return resource_values
