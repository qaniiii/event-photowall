import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict

# --- Photo Schemas ---
class PhotoBase(BaseModel):
    uploader_name: Optional[str] = "Guest"
    caption: Optional[str] = None

class PhotoResponse(PhotoBase):
    id: int
    event_id: int
    image_url: str
    uploaded_at: datetime.datetime

    # Allows Pydantic to read SQLAlchemy database models directly
    model_config = ConfigDict(from_attributes=True)


# --- Event Schemas ---
class EventCreate(BaseModel):
    title: str
    access_code: str

class EventResponse(BaseModel):
    id: int
    title: str
    access_code: str
    created_at: datetime.datetime
    photos: List[PhotoResponse] = []

    model_config = ConfigDict(from_attributes=True)