from typing import Any, Dict, List, Optional, Tuple, Type, Union
from numpy import average
from pydantic import BaseModel, Field
from pydantic_collections import BaseCollectionModel
import pandas as pd
from datetime import datetime


class Order(BaseModel):
    id: int = Field(alias="cId")
    name: str = Field(alias="cName")
    price: int = Field(alias="unitPrice")
    qty: int

class PriceHistoryEntry(BaseModel):
    date: str
    average_price: int = Field(alias="avgPrice")
    quantity_sold: int = Field(alias="qtySold")
    quantity_remaining: int = Field(alias="qtyRemaining")
class Listing(BaseModel):
    id: int = Field(alias="matId")
    name: str = Field(alias="matName")
    current_price: int = Field(alias="currentPrice")
    dataframe: pd.DataFrame = Field(
        default_factory=lambda: pd.DataFrame(columns=["current_price", "average_price", "total_quantity_available", "quantity_sold"])
    )
    average_price: int = Field(alias="avgPrice")
    updated_time: datetime = Field(default_factory=datetime.now)
    total_quantity_available: int = Field(alias="totalQtyAvailable")
    orders: List[Order]
    average_quantity_sold_daily: float = Field(alias="avgQtySoldDaily")
    price_history: List[PriceHistoryEntry] = Field(alias="priceHistory")

    class Config:
        arbitrary_types_allowed = True


class Listings(BaseCollectionModel[Listing]):
    pass
