from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.api.routes import router
from app.api.lineups import router as lineup_router
from app.api.yahoo import router as yahoo_router
from app.database import Base, engine, ensure_schema_compatibility


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    ensure_schema_compatibility()
    yield


app = FastAPI(
    title="Sleeper Fantasy Football Database",
    version="0.1.0",
    description="Read-only multi-league Sleeper data sync and foundation for lineup recommendations.",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(lineup_router)
app.include_router(yahoo_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html><html><head><title>Lineup Assistant</title><meta name='viewport' content='width=device-width'>
    <style>body{font:16px system-ui;margin:auto;max-width:1100px;padding:2rem;background:#0b1220;color:#e5edf7}select,button,input{padding:.7rem;margin:.3rem;background:#17233a;color:white;border:1px solid #405170;border-radius:8px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem}.card,table{background:#121d31;border-radius:12px;padding:1rem}table{width:100%;margin-top:1rem;border-collapse:collapse}td,th{padding:.7rem;border-bottom:1px solid #2b3952;text-align:left}.good{color:#65d99b}</style></head>
    <body><h1>Fantasy Football Lineup Assistant</h1><div><button onclick='syncYahoo()'>Sync Yahoo</button><span id='syncStatus'></span></div><div><select id='league'></select><input id='week' type='number' min='1' max='18' value='1'><select id='profile'><option>balanced</option><option>conservative</option><option>upside</option></select><button onclick='load()'>Analyze</button></div><div class='cards' id='cards'></div><table><thead><tr><th>Slot</th><th>Player</th><th>Team</th><th>Projection</th><th>Start Score</th><th>Health</th></tr></thead><tbody id='lineup'></tbody></table><p><a href='/docs' style='color:#78b5ff'>API & projection import</a></p>
    <script>async function init(){let ls=await(await fetch('/api/leagues')).json();league.innerHTML=ls.map(x=>`<option value='${x.league_id}'>${x.platform==='yahoo'?'Yahoo':'Sleeper'} · ${x.name}</option>`).join('');if(ls.length)load()}async function syncYahoo(){syncStatus.textContent=' Syncing…';let x=await fetch('/api/platforms/yahoo/sync',{method:'POST'}),r=await x.json();syncStatus.textContent=x.ok?` Synced ${r.leagues_synced} leagues`:` ${r.detail||'Sync failed'}`;if(x.ok)await init()}async function load(){let u=`/api/leagues/${league.value}/recommendations/${week.value}?profile=${profile.value}`,r=await(await fetch(u)).json();cards.innerHTML=`<div class=card>Current<br><b>${r.current_projected_points}</b></div><div class=card>Optimized<br><b class=good>${r.optimized_projected_points}</b></div><div class=card>Changes<br><b>${r.changes.length}</b></div>`;lineup.innerHTML=r.recommended_lineup.map(x=>`<tr><td>${x.slot}</td><td>${x.name}</td><td>${x.team||'-'}</td><td>${x.projection}</td><td>${x.start_score}</td><td>${x.injury||'Healthy'}</td></tr>`).join('')}init()</script></body></html>"""
