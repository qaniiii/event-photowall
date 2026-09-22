import os
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Header, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import cloudinary
import cloudinary.uploader
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from database import engine, Base, get_db
import models
import schemas

load_dotenv()

# Cloudinary Setup
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

# Admin Secret
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "default_insecure_secret")

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Event Photo Wall API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


# --- Admin Security Dependency ---
def verify_admin_key(x_admin_key: str = Header(..., description="Admin Secret Passkey")):
    if x_admin_key != ADMIN_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Admin Secret Key."
        )
    return True


# Helper to extract Cloudinary public_id from URL
def get_cloudinary_public_id(image_url: str) -> str:
    # URL pattern: .../upload/(v12345/)?(photowall/EVENT/filename).ext
    match = re.search(r"/upload/(?:v\d+/)?(.+)\.[a-zA-Z0-9]+$", image_url)
    if match:
        return match.group(1)
    return None


# ==========================================
# GUEST & PUBLIC ENDPOINTS
# ==========================================

@app.get("/events/{access_code}", response_model=schemas.EventResponse)
def get_event(access_code: str, db: Session = Depends(get_db)):
    event = db.query(models.Event).filter(models.Event.access_code == access_code.upper()).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@app.post("/events/{access_code}/photos/", response_model=schemas.PhotoResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def upload_photo(
    request: Request,
    access_code: str,
    file: UploadFile = File(...),
    uploader_name: str = Form("Anonymous"),
    caption: str = Form(""),
    db: Session = Depends(get_db)
):
    event = db.query(models.Event).filter(models.Event.access_code == access_code.upper()).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type ({file.content_type}). Allowed: JPG, PNG, WEBP, HEIC."
        )

    contents = file.file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {MAX_FILE_SIZE / (1024 * 1024):.0f}MB."
        )
    
    file.file.seek(0)

    try:
        upload_result = cloudinary.uploader.upload(
            file.file,
            folder=f"photowall/{access_code.upper()}"
        )
        image_url = upload_result.get("secure_url")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image upload failed: {str(e)}")

    photo = models.Photo(
        event_id=event.id,
        image_url=image_url,
        uploader_name=uploader_name.strip() or "Anonymous",
        caption=caption.strip()
    )
    db.add(photo)
    db.commit()
    db.refresh(photo)
    return photo

