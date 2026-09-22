import os
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

# Lifespan context: creates tables safely during application startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs when server starts
    Base.metadata.create_all(bind=engine)
    yield
    # Runs when server shuts down (cleanup if needed)

# Rate Limiter setup (tracks clients by IP address)
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Event Photo Wall API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security Constants
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB limit


# --- Security Dependency ---
def verify_admin_key(x_admin_key: str = Header(..., description="Admin Secret Passkey")):
    """Ensures caller has the secret key before executing sensitive routes."""
    if x_admin_key != ADMIN_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Admin Secret Key."
        )
    return True


# --- Endpoints ---

# Protected: Only authorized admins can create events
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


# Public: Guests look up an event by access code
@app.get("/events/{access_code}", response_model=schemas.EventResponse)
def get_event(access_code: str, db: Session = Depends(get_db)):
    event = db.query(models.Event).filter(models.Event.access_code == access_code.upper()).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


# Rate-Limited & Validated: Guests upload photos (Max 10 uploads per minute per IP)
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
    # 1. Verify Event Exists
    event = db.query(models.Event).filter(models.Event.access_code == access_code.upper()).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    # 2. Validate MIME Type
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type ({file.content_type}). Allowed: JPG, PNG, WEBP, HEIC."
        )

    # 3. Read & Validate File Size
    contents = file.file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {MAX_FILE_SIZE / (1024 * 1024):.0f}MB."
        )
    
    file.file.seek(0)

    # 4. Stream to Cloudinary
    try:
        upload_result = cloudinary.uploader.upload(
            file.file,
            folder=f"photowall/{access_code.upper()}"
        )
        image_url = upload_result.get("secure_url")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image upload failed: {str(e)}")

    # 5. Persist to Neon DB
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


# Serve Frontend
app.mount("/", StaticFiles(directory="static", html=True), name="static")