from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from enum import IntEnum
from api.models.gameData import Specialization

class Base(BaseModel):
    id: int
    planet_id: int = Field(alias="planetId")
    warehouse_id: int = Field(alias="warehouseId")
    name: str

class Ship(BaseModel):
    id: int
    cId: int
    warehouse_id: int = Field(alias="warehouseId")
    name: str
    fuel: int
    condition: float
    pId: int

class Technology(BaseModel):
    id: int # 0 if undefined
    level: int

class Company(BaseModel):
    name: str
    bases: List[Base]
    ships: List[Ship]
    cash: int
    technologies: List[Technology]


