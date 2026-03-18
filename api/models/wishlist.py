from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from enum import IntEnum

from api.models.gameData import MaterialAmount


class WishlistModel(BaseModel):
    id: int  # 0 is no wishlist. 1-10_000_000 are planet ID; 50_000_001+ are custom wishlists
    title: Optional[str]  # Only for custom wishlists, otherwise None
    mats: List[MaterialAmount]


class CreateWishlistRequest(BaseModel):
    title: str  # Max 40 characters
    mats: List[MaterialAmount]
