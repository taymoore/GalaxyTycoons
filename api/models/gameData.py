from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from enum import IntEnum


class RecipeType(IntEnum):
    EXTRACTION = 1
    PRODUCTION = 2
    FARMING = 3


class Specialization(IntEnum):
    NONE = 0
    CONSTRUCTION = 1
    MANUFACTURING = 2
    AGRICULTURE = 3
    RESOURCE_EXTRACTION = 4
    METALLURGY = 5
    CHEMISTRY = 6
    ELECTRONICS = 7
    FOOD_PRODUCTION = 8
    SCIENCE = 10


class WorkerType(IntEnum):
    WORKER = 1
    TECHNICIAN = 2
    ENGINEER = 3
    SCIENTIST = 4


class MaterialSource(IntEnum):
    EXTRACTION = 1
    CRAFTING = 2
    FARMING = 3


class Material(BaseModel):
    id: int  # Unique material identifier
    sName: str  # Short display name
    name: str
    description: str
    type: int  # Material category enum
    weight: float  # Weight per unit in tonnes
    source: MaterialSource
    reqTech: int  # Required technology level to produce (0 if none)
    tier: int
    cp: int  # Calculated base price in cents (price of material calculated by internal algorithm, the actual market price may vary)


class MaterialAmount(BaseModel):
    id: int
    am: int


class Recipe(BaseModel):
    id: int
    producedIn: int  # Building type ID where this recipe can be crafted
    type: RecipeType
    reqTech: int  # Required technology level to produce (0 if none)
    timeMinutes: int  # Production time in minutes
    inputs: List[MaterialAmount]
    output: MaterialAmount


class Building(BaseModel):
    id: int
    name: str
    description: str
    cost: Optional[int]  # Unused
    constructionMaterials: List[MaterialAmount]
    workersNeeded: Optional[
        List[int]
    ]  # Quantity of each type: [Worker, Technician, Engineer, Scientist]
    workersHousing: Optional[List[int]]  # Housing capacity by type
    specialization: Specialization
    tier: int
    requiredResearch: Optional[int]  # Unused
    recipesIds: Optional[List[int]]  # Recipe IDs this building can produce


class WorkerConsumable(BaseModel):
    matId: int  # Material ID consumed
    amount: int  # Daily consumption per 1,000 workers
    essential: bool


class Worker(BaseModel):
    type: WorkerType
    adminCost: int  # Administration overhead per 100 workers
    consumables: List[WorkerConsumable]


class PlanetMaterial(BaseModel):
    id: int
    ab: int  # Abundance


class Planet(BaseModel):
    id: int
    sId: int  # Parent system ID
    name: str
    type: int  # Planet classification enum
    mats: List[PlanetMaterial]
    fert: int  # Fertility
    x: int
    y: int
    size: int
    tier: int


class System(BaseModel):
    id: int
    name: str
    x: int
    y: int
    v: int  # Visual variation identifier
    planets: Optional[List[Planet]]


class BaseBuildingCost(BaseModel):
    id: int
    am: int


class GameData(BaseModel):
    materials: List[Material]
    materials_dict: Dict[int, Material] = Field(default_factory=dict, exclude=True)
    recipes: List[Recipe]
    buildings: List[Building]
    workers: List[Worker]
    systems: List[System]
    baseBuildingCost: List[BaseBuildingCost]
