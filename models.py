import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from database import Base

class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(100), nullable=False)                         # e.g., "Sarah & Adam's Wedding"
    access_code = Column(String(20), unique=True, index=True, nullable=False) # e.g., "WED2026"
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Cascading delete: if an event is deleted, its photos are deleted too
    photos = relationship("Photo", back_populates="event", cascade="all, delete-orphan")


class Photo(Base):
    __tablename__ = "photos"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    image_url = Column(String(500), nullable=False)                     # URL from Cloudinary
    uploader_name = Column(String(50), default="Guest")
    caption = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationship back to the parent Event
    event = relationship("Event", back_populates="photos")