# Rate-Limited & Validated: Multi-photo Batch Upload (Up to 5 images per request)
@app.post("/events/{access_code}/photos/batch", response_model=list[schemas.PhotoResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def upload_photos_batch(
    request: Request,
    access_code: str,
    files: list[UploadFile] = File(...),
    uploader_name: str = Form("Anonymous"),
    caption: str = Form(""),
    db: Session = Depends(get_db)
):
    event = db.query(models.Event).filter(models.Event.access_code == access_code.upper()).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if len(files) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 photos can be uploaded simultaneously.")

    created_photos = []
    clean_name = uploader_name.strip() or "Anonymous"
    clean_caption = caption.strip()

    for file in files:
        if file.content_type not in ALLOWED_IMAGE_TYPES:
            continue  # Skip unallowed types gracefully in batch

        contents = file.file.read()
        if len(contents) > MAX_FILE_SIZE:
            continue  # Skip files exceeding 10MB
        file.file.seek(0)

        try:
            upload_result = cloudinary.uploader.upload(
                file.file,
                folder=f"photowall/{access_code.upper()}"
            )
            image_url = upload_result.get("secure_url")

            photo = models.Photo(
                event_id=event.id,
                image_url=image_url,
                uploader_name=clean_name,
                caption=clean_caption
            )
            db.add(photo)
            db.commit()
            db.refresh(photo)
            created_photos.append(photo)
        except Exception:
            continue

    if not created_photos:
        raise HTTPException(status_code=400, detail="No valid images were successfully uploaded.")

    return created_photos

# ==========================================
# PROTECTED ADMIN ENDPOINTS
# ==========================================

# 1. Create a new event
@app.post("/events/", response_model=schemas.EventResponse, status_code=status.HTTP_201_CREATED)
def create_event(
    event: schemas.EventCreate,
    db: Session = Depends(get_db),
    is_admin: bool = Depends(verify_admin_key)
):
    existing = db.query(models.Event).filter(models.Event.access_code == event.access_code.upper()).first()
    if existing:
        raise HTTPException(status_code=400, detail="Access code already exists")
    
    new_event = models.Event(
        title=event.title,
        access_code=event.access_code.upper()
    )
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    return new_event


# 2. Get list of all events with their photos
@app.get("/admin/events", status_code=status.HTTP_200_OK)
def admin_get_all_events(
    db: Session = Depends(get_db),
    is_admin: bool = Depends(verify_admin_key)
):
    events = db.query(models.Event).order_by(models.Event.created_at.desc()).all()
    result = []
    for ev in events:
        result.append({
            "id": ev.id,
            "title": ev.title,
            "access_code": ev.access_code,
            "created_at": ev.created_at.isoformat() if ev.created_at else None,
            "photo_count": len(ev.photos),
            "photos": [
                {
                    "id": p.id,
                    "image_url": p.image_url,
                    "uploader_name": p.uploader_name,
                    "caption": p.caption,
                    "uploaded_at": p.uploaded_at.isoformat() if p.uploaded_at else None
                } for p in ev.photos
            ]
        })
    return result


# 3. Moderation: Delete a specific photo
@app.delete("/admin/photos/{photo_id}", status_code=status.HTTP_200_OK)
def admin_delete_photo(
    photo_id: int,
    db: Session = Depends(get_db),
    is_admin: bool = Depends(verify_admin_key)
):
    photo = db.query(models.Photo).filter(models.Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    # 1. Attempt to delete from Cloudinary CDN
    public_id = get_cloudinary_public_id(photo.image_url)
    if public_id:
        try:
            cloudinary.uploader.destroy(public_id)
        except Exception as e:
            print(f"Warning: Cloudinary asset removal failed: {e}")

    # 2. Delete record from Neon DB
    db.delete(photo)
    db.commit()
    return {"message": "Photo deleted successfully", "photo_id": photo_id}

# Add this schema helper or import
class EventUpdate(schemas.BaseModel):
    title: str | None = None
    access_code: str | None = None

# Update an existing event title or access code
@app.patch("/admin/events/{event_id}", status_code=status.HTTP_200_OK)
def admin_update_event(
    event_id: int,
    payload: EventUpdate,
    db: Session = Depends(get_db),
    is_admin: bool = Depends(verify_admin_key)
):
    event = db.query(models.Event).filter(models.Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if payload.access_code:
        new_code = payload.access_code.strip().upper()
        if new_code != event.access_code:
            existing = db.query(models.Event).filter(models.Event.access_code == new_code).first()
            if existing:
                raise HTTPException(status_code=400, detail="Access code is already taken by another event.")
            event.access_code = new_code

    if payload.title:
        event.title = payload.title.strip()

    db.commit()
    db.refresh(event)
    return {
        "message": "Event updated successfully",
        "id": event.id,
        "title": event.title,
        "access_code": event.access_code
    }

# 4. Moderation: Delete an entire event
@app.delete("/admin/events/{event_id}", status_code=status.HTTP_200_OK)
def admin_delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    is_admin: bool = Depends(verify_admin_key)
):
    event = db.query(models.Event).filter(models.Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Delete all photos in Cloudinary for this event
    for p in event.photos:
        pub_id = get_cloudinary_public_id(p.image_url)
        if pub_id:
            try:
                cloudinary.uploader.destroy(pub_id)
            except Exception:
                pass

    db.delete(event)
    db.commit()
    return {"message": f"Event '{event.title}' and all associated photos deleted."}


# Mount Static directory
app.mount("/", StaticFiles(directory="static", html=True), name="static")