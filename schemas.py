from pydantic import BaseModel
from typing import Optional, List

class ItemIn(BaseModel):
    name: Optional[str] = None
    rarity: int
    crate: Optional[str] = None
    in_min: float = 0.0
    in_max: float = 1.0
    float_value: float

class BatchIn(BaseModel):
    bucket_id: int
    items: List[ItemIn]

class ItemUpdate(BaseModel):
    name: Optional[str] = None
    rarity: Optional[int] = None
    crate: Optional[str] = None
    in_min: Optional[float] = None
    in_max: Optional[float] = None
    float_value: Optional[float] = None
