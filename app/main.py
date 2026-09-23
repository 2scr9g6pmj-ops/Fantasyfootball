from contextlib import asynccontextmanager
import base64
import hashlib
import hmac
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes import router
from app.api.lineups import router as lineup_router
from app.api.drafts import router as draft_router
from app.api.yahoo import router as yahoo_router
from app.database import Base, engine
from app.config import get_settings


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        settings = get_settings()
        if (
            not settings.app_access_username
            or not settings.app_access_password
            or request.url.path in {"/health", "/login"}
        ):
            return await call_next(request)

        expected_session = hmac.new(
            settings.app_access_password.encode(),
            settings.app_access_username.encode(),
            hashlib.sha256,
        ).hexdigest()
        session = request.cookies.get("fantasyfootball_session", "")
        if hmac.compare_digest(session, expected_session):
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        try:
            scheme, encoded = authorization.split(" ", 1)
            username, password = base64.b64decode(encoded).decode().split(":", 1)
        except (ValueError, UnicodeDecodeError):
            scheme = username = password = ""
        valid = scheme.lower() == "basic" and hmac.compare_digest(username, settings.app_access_username) and hmac.compare_digest(password, settings.app_access_password)
        if not valid:
            if request.url.path.startswith("/api/"):
                return Response(status_code=401)
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)


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
app.add_middleware(BasicAuthMiddleware)
app.include_router(router)
app.include_router(lineup_router)
app.include_router(draft_router)
app.include_router(yahoo_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login_page(error: str | None = None):
    message = "<p class='error'>Incorrect username or password.</p>" if error else ""
    return f"""<!doctype html><html><head><title>Lineup Assistant Login</title><meta name='viewport' content='width=device-width'>
    <style>body{{font:16px system-ui;background:#0b1220;color:#e5edf7;display:grid;place-items:center;min-height:100vh;margin:0}}form{{background:#121d31;padding:2rem;border-radius:14px;width:min(360px,80vw)}}input,button{{box-sizing:border-box;width:100%;padding:.8rem;margin:.45rem 0;border-radius:8px;border:1px solid #405170}}input{{background:#17233a;color:white}}button{{background:#3478db;color:white;cursor:pointer}}.error{{color:#ff8d8d}}</style></head>
    <body><form method='post'><h1>Lineup Assistant</h1>{message}<label>Username<input name='username' autocomplete='username' required></label><label>Password<input name='password' type='password' autocomplete='current-password' required></label><button type='submit'>Sign in</button></form></body></html>"""


@app.post("/login")
async def login(request: Request):
    settings = get_settings()
    form = parse_qs((await request.body()).decode())
    username = form.get("username", [""])[0]
    password = form.get("password", [""])[0]
    if not (
        hmac.compare_digest(username, settings.app_access_username)
        and hmac.compare_digest(password, settings.app_access_password)
    ):
        return RedirectResponse("/login?error=1", status_code=303)
    session = hmac.new(password.encode(), username.encode(), hashlib.sha256).hexdigest()
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "fantasyfootball_session",
        session,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@app.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html><html><head><title>Lineup Assistant</title><meta name='viewport' content='width=device-width'>
    <style>body{font:16px system-ui;margin:auto;max-width:1100px;padding:2rem;background:#0b1220;color:#e5edf7}select,button,input{padding:.7rem;margin:.3rem;background:#17233a;color:white;border:1px solid #405170;border-radius:8px}button{cursor:pointer}.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:.35rem}.status{min-height:1.5rem;margin:.8rem .3rem;color:#aebbd0}.status.good{color:#65d99b}.status.error{color:#ff8d8d}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem}.card,table,.empty{background:#121d31;border-radius:12px;padding:1rem}table{width:100%;margin-top:1rem;border-collapse:collapse}td,th{padding:.7rem;border-bottom:1px solid #2b3952;text-align:left}.good{color:#65d99b}.empty{margin-top:1rem;color:#aebbd0}a{color:#78b5ff}@media(max-width:650px){body{padding:1rem}.toolbar>*{flex:1 1 100%}}</style></head>
    <body><h1>Sleeper Lineup Assistant</h1>
    <div class='toolbar'><button id='syncButton' onclick='syncSleeper()'>Sync Sleeper</button><input id='season' inputmode='numeric' maxlength='4' placeholder='Current season'><select id='league' aria-label='League'></select><input id='week' aria-label='Week' type='number' min='1' max='18' value='1'><select id='profile' aria-label='Recommendation profile'><option>balanced</option><option>conservative</option><option>upside</option></select><button id='analyzeButton' onclick='load()'>Analyze</button></div>
    <div id='status' class='status'>Loading synced leagues…</div><div class='cards' id='cards'></div><div id='empty' class='empty' hidden></div><table id='lineupTable' hidden><thead><tr><th>Slot</th><th>Player</th><th>Team</th><th>Projection</th><th>Start Score</th><th>Health</th></tr></thead><tbody id='lineup'></tbody></table><p><a href='/draft'>Draft Room</a> · <a href='/docs'>API & projection import</a></p>
    <script>
    const el=id=>document.getElementById(id);
    function showStatus(message,kind=''){el('status').textContent=message;el('status').className='status '+kind}
    async function jsonRequest(url,options){const response=await fetch(url,options);let payload;try{payload=await response.json()}catch{payload={detail:await response.text()}}if(!response.ok)throw new Error(payload.detail||`Request failed (${response.status})`);return payload}
    function setLeagues(leagues){const current=el('league').value;el('league').replaceChildren();for(const item of leagues){const option=document.createElement('option');option.value=item.league_id;option.textContent=`${item.name} (${item.season})`;el('league').append(option)}if([...el('league').options].some(x=>x.value===current))el('league').value=current;el('analyzeButton').disabled=!leagues.length;el('empty').hidden=Boolean(leagues.length);if(!leagues.length){el('empty').textContent='No leagues are synced yet. Choose a season if needed, then select Sync Sleeper.';el('lineupTable').hidden=true;el('cards').replaceChildren()}}
    async function refreshLeagues(analyze=true){const leagues=await jsonRequest('/api/leagues');setLeagues(leagues);if(leagues.length){showStatus(`${leagues.length} Sleeper league${leagues.length===1?'':'s'} ready.`,'good');if(analyze)await load()}return leagues}
    async function syncSleeper(){const button=el('syncButton');button.disabled=true;showStatus('Syncing CousinsFF leagues, rosters, and players from Sleeper…');try{const value=el('season').value.trim();const query=value?`?season=${encodeURIComponent(value)}`:'';const result=await jsonRequest('/api/sync'+query,{method:'POST'});await refreshLeagues(false);showStatus(`Sync complete: ${result.leagues_synced} leagues, ${result.rosters_synced} rosters, ${result.players_synced} players.`,'good');if(result.leagues_synced)await load()}catch(error){showStatus(error.message,'error')}finally{button.disabled=false}}
    async function load(){if(!el('league').value)return;showStatus('Calculating the best lineup…');try{const url=`/api/leagues/${encodeURIComponent(el('league').value)}/recommendations/${el('week').value}?profile=${encodeURIComponent(el('profile').value)}`;const result=await jsonRequest(url);el('cards').innerHTML=`<div class=card>Current<br><b>${result.current_projected_points}</b></div><div class=card>Optimized<br><b class=good>${result.optimized_projected_points}</b></div><div class=card>Changes<br><b>${result.changes.length}</b></div>`;el('lineup').replaceChildren();for(const player of result.recommended_lineup){const row=document.createElement('tr');for(const value of [player.slot,player.name,player.team||'-',player.projection,player.start_score,player.injury||'Healthy']){const cell=document.createElement('td');cell.textContent=value;row.append(cell)}el('lineup').append(row)}el('lineupTable').hidden=false;showStatus(`Week ${el('week').value} recommendations are ready.`,'good')}catch(error){el('lineupTable').hidden=true;showStatus(error.message,'error')}}
    refreshLeagues().catch(error=>showStatus(error.message,'error'));
    </script></body></html>"""


@app.get("/draft", response_class=HTMLResponse)
def draft_room():
    return """<!doctype html><html><head><title>Draft Room</title><meta name=viewport content='width=device-width'><style>body{font:15px system-ui;margin:auto;max-width:1200px;padding:24px;background:#09111f;color:#e8eef8}input,select,button,textarea{padding:9px;margin:4px;background:#14213a;color:white;border:1px solid #405170;border-radius:7px}button{cursor:pointer}.grid{display:grid;grid-template-columns:2fr 1fr;gap:16px}.panel{background:#111d31;padding:16px;border-radius:12px;margin:12px 0}table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #293956;text-align:left}.Draft{color:#62dda0}.Wait{color:#ffd166}.Target{color:#8bb9ff}.ok{color:#62dda0}@media(max-width:800px){.grid{grid-template-columns:1fr}}</style></head><body><h1>Live Draft Room</h1><div class=panel><input id=sid placeholder='Session ID'><button onclick=load()>Open</button><button onclick=sync()>Sync platform</button><button onclick=move('undo')>Undo</button><button onclick=move('redo')>Redo</button><span id=clock></span><details><summary>Create a manual session</summary><textarea id=setup rows=6 style='width:90%'>{"user_team_id":"1","platform":"manual","players":[],"settings":{"teams":12,"roster_positions":["QB","RB","RB","WR","WR","TE","FLEX"]}}</textarea><button onclick=createDraft()>Create</button></details></div><div class=panel><h2>Yahoo connection</h2><p>Authorize read-only Fantasy Sports access, then import one of your leagues.</p><button onclick=connectYahoo()>Connect Yahoo</button><span id=yahooStatus></span><div id=yahooLeagues></div></div><div class=grid><section><div class=panel><h2>Top recommendations</h2><table><thead><tr><th>Player</th><th>Pos/Tier</th><th>Score</th><th>Scarcity</th><th>Guidance</th><th>Survives</th><th>Why</th></tr></thead><tbody id=recs></tbody></table></div></section><aside><div class=panel><h2>Live input</h2><select id=etype><option value=pick>Drafted player</option><option value=keeper>Keeper</option><option value=set_clock>Pick / round</option><option value=set_roster>User/team roster</option><option value=pool>Player pool</option><option value=player_override>ADP / ranking / injury / projection</option><option value=settings_override>League / scoring / roster settings</option></select><textarea id=payload rows=9 style='width:90%' placeholder='JSON payload'></textarea><button onclick=eventAdd()>Apply & recalculate</button></div><div class=panel><h2>Your roster</h2><pre id=roster></pre><h2>History</h2><div id=history></div></div></aside></div><script>let data,yahooConnection;const yahooMappings=[{league_id:'174457',team_id:'4'},{league_id:'396381',team_id:'3'},{league_id:'429589',team_id:'8'}];async function req(path,opt){let r=await fetch('/api/drafts/'+sid.value+path,opt);if(!r.ok)alert(await r.text());else render(await r.json())}async function createDraft(){let r=await fetch('/api/drafts',{method:'POST',headers:{'Content-Type':'application/json'},body:setup.value});if(!r.ok)alert(await r.text());else{let x=await r.json();sid.value=x.id;render(x)}}async function connectYahoo(){let r=await fetch('/api/yahoo/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({leagues:yahooMappings})});let x=await r.json();if(!r.ok){alert(x.detail||'Yahoo is not configured');return}location.href=x.authorization_url}async function loadYahoo(id){yahooConnection=id;let r=await fetch('/api/yahoo/connections/'+id),x=await r.json();yahooStatus.textContent=x.connected?' Connected':' '+(x.last_error||x.status);yahooStatus.className=x.connected?'ok':'';yahooLeagues.innerHTML=x.leagues.map(l=>`<button onclick="importYahoo('${l.league_id}')">${l.name||'League '+l.league_id} · Team ${l.team_id}</button>`).join('')}async function importYahoo(league){let r=await fetch(`/api/yahoo/connections/${yahooConnection}/leagues/${league}/draft`,{method:'POST'}),x=await r.json();if(!r.ok){alert(x.detail);return}sid.value=x.session_id;history.replaceState({},'',x.draft_url);load()}function load(){req('')}function move(x){req('/'+x,{method:'POST'})}function sync(){req('/sync',{method:'POST'})}function eventAdd(){req('/events',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:etype.value,payload:JSON.parse(payload.value||'{}')})})}function render(x){data=x;clock.textContent=` Pick ${x.state.current_pick}, Round ${x.state.current_round}`;recs.innerHTML=x.recommendations.map(p=>`<tr><td>${p.name||p.player_id}</td><td>${p.position||'-'} / ${p.tier||'-'}</td><td>${p.score}</td><td>${p.tier_scarcity}</td><td class='${p.guidance}'>${p.guidance}</td><td>${p.survive_next_pick_pct}%</td><td>${p.explanation}</td></tr>`).join('');roster.textContent=JSON.stringify(x.roster,null,2);history.innerHTML=x.history.slice().reverse().map(e=>`<div>${e.applied?'●':'○'} ${e.sequence}. ${e.type} <small>${e.source}</small></div>`).join('')}let params=new URLSearchParams(location.search);if(params.get('yahoo_connection'))loadYahoo(params.get('yahoo_connection'));if(params.get('session')){sid.value=params.get('session');load()}if(params.get('yahoo_error'))yahooStatus.textContent=' Connection failed: '+params.get('yahoo_error');</script></body></html>"""
