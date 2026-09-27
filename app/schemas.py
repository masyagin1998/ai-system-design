from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: str
    created_at: datetime
