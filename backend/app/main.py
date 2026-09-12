from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import Base, engine
from .routers import appraisals, media, sessions

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Kamion Inspect API", version="0.1.0")

# Wide open for the hackathon demo (mobile/web client origin is unknown ahead
# of time); tighten before anything beyond a judged demo goes live.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router)
app.include_router(media.router)
app.include_router(appraisals.router)


@app.get("/health")
def health():
    return {"status": "ok"}
