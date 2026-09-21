import os
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

from database import engine, Base, get_db
import models
import schemas

load_dotenv()

# Configure Cloudinary SDK with your credentials from .env
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

# Automatically create tables in photowall.db if they don't exist yet
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Event Photo-Wall API", version="1.0.0")

# Mount the static directory to serve frontend assets (CSS, JS, images)
app.mount("/static", StaticFiles(directory="static"), name="static")


# --- Frontend Route ---

@app.get("/")
def serve_home():
    """Serves the main photo wall web interface."""
    return FileResponse("static/index.html")


# --- API Routes ---

# 1. Create a new event (e.g. Host creates a wedding room)
@app.post("/events/", response_model=schemas.EventResponse, status_code=status.HTTP_201_CREATED)
def create_event(event: schemas.EventCreate, db: Session = Depends(get_db)):
    db_event = db.query(models.Event).filter(models.Event.access_code == event.access_code.upper()).first()
    if db_event:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="An event with this access code already exists."
        )
    
    new_event = models.Event(
        title=event.title, 
        access_code=event.access_code.upper()
    )
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    return new_event


# 2. Get event details and its full photo gallery via access code
@app.get("/events/{access_code}", response_model=schemas.EventResponse)
def get_event(access_code: str, db: Session = Depends(get_db)):
    event = db.query(models.Event).filter(models.Event.access_code == access_code.upper()).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Event not found."
        )
    return event


# 3. Upload a photo to an event (Uploads binary to Cloudinary, saves metadata to DB)
@app.post("/events/{access_code}/photos/", response_model=schemas.PhotoResponse, status_code=status.HTTP_201_CREATED)
async def upload_photo(
    access_code: str,
    file: UploadFile = File(...),
    uploader_name: Optional[str] = Form("Guest"),
    caption: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    # Verify the event exists in the database
    event = db.query(models.Event).filter(models.Event.access_code == access_code.upper()).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Event not found."
        )

    # Upload binary file directly to Cloudinary
    try:
        upload_result = cloudinary.uploader.upload(
            file.file,
            folder=f"photowall/{access_code.upper()}"
        )
        secure_url = upload_result.get("secure_url")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Image upload failed: {str(e)}"
        )

    # Store image URL and metadata into SQLite
    new_photo = models.Photo(
        event_id=event.id,
        image_url=secure_url,
        uploader_name=uploader_name,
        caption=caption
    )
    db.add(new_photo)
    db.commit()
    db.refresh(new_photo)

    return new_photo