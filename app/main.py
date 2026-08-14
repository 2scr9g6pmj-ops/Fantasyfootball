from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.api.routes import router
from app.database import Base, engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(
    title="Sleeper Fantasy Football Database",
    version="0.1.0",
    description="Read-only multi-league Sleeper data sync and foundation for lineup recommendations.",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html><html><head><title>Fantasy Football</title>
    <style>body{font:16px system-ui;max-width:780px;margin:4rem auto;padding:1rem;color:#17202a}code{background:#eef2f5;padding:.2rem .4rem}</style>
    </head><body><h1>Sleeper Fantasy Football Database</h1>
    <p>Milestone 1 is ready. Run <code>POST /api/sync</code>, then inspect <a href='/docs'>the API documentation</a>.</p>
    <p>This application is read-only and never modifies Sleeper lineups or rosters.</p></body></html>"""

