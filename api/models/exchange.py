from typing import Any, Dict, List, Optional, Tuple, Type, Union
from pydantic import BaseModel, Field
from pydantic_collections import BaseCollectionModel
import pandas as pd
from datetime import datetime


class Listing(BaseModel):
    id: int = Field(alias="matId")
    name: str = Field(alias="matName")
    current_price: int = Field(alias="currentPrice")
    current_price_history: pd.DataFrame = Field(
        default_factory=lambda: pd.DataFrame(columns=["price"])
    )
    average_price: int = Field(alias="avgPrice")
    average_price_history: pd.DataFrame = Field(
        default_factory=lambda: pd.DataFrame(columns=["price"])
    )
    updated_time: datetime = Field(default_factory=datetime.now)

    class Config:
        arbitrary_types_allowed = True


class Listings(BaseCollectionModel[Listing]):
    pass
