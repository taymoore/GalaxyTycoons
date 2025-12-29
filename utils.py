import pandas as pd
from typing import Tuple, Union, List
import logging
import itertools

from api.gameData import get_gamedata, get_building, get_worker, get_item_name, get_worker_housing
from api.exchange import Exchange
from api.models.gameData import WorkerType

_logger = logging.getLogger(__name__)


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


def calculate_profit_and_consumables(recipe):
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
        building = get_building(recipe.producedIn)
        listing = Exchange.get_listing(recipe.output.id)
        base_profit_per_hour = listing.current_price * recipe.output.am / 100

        for material_amount in recipe.inputs:
            material_price = Exchange.get_listing(material_amount.id).current_price / 100
            if material_price < 1:
                return None
            base_profit_per_hour -= material_price * material_amount.am

        base_profit_per_hour = base_profit_per_hour / (recipe.timeMinutes / 60)

        # Calculate worker cost
        optimal_profit_per_hour = float("-inf")
        # Get list of consumables
        consumable_id_set: set[int] = set()
        for worker_type, worker_count in enumerate(building.workersNeeded or [], start=1):
            if worker_count == 0:
                continue
            worker = get_worker(worker_type)
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
                    worker = get_worker(worker_type)
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
                    base_profit_per_hour * total_worker_satisfaction - worker_cost_per_hour
                )
                if configuration_profit_per_hour > optimal_profit_per_hour:
                    optimal_profit_per_hour = configuration_profit_per_hour
                    consumable_preferred_combination = consumable_list
        if optimal_profit_per_hour == float("-inf"):
            _logger.debug(
                f"Could not calculate profit for recipe {get_item_name(recipe.output.id)} ({recipe.id}). Base profit/hr: {base_profit_per_hour:,.2f}."
            )
            return None

        if consumable_preferred_combination is None:
            _logger.error(
                f"No valid consumable combination found for recipe {get_item_name(recipe.output.id)} ({recipe.id}). This should not happen."
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
            worker_housing_building = get_worker_housing(WorkerType(worker_type))
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
                    * (worker_count / worker_housing_building.workersHousing[worker_type - 1])  # scale by worker count
                    / (40 * 24)  # 40 days depreciation period
                    / 100  # convert cents to dollars
                )

        return optimal_profit_per_hour - building_depreciation_per_hour, consumable_preferred_combination, consumable_rejected_combination
    except Exception as e:
        _logger.error(f"Error calculating profit for recipe {get_item_name(recipe.output.id)} ({recipe.id}): {e}")
        return None


def find_best_recipe_for_building(building_id: int, tech_level: int = float("inf")) -> None | Tuple[str, float, str]:
    """
    Find the best recipe (highest profit/hr) for a given building and tech level.
    
    Args:
        building_id: ID of the building
        tech_level: Technology level filter
    
    Returns:
        None | tuple[str, float, str]: Recipe name, profit/hr, and consumables string
    """
    try:
        game_data = get_gamedata()
        building = get_building(building_id)
        
        best_recipe = None
        best_profit = float("-inf")
        best_consumables = None
        
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
                
            profit, consumables, _ = result
            if profit > best_profit:
                best_profit = profit
                best_recipe = recipe
                best_consumables = consumables
        
        if best_recipe is None:
            return None
            
        # Format consumables as string
        consumables_str = ", ".join(
            get_item_name(c_id) for c_id in best_consumables
        ) if best_consumables else "None"
        
        return get_item_name(best_recipe.output.id), best_profit, consumables_str
    except Exception as e:
        _logger.error(f"Error finding best recipe for building {building_id}: {e}")
        return None
