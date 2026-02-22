from turtle import update
from typing import List, Optional, Dict
from matplotlib.pylab import f
from matplotlib.pyplot import cla
from pydantic import BaseModel, Field, field_validator
from enum import IntEnum


class BuildingStatus(IntEnum):
    UNDEFINED = 0
    EMPTY = 1
    BUILDING = 2
    DEBRIS = 3
    PREMIUM = 4


class BuildingType(IntEnum):
    UNDEFINED = 0
    MINE = 1
    SMELTER = 2
    PUMP = 3
    COLONY_BARRACKS = 4
    GAS_COLLECTOR = 5
    PREFAB_PLANT = 6
    REFINERY = 7
    FOOD_PROCESSING_PLANT = 8
    HEADQUARTERS = 9
    FARM = 10
    BASIC_ASSEMBLY_PLANT = 11
    POLYMER_PLANT = 12
    CHEMISTRY_PLANT = 13
    WAREHOUSE = 14
    TEXTILE_MILL = 15
    ADVANCED_ASSEMBLY_PLANT = 16
    RANCH = 17
    SURVEY_STATION = 18
    ORCHARD = 19
    LABORATORY = 20
    RESIDENTIAL_COMPLEX = 21
    COMFORT_QUARTERS = 22
    STELLAR_SUITES = 23
    SEMICONDUCTOR_FOUNDRY = 24
    ELECTRONICS_FACTORY = 25
    WELDING_PLANT = 26
    QUARRY_COMPLEX = 27
    SCIENCE_INSTITUTE = 28
    ADVANCED_PREFAB_PLANT = 29
    SHIPYARD = 30
    MICROELECTRONICS_FACTORY = 31
    ADVANCED_MATERIALS_LAB = 32
    EXOTIC_MATTER_LAB = 33
    AQUAPONICS_FARM = 34
    ROBOTICS_FACILITY = 35
    CULINARY_STUDIO = 36
    APEX_PREFAB_PLANT = 37
    QUANTUM_COMPUTING_CENTER = 38
    ADVANCED_GAS_COLLECTOR = 39
    FOUNDRY = 40
    NANOMATERIAL_LAB = 41
    QUANTUM_FACTORY = 42


class Task(BaseModel):
    building_id: int = Field(alias="bId")
    recipe_id: int = Field(alias="recipeId")
    start_date: str = Field(alias="startDate")  # Date when production started
    updated_date: str = Field(alias="updD")  # Date when production was last updated
    completion_date: str = Field(alias="comD")  # Expected completion date
    updated_part: float = Field(
        alias="updPart"
    )  # Part of the task completed at last update
    amount_multiplier: int = Field(
        alias="mul"
    )  # Amount multiplier (both input and output, level 2 building produces 2x)


class Building(BaseModel):
    id: int
    type: BuildingType
    level: int
    condition: float = Field(alias="cond")
    task: Optional[Task] = None


class BuildingSlot(BaseModel):
    id: int
    status: BuildingStatus
    building: Optional[Building] = None

    @field_validator("status", mode="before")
    @classmethod
    def parse_status(cls, v):
        if isinstance(v, str):
            # Extract the number from strings like "0 = Undefined"
            return int(v.split("=")[0].strip())
        return v


class ProductionOrder(BaseModel):
    id: int
    recipe_id: int = Field(alias="rId")
    amount: int = Field(alias="amt")  # How many times to produce the recipe


class ConsumptionMaterial(BaseModel):
    material_id: int = Field(alias="matId")
    is_eating: bool = Field(alias="isEating")
    updated_date: str = Field(alias="luDate")  # Last updated date
    buffer: float  # Extra amount eaten last update date
    rate: float  # Amount consumed per day


class Workforce(BaseModel):
    base_id: int = Field(alias="baseId")
    workers_needed: List[int] = Field(
        alias="workersNeeded"
    )  # Quantity of each type: [Worker, Technician, Engineer, Scientist]
    workers_housing: List[int] = Field(
        alias="workersHousing"
    )  # Housing capacity by type
    workers_count: List[int] = Field(
        alias="workersCount"
    )  # Current number of each type of worker
    consumption_materials: List[ConsumptionMaterial] = Field(
        alias="consumptionMaterials"
    )
    workers_satisfaction: List[float] = Field(
        alias="workersSatisfaction"
    )  # Satisfaction levels for each type of worker


class Material(BaseModel):
    id: int
    amount: int = Field(alias="am")


class Warehouse(BaseModel):
    id: int
    capacity: float = Field(alias="cap")
    materials: List[Material] = Field(
        alias="mats"
    )  # List of materials stored in the warehouse


class Base(BaseModel):
    id: int
    planet_id: int = Field(alias="planetId")
    warehouse_id: int = Field(alias="warehouseId")
    name: str
    building_slots: List[BuildingSlot] = Field(alias="buildingSlots", default=[])
    production_orders: List[ProductionOrder] = Field(
        alias="productionOrders", default=[]
    )
    workforce: Optional[Workforce] = None
    warehouse: Optional[Warehouse] = None


class FlightType(IntEnum):
    NONE = 0
    NORMAL = 1
    EMERGENCY = 2
    CANCELLED = 3


class Flight(BaseModel):
    dest_planet_id: int = Field(alias="destPId")
    start_date: str = Field(alias="sDate")
    arrival_date: str = Field(alias="aDate")
    start_fuel: float = Field(alias="startFuel")
    arrival_fuel: float = Field(alias="arrivalFuel")
    type: FlightType
    auto_unload: bool = Field(alias="aUnload")


class Ship(BaseModel):
    id: int
    cId: int
    warehouse_id: int = Field(alias="warehouseId")
    name: str
    fuel: float
    condition: float
    pId: int
    flight: Optional[Flight] = None
    warehouse: Optional[Warehouse] = None


class Technology(BaseModel):
    id: int  # 0 if undefined
    level: int


class Company(BaseModel):
    name: str
    bases: List[Base]
    ships: List[Ship]
    cash: int
    technologies: List[Technology]
