import os
import time
import uuid
import json
import hmac
import secrets
import sqlite3
import smtplib
import csv
import io
from datetime import timedelta
from contextlib import closing
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response, session
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from openai import OpenAI

load_dotenv()

try:
    import stripe
except Exception:
    stripe = None


# =========================
# CONFIG
# =========================
APP_VERSION = "v26.0"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

PORT = int(os.getenv("PORT", "5000"))
DB_PATH = os.getenv("DB_PATH", "assistify.db").strip()

SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32)).strip()
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax").strip()
PERMANENT_SESSION_LIFETIME_HOURS = int(os.getenv("PERMANENT_SESSION_LIFETIME_HOURS", "24"))

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]

ENABLE_LEAD_CAPTURE = os.getenv("ENABLE_LEAD_CAPTURE", "true").lower() == "true"
ENABLE_HISTORY = os.getenv("ENABLE_HISTORY", "true").lower() == "true"

RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "30"))

MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "4000"))
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "6"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "400"))
MAX_ASSISTANT_REPLY_CHARS = int(os.getenv("MAX_ASSISTANT_REPLY_CHARS", "5000"))
OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))

TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", "72"))

PRICE_STARTER_EUR = int(os.getenv("PRICE_STARTER_EUR", "49"))
PRICE_PRO_EUR = int(os.getenv("PRICE_PRO_EUR", "149"))
PRICE_AGENCY_EUR = int(os.getenv("PRICE_AGENCY_EUR", "399"))

DEFAULT_COMPANY_NAME = os.getenv("COMPANY_NAME", "Assistify AI").strip()
DEFAULT_COMPANY_TONE = os.getenv(
    "COMPANY_TONE",
    "vriendelijk, duidelijk, professioneel en behulpzaam",
).strip()
DEFAULT_COMPANY_DESCRIPTION = os.getenv(
    "COMPANY_DESCRIPTION",
    "Wij helpen bedrijven met AI klantenservice, automatische support en snelle beantwoording van klantvragen.",
).strip()
DEFAULT_SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "support@jouwdomein.nl").strip()
DEFAULT_SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+31 6 00000000").strip()
DEFAULT_WEBSITE_URL = os.getenv("WEBSITE_URL", "https://jouwdomein.nl").strip()
DEFAULT_FAQ_CONTEXT = os.getenv(
    "FAQ_CONTEXT",
    """
- Openingstijden support: maandag t/m vrijdag van 09:00 tot 17:00.
- Reactietijd per e-mail: meestal binnen 24 uur.
- Demo aanvragen kan via de website.
- Prijzen verschillen per pakket en gebruik.
- Technische support loopt via e-mail of het contactformulier.
- Bij complexe problemen moet de klant naam, e-mail en probleemomschrijving achterlaten.
""".strip(),
)
DEFAULT_WIDGET_COLOR = os.getenv("DEFAULT_WIDGET_COLOR", "#6d5efc").strip()
DEFAULT_TENANT_API_KEY = os.getenv("DEFAULT_TENANT_API_KEY", "default-demo-key").strip()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "").strip()

PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip()

SMTP_ENABLED = os.getenv("SMTP_ENABLED", "false").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", DEFAULT_SUPPORT_EMAIL).strip()

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "").strip()
STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", "").strip()
STRIPE_PRICE_STARTER_MONTHLY = os.getenv("STRIPE_PRICE_STARTER_MONTHLY", "").strip()
STRIPE_PRICE_PRO_MONTHLY = os.getenv("STRIPE_PRICE_PRO_MONTHLY", "").strip()
STRIPE_PRICE_AGENCY_MONTHLY = os.getenv("STRIPE_PRICE_AGENCY_MONTHLY", "").strip()

if stripe and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

rate_limit_store = {}
_startup_done = False


# =========================
# APP
# =========================
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = SESSION_COOKIE_SECURE
app.config["SESSION_COOKIE_SAMESITE"] = SESSION_COOKIE_SAMESITE
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    hours=PERMANENT_SESSION_LIFETIME_HOURS
)

if ALLOWED_ORIGINS == ["*"]:
    CORS(
        app,
        resources={
            r"/widget/*": {"origins": "*"},
            r"/signup/*": {"origins": "*"},
            r"/dashboard/*": {"origins": "*"},
            r"/invite/*": {"origins": "*"},
            r"/reset-password/*": {"origins": "*"},
            r"/admin/*": {"origins": "*"},
            r"/stripe/*": {"origins": "*"},
        },
        supports_credentials=False,
    )
else:
    CORS(
        app,
        resources={r"/*": {"origins": ALLOWED_ORIGINS}},
        supports_credentials=True,
    )


# =========================
# DATABASE
# =========================
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db():
    with closing(get_db()) as conn:
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                api_key TEXT NOT NULL UNIQUE,
                company_tone TEXT NOT NULL,
                company_description TEXT NOT NULL,
                support_email TEXT NOT NULL,
                support_phone TEXT NOT NULL,
                website_url TEXT NOT NULL,
                faq_context TEXT NOT NULL,
                plan_name TEXT NOT NULL DEFAULT 'starter',
                subscription_status TEXT NOT NULL DEFAULT 'active',
                monthly_message_limit INTEGER NOT NULL DEFAULT 500,
                stripe_customer_id TEXT DEFAULT '',
                stripe_subscription_id TEXT DEFAULT '',
                billing_email TEXT DEFAULT '',
                billing_cycle TEXT DEFAULT 'monthly',
                widget_color TEXT NOT NULL DEFAULT '#6d5efc',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_users (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                email TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL DEFAULT '',
                is_owner INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                UNIQUE(tenant_id, email),
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                message TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                month_key TEXT NOT NULL,
                meta_json TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            )
            """
        )

        conn.commit()


def seed_default_tenant():
    with closing(get_db()) as conn:
        existing = conn.execute(
            "SELECT id FROM tenants WHERE slug = ?",
            ("default",),
        ).fetchone()

        if existing:
            return

        tenant_id = str(uuid.uuid4())

        conn.execute(
            """
            INSERT INTO tenants (
                id, name, slug, api_key, company_tone, company_description,
                support_email, support_phone, website_url, faq_context,
                plan_name, subscription_status, monthly_message_limit,
                stripe_customer_id, stripe_subscription_id, billing_email,
                billing_cycle, widget_color, is_active, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                DEFAULT_COMPANY_NAME,
                "default",
                DEFAULT_TENANT_API_KEY,
                DEFAULT_COMPANY_TONE,
                DEFAULT_COMPANY_DESCRIPTION,
                DEFAULT_SUPPORT_EMAIL,
                DEFAULT_SUPPORT_PHONE,
                DEFAULT_WEBSITE_URL,
                DEFAULT_FAQ_CONTEXT,
                "starter",
                "active",
                500,
                "",
                "",
                DEFAULT_SUPPORT_EMAIL,
                "monthly",
                DEFAULT_WIDGET_COLOR,
                1,
                int(time.time()),
            ),
        )

        conn.execute(
            """
            INSERT INTO customer_users (
                id, tenant_id, email, password_hash, full_name,
                is_owner, is_active, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                tenant_id,
                DEFAULT_SUPPORT_EMAIL,
                generate_password_hash("changeme123"),
                "Default Owner",
                1,
                1,
                int(time.time()),
            ),
        )

        conn.commit()


def ensure_startup():
    global _startup_done
    if _startup_done:
        return

    init_db()
    seed_default_tenant()
    _startup_done = True# =========================
# HELPERS
# =========================

def now_ts() -> int:
    return int(time.time())


def clamp_text(value: str, max_len: int) -> str:
    text = (value or "").strip()
    return text[:max_len] if len(text) > max_len else text


def validate_email(email: str) -> bool:
    email = (email or "").strip()
    return "@" in email and "." in email and len(email) >= 6


def normalize_slug(value: str) -> str:
    value = (value or "").strip().lower()
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-"

    result = []
    for ch in value.replace(" ", "-").replace("_", "-"):
        if ch in allowed:
            result.append(ch)

    slug = "".join(result).strip("-")

    while "--" in slug:
        slug = slug.replace("--", "-")

    return slug[:60]


def generate_api_key() -> str:
    return "tenant_" + secrets.token_urlsafe(24)


def json_body():
    return request.get_json(silent=True) or {}


def get_client_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.remote_addr or "unknown"


# =========================
# RATE LIMIT
# =========================

def cleanup_rate_limit_store():

    now = time.time()

    expired = []

    for key, timestamps in list(rate_limit_store.items()):

        fresh = [ts for ts in timestamps if now - ts <= RATE_LIMIT_WINDOW_SECONDS]

        if fresh:
            rate_limit_store[key] = fresh
        else:
            expired.append(key)

    for key in expired:
        rate_limit_store.pop(key, None)


def is_rate_limited(ip: str, tenant_key: str) -> bool:

    cleanup_rate_limit_store()

    now = time.time()

    key = f"{tenant_key}:{ip}"

    rate_limit_store.setdefault(key, [])

    recent = [ts for ts in rate_limit_store[key] if now - ts <= RATE_LIMIT_WINDOW_SECONDS]

    rate_limit_store[key] = recent

    if len(recent) >= RATE_LIMIT_MAX_REQUESTS:
        return True

    rate_limit_store[key].append(now)

    return False


# =========================
# DATABASE HELPERS
# =========================

def get_tenant_by_api_key(api_key: str):

    with closing(get_db()) as conn:

        row = conn.execute(
            "SELECT * FROM tenants WHERE api_key = ? AND is_active = 1",
            (api_key,),
        ).fetchone()

    return dict(row) if row else None


def get_tenant_by_slug(slug: str):

    with closing(get_db()) as conn:

        row = conn.execute(
            "SELECT * FROM tenants WHERE slug = ?",
            (slug,),
        ).fetchone()

    return dict(row) if row else None


def get_tenant_by_id(tenant_id: str):

    with closing(get_db()) as conn:

        row = conn.execute(
            "SELECT * FROM tenants WHERE id = ?",
            (tenant_id,),
        ).fetchone()

    return dict(row) if row else None


# =========================
# MESSAGE HISTORY
# =========================

def save_message(tenant_id: str, session_id: str, role: str, content: str):

    if not ENABLE_HISTORY:
        return

    with closing(get_db()) as conn:

        conn.execute(
            """
            INSERT INTO messages (id, tenant_id, session_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                tenant_id,
                session_id,
                role,
                clamp_text(content, 20000),
                now_ts(),
            ),
        )

        conn.commit()


def build_openai_input(tenant_id: str, session_id: str, user_message: str):

    messages = []

    if ENABLE_HISTORY:

        with closing(get_db()) as conn:

            rows = conn.execute(
                """
                SELECT role, content
                FROM messages
                WHERE tenant_id = ? AND session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (tenant_id, session_id, MAX_HISTORY_MESSAGES),
            ).fetchall()

        for row in reversed(rows):

            messages.append(
                {
                    "role": row["role"],
                    "content": [{"type": "input_text", "text": row["content"]}],
                }
            )

    messages.append(
        {
            "role": "user",
            "content": [{"type": "input_text", "text": user_message}],
        }
    )

    return messages


# =========================
# AI PROMPT
# =========================

def build_system_prompt(tenant: dict) -> str:

    return f"""
Je bent de AI klantenservice-assistent van {tenant['name']}.

Gebruik een {tenant['company_tone']} toon.

Bedrijfsomschrijving:
{tenant['company_description']}

FAQ:
{tenant['faq_context']}

Contact:
Email: {tenant['support_email']}
Telefoon: {tenant['support_phone']}
Website: {tenant['website_url']}

Antwoorden moeten:
- kort
- duidelijk
- behulpzaam
- niet verzonnen
""".strip()


# =========================
# AI CALL
# =========================

def extract_response_text(response):

    text = getattr(response, "output_text", None)

    if isinstance(text, str) and text.strip():
        return clamp_text(text.strip(), MAX_ASSISTANT_REPLY_CHARS)

    try:

        output = getattr(response, "output", []) or []

        parts = []

        for item in output:

            content = getattr(item, "content", []) or []

            for part in content:

                t = getattr(part, "text", None)

                if isinstance(t, str):
                    parts.append(t)

        return clamp_text("\n".join(parts), MAX_ASSISTANT_REPLY_CHARS)

    except Exception:
        return ""


def ask_ai(tenant: dict, session_id: str, user_message: str):

    if not client:
        raise RuntimeError("OPENAI_API_KEY ontbreekt")

    try:

        response = client.responses.create(
            model=MODEL_NAME,
            instructions=build_system_prompt(tenant),
            input=build_openai_input(tenant["id"], session_id, user_message),
            max_output_tokens=MAX_OUTPUT_TOKENS,
            timeout=OPENAI_TIMEOUT_SECONDS,
        )

        return extract_response_text(response)

    except Exception as e:

        text = str(e).lower()

        if "timeout" in text or "timed out" in text:
            return "Sorry, het antwoord duurde te lang. Probeer het opnieuw."

        raise# =========================
# HTML SHELL
# =========================

def render_shell(title: str, body: str) -> str:

    return f"""
<!DOCTYPE html>
<html lang="nl">

<head>

<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<title>{title}</title>

<style>

body {{
font-family: Inter, Arial;
margin:0;
background:#08101f;
color:#fff;
}}

.wrap {{
max-width:1200px;
margin:auto;
padding:30px;
}}

.topnav {{
display:flex;
justify-content:space-between;
margin-bottom:30px;
}}

button {{
padding:12px 18px;
border:none;
border-radius:10px;
background:#6d5efc;
color:white;
cursor:pointer;
font-weight:600;
}}

input, textarea {{
width:100%;
padding:12px;
margin-bottom:10px;
border-radius:8px;
border:1px solid #333;
background:#0f1b33;
color:white;
}}

.card {{
background:#0f1b33;
padding:20px;
border-radius:14px;
margin-bottom:20px;
}}

</style>

</head>

<body>

<div class="wrap">

<div class="topnav">

<div>
<b>Assistify AI</b>
</div>

<div>

<a href="/"><button>Home</button></a>
<a href="/signup"><button>Signup</button></a>
<a href="/dashboard/login"><button>Dashboard</button></a>

</div>

</div>

{body}

</div>

</body>
</html>
"""


# =========================
# HOMEPAGE
# =========================

def render_homepage_html():

    body = f"""

<div class="card">

<h1>Assistify AI</h1>

<p>
AI klantenservice voor websites.
</p>

<button onclick="window.location.href='/signup'">
Start nu
</button>

</div>


<div class="card">

<h3>Starter</h3>
<p>€{PRICE_STARTER_EUR}/maand</p>

</div>

<div class="card">

<h3>Pro</h3>
<p>€{PRICE_PRO_EUR}/maand</p>

</div>

<div class="card">

<h3>Agency</h3>
<p>€{PRICE_AGENCY_EUR}/maand</p>

</div>

"""

    return render_shell("Assistify AI", body)


# =========================
# SIGNUP PAGE
# =========================

def render_signup_html():

    body = """

<div class="card">

<h2>Signup</h2>

<input id="email" placeholder="email@bedrijf.nl">

<button onclick="startSignup('starter')">
Start Starter
</button>

<button onclick="startSignup('pro')">
Start Pro
</button>

<button onclick="startSignup('agency')">
Start Agency
</button>

<p id="status"></p>

</div>


<script>

async function startSignup(plan){

status.innerText="Bezig..."

try{

let res = await fetch("/signup/create-checkout",{

method:"POST",

headers:{
"Content-Type":"application/json"
},

body:JSON.stringify({

email:email.value.trim(),

plan_name:plan

})

})

let data = await res.json()

if(!res.ok) throw new Error(data.error)

window.location.href=data.url

}catch(e){

status.innerText=e.message

}

}

</script>

"""

    return render_shell("Signup", body)


# =========================
# DASHBOARD LOGIN
# =========================

def render_dashboard_login_html():

    body = """

<div class="card">

<h2>Dashboard login</h2>

<input id="tenant_slug" placeholder="tenant slug">

<input id="email" placeholder="email">

<input id="password" type="password" placeholder="wachtwoord">

<button onclick="login()">Login</button>

<p id="status"></p>

</div>


<script>

async function login(){

status.innerText="Bezig..."

try{

let res = await fetch("/dashboard/login",{

method:"POST",

headers:{

"Content-Type":"application/json"

},

credentials:"include",

body:JSON.stringify({

tenant_slug:tenant_slug.value,

email:email.value,

password:password.value

})

})

let data = await res.json()

if(!res.ok) throw new Error(data.error)

window.location.href="/dashboard"

}catch(e){

status.innerText=e.message

}

}

</script>

"""

    return render_shell("Dashboard login", body)# =========================
# ONBOARDING / EXTRA HTML
# =========================

def render_onboarding_html(token: str, tenant: dict):
    host = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/")
    color = tenant.get("widget_color") or DEFAULT_WIDGET_COLOR

    embed_code = f"""<script src="{host}/widget.js" data-api-base="{host}" data-tenant-key="{tenant["api_key"]}" data-title="{tenant["name"]}" data-color="{color}"></script>"""

    body = f"""

<div class="card">
    <h2>Onboarding</h2>

    <input id="full_name" placeholder="Jouw naam">
    <input id="password" type="password" placeholder="Nieuw wachtwoord">
    <input id="name" value="{tenant['name']}" placeholder="Bedrijfsnaam">
    <input id="slug" value="{tenant['slug']}" placeholder="Slug">
    <input id="support_email" value="{tenant['support_email']}" placeholder="Support e-mail">
    <input id="website_url" value="{tenant['website_url']}" placeholder="Website URL">
    <textarea id="company_description" placeholder="Bedrijfsomschrijving">{tenant['company_description']}</textarea>

    <button onclick="saveSetup()">Opslaan</button>
    <p id="status"></p>
</div>

<div class="card">
    <h3>API key</h3>
    <pre id="api_key">{tenant["api_key"]}</pre>

    <h3>Embed code</h3>
    <pre id="embed">{embed_code}</pre>
</div>

<script>
async function saveSetup(){{
    status.innerText = "Bezig...";

    try {{
        const res = await fetch("/signup/complete/{token}", {{
            method: "POST",
            headers: {{
                "Content-Type": "application/json"
            }},
            body: JSON.stringify({{
                full_name: full_name.value.trim(),
                password: password.value.trim(),
                name: name.value.trim(),
                slug: slug.value.trim(),
                support_email: support_email.value.trim(),
                website_url: website_url.value.trim(),
                company_description: company_description.value.trim()
            }})
        }});

        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Er ging iets mis.");

        api_key.textContent = data.tenant.api_key || "";
        embed.textContent = data.embed_code || "";
        status.innerHTML = 'Opgeslagen. Login via <a href="/dashboard/login">/dashboard/login</a>';
    }} catch(e) {{
        status.innerText = e.message;
    }}
}}
</script>
"""
    return render_shell("Onboarding", body)


def render_dashboard_html():
    body = """

<div class="card">
    <h2>Dashboard</h2>
    <p>Beheer je Assistify omgeving.</p>

    <button onclick="logout()">Uitloggen</button>
</div>

<div class="card">
    <h3>Instellingen</h3>

    <input id="name" placeholder="Bedrijfsnaam">
    <input id="support_email" placeholder="Support e-mail">
    <input id="website_url" placeholder="Website URL">
    <textarea id="company_description" placeholder="Bedrijfsomschrijving"></textarea>
    <textarea id="faq_context" placeholder="FAQ context"></textarea>

    <button onclick="saveSettings()">Opslaan</button>
    <p id="saveStatus"></p>
</div>

<div class="card">
    <h3>Integratie</h3>
    <pre id="apiKeyBox"></pre>
    <pre id="embedBox"></pre>
</div>

<div class="card">
    <h3>Leads</h3>
    <button onclick="loadLeads()">Leads laden</button>
    <div id="leadsBox"></div>
</div>

<div class="card">
    <h3>Chatsessies</h3>
    <button onclick="loadSessions()">Sessies laden</button>
    <div id="sessionsBox"></div>
</div>

<div class="card">
    <h3>Transcript</h3>
    <div id="transcriptBox"></div>
</div>

<script>
function esc(value){
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

async function api(path, options = {}){
    const headers = options.headers || {};
    if (!headers["Content-Type"] && !(options.body instanceof FormData)) {
        headers["Content-Type"] = "application/json";
    }

    const res = await fetch(path, {
        ...options,
        headers,
        credentials: "include"
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Er ging iets mis.");
    return data;
}

function formatDate(ts){
    return ts ? new Date(ts * 1000).toLocaleString() : "-";
}

async function loadDashboard(){
    const me = await api("/dashboard/me");
    const tenant = me.tenant || {};

    name.value = tenant.name || "";
    support_email.value = tenant.support_email || "";
    website_url.value = tenant.website_url || "";
    company_description.value = tenant.company_description || "";
    faq_context.value = tenant.faq_context || "";

    apiKeyBox.textContent = tenant.api_key || "";
    embedBox.textContent = me.embed_code || "";
}

async function saveSettings(){
    saveStatus.innerText = "Bezig...";

    try {
        const data = await api("/dashboard/settings", {
            method: "POST",
            body: JSON.stringify({
                name: name.value.trim(),
                support_email: support_email.value.trim(),
                website_url: website_url.value.trim(),
                company_description: company_description.value.trim(),
                faq_context: faq_context.value.trim()
            })
        });

        apiKeyBox.textContent = data.tenant.api_key || "";
        embedBox.textContent = data.embed_code || "";
        saveStatus.innerText = "Opgeslagen.";
    } catch(e) {
        saveStatus.innerText = e.message;
    }
}

async function loadLeads(){
    try {
        const data = await api("/dashboard/leads");
        leadsBox.innerHTML = (data.leads || []).map(l => `
            <div class="card">
                <b>${esc(l.name)}</b><br>
                ${esc(l.email)}<br>
                ${esc(l.phone || "-")}<br>
                ${esc(formatDate(l.created_at))}<br><br>
                ${esc(l.message)}
            </div>
        `).join("") || "<p>Geen leads gevonden.</p>";
    } catch(e) {
        leadsBox.innerHTML = "<p>" + esc(e.message) + "</p>";
    }
}

async function loadSessions(){
    try {
        const data = await api("/dashboard/chat/sessions");
        sessionsBox.innerHTML = (data.sessions || []).map(s => `
            <div class="card">
                <b>${esc(s.session_id)}</b><br>
                ${esc(String(s.total_messages))} berichten<br>
                ${esc(formatDate(s.last_message_at))}<br><br>
                <button onclick="loadTranscript('${encodeURIComponent(s.session_id)}')">Open transcript</button>
            </div>
        `).join("") || "<p>Geen sessies gevonden.</p>";
    } catch(e) {
        sessionsBox.innerHTML = "<p>" + esc(e.message) + "</p>";
    }
}

async function loadTranscript(encodedSessionId){
    const sessionId = decodeURIComponent(encodedSessionId);
    transcriptBox.innerHTML = "<p>Bezig...</p>";

    try {
        const data = await api("/dashboard/chat/session/" + encodeURIComponent(sessionId));
        transcriptBox.innerHTML = (data.messages || []).map(m => `
            <div class="card">
                <b>${esc(m.role)}</b><br>
                ${esc(formatDate(m.created_at))}<br><br>
                <div style="white-space:pre-wrap;">${esc(m.content)}</div>
            </div>
        `).join("") || "<p>Geen berichten gevonden.</p>";
    } catch(e) {
        transcriptBox.innerHTML = "<p>" + esc(e.message) + "</p>";
    }
}

async function logout(){
    await api("/dashboard/logout", {
        method: "POST",
        body: JSON.stringify({})
    });
    window.location.href = "/dashboard/login";
}

(async () => {
    try {
        await loadDashboard();
    } catch(e) {
        window.location.href = "/dashboard/login";
    }
})();
</script>
"""
    return render_shell("Dashboard", body)


def render_admin_html():
    body = """

<div id="loginView" class="card">
    <h2>Admin login</h2>
    <input id="u" placeholder="admin">
    <input id="p" type="password" placeholder="wachtwoord">
    <button onclick="login()">Inloggen</button>
    <p id="status"></p>
</div>

<div id="appView" style="display:none;">
    <div class="card">
        <h2>Admin dashboard</h2>
        <div id="overview"></div>
    </div>

    <div class="card">
        <h3>Tenants</h3>
        <div id="tenants"></div>
    </div>

    <div class="card">
        <h3>Audit logs</h3>
        <div id="auditLogs"></div>
    </div>
</div>

<script>
async function api(path, options = {}){
    const headers = options.headers || {};
    if (!headers["Content-Type"] && !(options.body instanceof FormData)) {
        headers["Content-Type"] = "application/json";
    }

    const res = await fetch(path, {
        ...options,
        headers,
        credentials: "include"
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Er ging iets mis.");
    return data;
}

function formatDate(ts){
    return ts ? new Date(ts * 1000).toLocaleString() : "-";
}

async function login(){
    try {
        await api("/admin/login", {
            method: "POST",
            body: JSON.stringify({
                username: u.value.trim(),
                password: p.value.trim()
            })
        });
        load();
    } catch(e) {
        status.innerText = e.message;
    }
}

async function load(){
    loginView.style.display = "none";
    appView.style.display = "block";

    const s = await api("/admin/stats/overview");
    overview.innerHTML = `
        <p>Tenants: ${s.stats.tenant_count}</p>
        <p>Leads: ${s.stats.lead_count_total}</p>
        <p>Berichten: ${s.stats.message_count_total}</p>
        <p>Sessies: ${s.stats.session_count_total}</p>
    `;

    const t = await api("/admin/tenants");
    tenants.innerHTML = (t.tenants || []).map(x => `
        <div class="card">
            <b>${x.name}</b><br>
            ${x.slug}<br>
            ${x.plan_name}<br>
            ${x.subscription_status}
        </div>
    `).join("");

    const logs = await api("/admin/audit-logs");
    auditLogs.innerHTML = (logs.logs || []).map(x => `
        <div class="card">
            ${formatDate(x.created_at)}<br>
            ${x.actor_type}:${x.actor_id || "-"}<br>
            ${x.action}<br>
            ${x.target_type}:${x.target_id || "-"}
        </div>
    `).join("");
}

(async () => {
    try {
        await api("/admin/me");
        load();
    } catch(e) {}
})();
</script>
"""
    return render_shell("Admin", body)


def render_invite_accept_html(token: str, invite_row: dict):
    body = f"""

<div class="card">
    <h2>Team uitnodiging</h2>

    <input id="full_name" value="{invite_row.get('full_name', '')}" placeholder="Naam">
    <input id="email" value="{invite_row.get('email', '')}" placeholder="E-mail">
    <input id="password" type="password" placeholder="Wachtwoord">

    <button onclick="acceptInvite()">Account maken</button>
    <p id="status"></p>
</div>

<script>
async function acceptInvite(){{
    status.innerText = "Bezig...";

    try {{
        const res = await fetch("/invite/{token}", {{
            method: "POST",
            headers: {{
                "Content-Type": "application/json"
            }},
            body: JSON.stringify({{
                full_name: full_name.value.trim(),
                email: email.value.trim(),
                password: password.value.trim()
            }})
        }});

        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Er ging iets mis.");

        status.innerHTML = 'Account aangemaakt. Login via <a href="/dashboard/login">/dashboard/login</a>';
    }} catch(e) {{
        status.innerText = e.message;
    }}
}}
</script>
"""
    return render_shell("Invite", body)


def render_reset_request_html():
    body = """

<div class="card">
    <h2>Wachtwoord reset aanvragen</h2>

    <input id="email" placeholder="jij@bedrijf.nl">
    <button onclick="requestReset()">Reset aanvragen</button>
    <pre id="result"></pre>
</div>

<script>
async function requestReset(){
    try {
        const res = await fetch("/reset-password/request", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                email: email.value.trim()
            })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Er ging iets mis.");

        result.textContent = JSON.stringify(data, null, 2);
    } catch(e) {
        result.textContent = e.message;
    }
}
</script>
"""
    return render_shell("Reset aanvragen", body)


def render_reset_password_html(token: str):
    body = f"""

<div class="card">
    <h2>Nieuw wachtwoord</h2>

    <input id="password" type="password" placeholder="Nieuw wachtwoord">
    <button onclick="saveNewPassword()">Opslaan</button>
    <p id="status"></p>
</div>

<script>
async function saveNewPassword(){{
    status.innerText = "Bezig...";

    try {{
        const res = await fetch("/reset-password/{token}", {{
            method: "POST",
            headers: {{
                "Content-Type": "application/json"
            }},
            body: JSON.stringify({{
                password: password.value.trim()
            }})
        }});

        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Er ging iets mis.");

        status.innerHTML = 'Wachtwoord opgeslagen. Login via <a href="/dashboard/login">/dashboard/login</a>';
    }} catch(e) {{
        status.innerText = e.message;
    }}
}}
</script>
"""
    return render_shell("Nieuw wachtwoord", body)# =========================
# HOOKS
# =========================

@app.before_request
def before_any_request():
    ensure_startup()


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


# =========================
# AUTH HELPERS
# =========================

def require_admin() -> bool:
    return bool(session.get("admin_logged_in"))


def require_customer() -> bool:
    return bool(session.get("customer_logged_in")) and bool(session.get("customer_tenant_id"))


def admin_forbidden():
    return jsonify({"ok": False, "error": "Niet geautoriseerd."}), 401


def customer_forbidden():
    return jsonify({"ok": False, "error": "Niet geautoriseerd."}), 401


# =========================
# PUBLIC ROUTES
# =========================

@app.route("/", methods=["GET"])
def home():
    return Response(render_homepage_html(), mimetype="text/html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "version": APP_VERSION,
        "openai_configured": bool(OPENAI_API_KEY),
        "model": MODEL_NAME,
        "database": DB_PATH,
        "stripe_configured": bool(stripe and STRIPE_SECRET_KEY),
        "startup_done": _startup_done,
    })


@app.route("/version", methods=["GET"])
def version():
    return jsonify({
        "ok": True,
        "version": APP_VERSION,
        "model": MODEL_NAME,
    })


@app.route("/signup", methods=["GET"])
def signup_page():
    return Response(render_signup_html(), mimetype="text/html")


@app.route("/dashboard/login", methods=["GET"])
def dashboard_login_page():
    return Response(render_dashboard_login_html(), mimetype="text/html")


@app.route("/dashboard", methods=["GET"])
def dashboard_page():
    return Response(render_dashboard_html(), mimetype="text/html")


@app.route("/admin", methods=["GET"])
def admin_page():
    return Response(render_admin_html(), mimetype="text/html")


@app.route("/reset-password/request", methods=["GET"])
def reset_request_page():
    return Response(render_reset_request_html(), mimetype="text/html")


@app.route("/reset-password/<token>", methods=["GET"])
def reset_password_page(token):
    return Response(render_reset_password_html(token), mimetype="text/html")


# =========================
# ADMIN AUTH
# =========================

@app.route("/admin/login", methods=["POST"])
def admin_login():
    data = json_body()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    valid = False

    if ADMIN_PASSWORD_HASH:
        try:
            valid = username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password)
        except Exception:
            valid = False
    else:
        valid = username == ADMIN_USERNAME and bool(ADMIN_PASSWORD) and hmac.compare_digest(ADMIN_PASSWORD, password)

    if not valid:
        return jsonify({"ok": False, "error": "Ongeldige inloggegevens."}), 401

    session.clear()
    session["admin_logged_in"] = True
    session["admin_username"] = username
    session.permanent = True

    return jsonify({"ok": True, "message": "Ingelogd."})


@app.route("/admin/me", methods=["GET"])
def admin_me():
    if not require_admin():
        return admin_forbidden()

    return jsonify({
        "ok": True,
        "admin": {
            "username": session.get("admin_username", ADMIN_USERNAME)
        }
    })


# =========================
# CUSTOMER AUTH
# =========================

@app.route("/dashboard/login", methods=["POST"])
def dashboard_login():
    data = json_body()
    tenant_slug = (data.get("tenant_slug") or "").strip().lower()
    email = (data.get("email") or "").strip()
    password = (data.get("password") or "").strip()

    if not tenant_slug or not email or not password:
        return jsonify({
            "ok": False,
            "error": "Tenant slug, e-mail en wachtwoord zijn verplicht."
        }), 400

    tenant = get_tenant_by_slug(tenant_slug)
    if not tenant:
        return jsonify({"ok": False, "error": "Ongeldige inloggegevens."}), 401

    with closing(get_db()) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM customer_users
            WHERE tenant_id = ? AND LOWER(email) = LOWER(?)
            LIMIT 1
            """,
            (tenant["id"], email),
        ).fetchone()

    if not row:
        return jsonify({"ok": False, "error": "Ongeldige inloggegevens."}), 401

    user = dict(row)

    if not check_password_hash(user["password_hash"], password):
        return jsonify({"ok": False, "error": "Ongeldige inloggegevens."}), 401

    if int(user.get("is_active", 0)) != 1:
        return jsonify({"ok": False, "error": "Account is niet actief."}), 403

    session.clear()
    session["customer_logged_in"] = True
    session["customer_user_id"] = user["id"]
    session["customer_tenant_id"] = tenant["id"]
    session["customer_email"] = user["email"]
    session.permanent = True

    return jsonify({"ok": True})


@app.route("/dashboard/logout", methods=["POST"])
def dashboard_logout():
    for key in ["customer_logged_in", "customer_user_id", "customer_tenant_id", "customer_email"]:
        session.pop(key, None)

    return jsonify({"ok": True})


# =========================
# BASIC DATA HELPERS
# =========================

def get_customer_user_by_id(user_id: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM customer_users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_tenant_stats(tenant_id: str):
    month_key = time.strftime("%Y-%m", time.localtime())

    with closing(get_db()) as conn:
        lead_count = conn.execute(
            "SELECT COUNT(*) AS total FROM leads WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()["total"]

        sessions_count = conn.execute(
            "SELECT COUNT(DISTINCT session_id) AS total FROM messages WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()["total"]

        total_messages = conn.execute(
            "SELECT COUNT(*) AS total FROM messages WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()["total"]

        monthly_messages = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM usage_events
            WHERE tenant_id = ? AND event_type = 'message' AND month_key = ?
            """,
            (tenant_id, month_key),
        ).fetchone()["total"]

        monthly_leads = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM usage_events
            WHERE tenant_id = ? AND event_type = 'lead' AND month_key = ?
            """,
            (tenant_id, month_key),
        ).fetchone()["total"]

    tenant = get_tenant_by_id(tenant_id)
    monthly_limit = int(tenant["monthly_message_limit"]) if tenant else 0

    return {
        "month_key": month_key,
        "lead_count_total": int(lead_count),
        "session_count_total": int(sessions_count),
        "message_count_total": int(total_messages),
        "message_count_current_month": int(monthly_messages),
        "lead_count_current_month": int(monthly_leads),
        "monthly_message_limit": monthly_limit,
        "monthly_message_remaining": max(monthly_limit - int(monthly_messages), 0) if monthly_limit > 0 else None,
    }


def list_customer_users(tenant_id: str):
    with closing(get_db()) as conn:
        rows = conn.execute(
            """
            SELECT id, tenant_id, email, full_name, is_owner, is_active, created_at
            FROM customer_users
            WHERE tenant_id = ?
            ORDER BY created_at ASC
            """,
            (tenant_id,),
        ).fetchall()

    return [dict(r) for r in rows]


def get_tenant_leads(tenant_id: str):
    with closing(get_db()) as conn:
        rows = conn.execute(
            """
            SELECT id, tenant_id, name, email, phone, message, source, created_at
            FROM leads
            WHERE tenant_id = ?
            ORDER BY created_at DESC
            LIMIT 200
            """,
            (tenant_id,),
        ).fetchall()

    return [dict(r) for r in rows]


def get_tenant_chat_sessions(tenant_id: str, limit: int = 100):
    safe_limit = max(1, min(int(limit), 500))

    with closing(get_db()) as conn:
        rows = conn.execute(
            """
            SELECT
                session_id,
                COUNT(*) AS total_messages,
                MAX(created_at) AS last_message_at,
                MIN(created_at) AS first_message_at
            FROM messages
            WHERE tenant_id = ?
            GROUP BY session_id
            ORDER BY last_message_at DESC
            LIMIT ?
            """,
            (tenant_id, safe_limit),
        ).fetchall()

    return [dict(r) for r in rows]


def get_session_messages(tenant_id: str, session_id: str, limit: int = 200):
    safe_limit = max(1, min(int(limit), 500))

    with closing(get_db()) as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, role, content, created_at
            FROM messages
            WHERE tenant_id = ? AND session_id = ?
            ORDER BY created_at ASC, rowid ASC
            LIMIT ?
            """,
            (tenant_id, session_id, safe_limit),
        ).fetchall()

    return [dict(r) for r in rows]


def update_tenant_settings(tenant_id: str, data: dict):
    tenant = get_tenant_by_id(tenant_id)
    if not tenant:
        return None, "Tenant niet gevonden."

    name = clamp_text(data.get("name") or tenant["name"], 200)
    support_email = clamp_text(data.get("support_email") or tenant["support_email"], 200)
    website_url = clamp_text(data.get("website_url") or tenant["website_url"], 500)
    company_description = clamp_text(data.get("company_description") or tenant["company_description"], 5000)
    faq_context = clamp_text(data.get("faq_context") or tenant["faq_context"], 15000)

    if not name:
        return None, "Naam ontbreekt."

    if not validate_email(support_email):
        return None, "Support e-mail is ongeldig."

    with closing(get_db()) as conn:
        conn.execute(
            """
            UPDATE tenants
            SET name = ?, support_email = ?, billing_email = ?, website_url = ?,
                company_description = ?, faq_context = ?
            WHERE id = ?
            """,
            (
                name,
                support_email,
                support_email,
                website_url,
                company_description,
                faq_context,
                tenant_id,
            ),
        )
        conn.commit()

    return get_tenant_by_id(tenant_id), None


# =========================
# DASHBOARD ROUTES
# =========================

@app.route("/dashboard/me", methods=["GET"])
def dashboard_me():
    if not require_customer():
        return customer_forbidden()

    tenant = get_tenant_by_id(session["customer_tenant_id"])
    user = get_customer_user_by_id(session["customer_user_id"])

    if not tenant or not user:
        return customer_forbidden()

    host = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/")
    embed_code = (
        f'<script src="{host}/widget.js" '
        f'data-api-base="{host}" '
        f'data-tenant-key="{tenant["api_key"]}" '
        f'data-title="{tenant["name"]}"></script>'
    )

    return jsonify({
        "ok": True,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user.get("full_name", ""),
        },
        "tenant": {
            "id": tenant["id"],
            "name": tenant["name"],
            "slug": tenant["slug"],
            "api_key": tenant["api_key"],
            "plan_name": tenant["plan_name"],
            "subscription_status": tenant["subscription_status"],
            "support_email": tenant["support_email"],
            "website_url": tenant["website_url"],
            "company_description": tenant["company_description"],
            "faq_context": tenant["faq_context"],
        },
        "embed_code": embed_code,
    })


@app.route("/dashboard/stats", methods=["GET"])
def dashboard_stats():
    if not require_customer():
        return customer_forbidden()

    tenant = get_tenant_by_id(session["customer_tenant_id"])
    if not tenant:
        return customer_forbidden()

    return jsonify({
        "ok": True,
        "stats": get_tenant_stats(tenant["id"])
    })


@app.route("/dashboard/settings", methods=["POST"])
def dashboard_settings():
    if not require_customer():
        return customer_forbidden()

    tenant, error = update_tenant_settings(session["customer_tenant_id"], json_body())
    if error:
        return jsonify({"ok": False, "error": error}), 400

    host = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/")
    embed_code = (
        f'<script src="{host}/widget.js" '
        f'data-api-base="{host}" '
        f'data-tenant-key="{tenant["api_key"]}" '
        f'data-title="{tenant["name"]}"></script>'
    )

    return jsonify({
        "ok": True,
        "tenant": {
            "id": tenant["id"],
            "name": tenant["name"],
            "slug": tenant["slug"],
            "api_key": tenant["api_key"],
        },
        "embed_code": embed_code,
    })


@app.route("/dashboard/team", methods=["GET"])
def dashboard_team():
    if not require_customer():
        return customer_forbidden()

    return jsonify({
        "ok": True,
        "users": list_customer_users(session["customer_tenant_id"])
    })


@app.route("/dashboard/leads", methods=["GET"])
def dashboard_leads():
    if not require_customer():
        return customer_forbidden()

    return jsonify({
        "ok": True,
        "leads": get_tenant_leads(session["customer_tenant_id"])
    })


@app.route("/dashboard/chat/sessions", methods=["GET"])
def dashboard_chat_sessions():
    if not require_customer():
        return customer_forbidden()

    return jsonify({
        "ok": True,
        "sessions": get_tenant_chat_sessions(session["customer_tenant_id"])
    })


@app.route("/dashboard/chat/session/<session_id>", methods=["GET"])
def dashboard_chat_session(session_id):
    if not require_customer():
        return customer_forbidden()

    return jsonify({
        "ok": True,
        "messages": get_session_messages(session["customer_tenant_id"], session_id)
    })# =========================
# EXTRA HELPERS
# =========================

def create_customer_user(
    tenant_id: str,
    email: str,
    password: str,
    full_name: str = "",
    is_owner: bool = False,
):
    if not validate_email(email):
        return None, "Geldig e-mailadres ontbreekt."

    if len((password or "").strip()) < 8:
        return None, "Wachtwoord moet minimaal 8 tekens zijn."

    with closing(get_db()) as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM customer_users
            WHERE tenant_id = ? AND LOWER(email) = LOWER(?)
            LIMIT 1
            """,
            (tenant_id, email.strip()),
        ).fetchone()

        if existing:
            return None, "Gebruiker bestaat al."

        user_id = str(uuid.uuid4())

        conn.execute(
            """
            INSERT INTO customer_users (
                id, tenant_id, email, password_hash, full_name,
                is_owner, is_active, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                tenant_id,
                email.strip().lower(),
                generate_password_hash(password.strip()),
                clamp_text(full_name or "", 200),
                1 if is_owner else 0,
                1,
                now_ts(),
            ),
        )
        conn.commit()

    return get_customer_user_by_id(user_id), None


def create_password_reset_token(tenant_id: str, user_id: str, email: str) -> str:
    token = secrets.token_urlsafe(32)

    with closing(get_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                email TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE,
                is_used INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                used_at INTEGER DEFAULT 0
            )
            """
        )

        conn.execute(
            """
            INSERT INTO password_reset_tokens (
                id, tenant_id, user_id, email, token, is_used, created_at, used_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                tenant_id,
                user_id,
                email.strip().lower(),
                token,
                0,
                now_ts(),
                0,
            ),
        )
        conn.commit()

    return token


def get_password_reset_token_row(token: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM password_reset_tokens
            WHERE token = ?
            LIMIT 1
            """,
            (token,),
        ).fetchone()
    return dict(row) if row else None


def mark_password_reset_token_used(token: str):
    with closing(get_db()) as conn:
        conn.execute(
            """
            UPDATE password_reset_tokens
            SET is_used = 1, used_at = ?
            WHERE token = ?
            """,
            (now_ts(), token),
        )
        conn.commit()


def create_onboarding_token(tenant_id: str, email: str) -> str:
    token = secrets.token_urlsafe(32)

    with closing(get_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS onboarding_tokens (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL,
                is_used INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                used_at INTEGER DEFAULT 0
            )
            """
        )

        conn.execute(
            """
            INSERT INTO onboarding_tokens (id, tenant_id, token, email, is_used, created_at, used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), tenant_id, token, email.strip(), 0, now_ts(), 0),
        )
        conn.commit()

    return token


def get_onboarding_token_row(token: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM onboarding_tokens WHERE token = ?",
            (token,),
        ).fetchone()
    return dict(row) if row else None


def mark_onboarding_token_used(token: str):
    with closing(get_db()) as conn:
        conn.execute(
            "UPDATE onboarding_tokens SET is_used = 1, used_at = ? WHERE token = ?",
            (now_ts(), token),
        )
        conn.commit()


def record_usage_event(tenant_id: str, event_type: str, meta=None):
    with closing(get_db()) as conn:
        conn.execute(
            """
            INSERT INTO usage_events (id, tenant_id, event_type, month_key, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                tenant_id,
                event_type,
                time.strftime("%Y-%m", time.localtime()),
                json.dumps(meta or {}, ensure_ascii=False),
                now_ts(),
            ),
        )
        conn.commit()


def get_monthly_usage_count(tenant_id: str, event_type: str) -> int:
    mk = time.strftime("%Y-%m", time.localtime())

    with closing(get_db()) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM usage_events
            WHERE tenant_id = ? AND event_type = ? AND month_key = ?
            """,
            (tenant_id, event_type, mk),
        ).fetchone()

    return int(row["total"] if row else 0)


def tenant_can_chat(tenant: dict):
    if not tenant:
        return False, "Tenant niet gevonden."

    if int(tenant.get("is_active", 0)) != 1:
        return False, "Tenant is niet actief."

    monthly_limit = int(tenant.get("monthly_message_limit", 0) or 0)
    used = get_monthly_usage_count(tenant["id"], "message")

    if monthly_limit > 0 and used >= monthly_limit:
        return False, "Maandelijkse limiet bereikt."

    return True, None


def get_overview_stats():
    month_key = time.strftime("%Y-%m", time.localtime())

    with closing(get_db()) as conn:
        tenant_count = conn.execute(
            "SELECT COUNT(*) AS total FROM tenants"
        ).fetchone()["total"]

        total_leads = conn.execute(
            "SELECT COUNT(*) AS total FROM leads"
        ).fetchone()["total"]

        total_messages = conn.execute(
            "SELECT COUNT(*) AS total FROM messages"
        ).fetchone()["total"]

        total_sessions = conn.execute(
            "SELECT COUNT(DISTINCT session_id) AS total FROM messages"
        ).fetchone()["total"]

        monthly_messages = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM usage_events
            WHERE event_type = 'message' AND month_key = ?
            """,
            (month_key,),
        ).fetchone()["total"]

        monthly_leads = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM usage_events
            WHERE event_type = 'lead' AND month_key = ?
            """,
            (month_key,),
        ).fetchone()["total"]

    return {
        "month_key": month_key,
        "tenant_count": int(tenant_count),
        "lead_count_total": int(total_leads),
        "message_count_total": int(total_messages),
        "session_count_total": int(total_sessions),
        "message_count_current_month": int(monthly_messages),
        "lead_count_current_month": int(monthly_leads),
    }


def get_all_tenants():
    with closing(get_db()) as conn:
        rows = conn.execute(
            """
            SELECT
                id, name, slug, api_key, plan_name, subscription_status,
                support_email, website_url, is_active, created_at
            FROM tenants
            ORDER BY created_at DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


# =========================
# WIDGET JAVASCRIPT
# =========================

@app.route("/widget.js", methods=["GET"])
def widget_js():
    js = r"""
(function () {
    var initialScript = document.currentScript;

    function resolveScriptElement() {
        if (initialScript) return initialScript;
        var scripts = document.querySelectorAll('script[src]');
        for (var i = scripts.length - 1; i >= 0; i--) {
            var src = scripts[i].getAttribute("src") || "";
            if (src.indexOf("/widget.js") !== -1 || src.indexOf("widget.js") !== -1) {
                return scripts[i];
            }
        }
        return null;
    }

    function bootWidget() {
        var currentScript = resolveScriptElement();
        var tenantKey = currentScript ? currentScript.getAttribute("data-tenant-key") : "";
        var apiBase = currentScript ? currentScript.getAttribute("data-api-base") : "";
        var title = currentScript ? currentScript.getAttribute("data-title") : "Chat";

        if (!tenantKey || !apiBase) return;
        if (document.getElementById("assistify-launcher")) return;

        var sessionId = localStorage.getItem("assistify_session_id");
        if (!sessionId) {
            sessionId = "sess-" + Math.random().toString(36).slice(2) + Date.now();
            localStorage.setItem("assistify_session_id", sessionId);
        }

        var launcher = document.createElement("button");
        launcher.id = "assistify-launcher";
        launcher.textContent = title;
        launcher.style.position = "fixed";
        launcher.style.right = "20px";
        launcher.style.bottom = "20px";
        launcher.style.zIndex = "999999";
        launcher.style.padding = "14px 18px";
        launcher.style.border = "none";
        launcher.style.borderRadius = "999px";
        launcher.style.background = "#6d5efc";
        launcher.style.color = "#fff";
        launcher.style.cursor = "pointer";

        var box = document.createElement("div");
        box.id = "assistify-box";
        box.style.display = "none";
        box.style.position = "fixed";
        box.style.right = "20px";
        box.style.bottom = "80px";
        box.style.width = "360px";
        box.style.maxWidth = "calc(100vw - 40px)";
        box.style.height = "520px";
        box.style.background = "#fff";
        box.style.border = "1px solid #ddd";
        box.style.borderRadius = "16px";
        box.style.zIndex = "999999";
        box.style.overflow = "hidden";

        box.innerHTML =
            '<div style="padding:14px;background:#6d5efc;color:#fff;font-weight:700;">' + title + '</div>' +
            '<div id="assistify-messages" style="height:360px;overflow:auto;padding:12px;background:#f8fafc;"></div>' +
            '<div style="padding:12px;border-top:1px solid #eee;">' +
            '<textarea id="assistify-input" placeholder="Typ je bericht..." style="width:100%;height:70px;padding:10px;box-sizing:border-box;"></textarea>' +
            '<button id="assistify-send" style="width:100%;margin-top:8px;padding:12px;border:none;border-radius:10px;background:#6d5efc;color:#fff;cursor:pointer;">Versturen</button>' +
            '</div>';

        document.body.appendChild(launcher);
        document.body.appendChild(box);

        var messages = document.getElementById("assistify-messages");
        var input = document.getElementById("assistify-input");
        var send = document.getElementById("assistify-send");

        function addMessage(text, who) {
            var row = document.createElement("div");
            row.style.marginBottom = "10px";
            row.style.textAlign = who === "user" ? "right" : "left";

            var bubble = document.createElement("div");
            bubble.style.display = "inline-block";
            bubble.style.maxWidth = "85%";
            bubble.style.padding = "10px 12px";
            bubble.style.borderRadius = "12px";
            bubble.style.whiteSpace = "pre-wrap";
            bubble.style.background = who === "user" ? "#6d5efc" : "#ffffff";
            bubble.style.color = who === "user" ? "#ffffff" : "#111111";
            bubble.style.border = who === "user" ? "none" : "1px solid #e5e7eb";
            bubble.textContent = text;

            row.appendChild(bubble);
            messages.appendChild(row);
            messages.scrollTop = messages.scrollHeight;
        }

        async function sendMessage() {
            var text = input.value.trim();
            if (!text) return;

            addMessage(text, "user");
            input.value = "";
            send.disabled = true;
            addMessage("Bezig met antwoorden...", "assistant");

            try {
                var res = await fetch(apiBase + "/widget/chat", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-Tenant-Key": tenantKey
                    },
                    body: JSON.stringify({
                        message: text,
                        session_id: sessionId
                    })
                });

                var data = await res.json();

                if (messages.lastChild) messages.removeChild(messages.lastChild);

                if (data.ok) addMessage(data.reply || "Geen antwoord ontvangen.", "assistant");
                else addMessage(data.error || "Er ging iets mis.", "assistant");
            } catch (err) {
                if (messages.lastChild) messages.removeChild(messages.lastChild);
                addMessage("Netwerkfout. Probeer het opnieuw.", "assistant");
            } finally {
                send.disabled = false;
            }
        }

        launcher.addEventListener("click", function () {
            box.style.display = box.style.display === "none" ? "block" : "none";
        });

        send.addEventListener("click", sendMessage);
        input.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        addMessage("Hoi! Waar kan ik je mee helpen?", "assistant");
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bootWidget);
    } else {
        bootWidget();
    }
})();
"""
    return Response(js, mimetype="application/javascript")


# =========================
# WIDGET ROUTES
# =========================

@app.route("/widget/chat", methods=["POST"])
def widget_chat():
    ip = get_client_ip()
    tenant_key = request.headers.get("X-Tenant-Key", "").strip()

    if not tenant_key:
        return jsonify({"ok": False, "error": "Tenant key ontbreekt."}), 401

    tenant = get_tenant_by_api_key(tenant_key)
    if not tenant:
        return jsonify({"ok": False, "error": "Ongeldige tenant key."}), 401

    allowed, reason = tenant_can_chat(tenant)
    if not allowed:
        return jsonify({"ok": False, "error": reason}), 403

    if is_rate_limited(ip, tenant_key):
        return jsonify({"ok": False, "error": "Te veel aanvragen. Probeer later opnieuw."}), 429

    try:
        data = json_body()
        user_message = clamp_text(data.get("message") or "", MAX_MESSAGE_LENGTH)
        session_id = (data.get("session_id") or "").strip()[:120] or str(uuid.uuid4())

        if not user_message:
            return jsonify({"ok": False, "error": "Bericht is leeg."}), 400

        answer = ask_ai(tenant, session_id, user_message)
        answer = clamp_text(answer or "Sorry, ik kon nu geen goed antwoord genereren.", MAX_ASSISTANT_REPLY_CHARS)

        save_message(tenant["id"], session_id, "user", user_message)
        save_message(tenant["id"], session_id, "assistant", answer)
        record_usage_event(tenant["id"], "message", {"session_id": session_id})

        return jsonify({
            "ok": True,
            "reply": answer,
            "session_id": session_id,
            "tenant": tenant["slug"],
            "version": APP_VERSION,
        })

    except Exception as e:
        error_text = str(e).lower()

        if "timeout" in error_text or "timed out" in error_text:
            return jsonify({
                "ok": False,
                "error": "De AI deed te lang over antwoorden. Probeer het opnieuw.",
                "version": APP_VERSION,
            }), 504

        return jsonify({
            "ok": False,
            "error": "Er ging iets mis in de AI backend.",
            "details": str(e),
            "version": APP_VERSION,
        }), 500


@app.route("/widget/lead", methods=["POST"])
def widget_lead():
    tenant_key = request.headers.get("X-Tenant-Key", "").strip()

    if not tenant_key:
        return jsonify({"ok": False, "error": "Tenant key ontbreekt."}), 401

    tenant = get_tenant_by_api_key(tenant_key)
    if not tenant:
        return jsonify({"ok": False, "error": "Ongeldige tenant key."}), 401

    try:
        data = json_body()

        name = clamp_text(data.get("name") or "", 200)
        email = clamp_text(data.get("email") or "", 200)
        phone = clamp_text(data.get("phone") or "", 100)
        message = clamp_text(data.get("message") or "", 5000)

        if not name:
            return jsonify({"ok": False, "error": "Naam ontbreekt."}), 400
        if not email or not validate_email(email):
            return jsonify({"ok": False, "error": "Geldig e-mailadres ontbreekt."}), 400
        if not message:
            return jsonify({"ok": False, "error": "Bericht ontbreekt."}), 400

        with closing(get_db()) as conn:
            lead_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO leads (id, tenant_id, name, email, phone, message, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (lead_id, tenant["id"], name, email, phone, message, "widget", now_ts()),
            )
            conn.commit()

        record_usage_event(tenant["id"], "lead", {"source": "widget"})

        return jsonify({"ok": True, "message": "Lead succesvol opgeslagen."})

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "Lead opslaan mislukt.",
            "details": str(e),
        }), 500


# =========================
# ADMIN DATA ROUTES
# =========================

@app.route("/admin/stats/overview", methods=["GET"])
def admin_stats_overview():
    if not require_admin():
        return admin_forbidden()

    return jsonify({
        "ok": True,
        "stats": get_overview_stats(),
    })


@app.route("/admin/tenants", methods=["GET"])
def admin_tenants():
    if not require_admin():
        return admin_forbidden()

    return jsonify({
        "ok": True,
        "tenants": get_all_tenants(),
    })


@app.route("/admin/audit-logs", methods=["GET"])
def admin_audit_logs():
    if not require_admin():
        return admin_forbidden()

    return jsonify({
        "ok": True,
        "logs": [],
    })


# =========================
# RESET PASSWORD ROUTES
# =========================

@app.route("/reset-password/request", methods=["POST"])
def reset_password_request_submit():
    data = json_body()
    email = clamp_text(data.get("email") or "", 200).lower()

    with closing(get_db()) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM customer_users
            WHERE LOWER(email) = LOWER(?)
            LIMIT 1
            """,
            (email,),
        ).fetchone()

    if not row:
        return jsonify({"ok": False, "error": "Gebruiker niet gevonden."}), 404

    user = dict(row)
    token = create_password_reset_token(user["tenant_id"], user["id"], user["email"])

    base_url = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/")
    reset_url = f"{base_url}/reset-password/{token}"

    return jsonify({
        "ok": True,
        "reset_url": reset_url,
        "email_sent": False,
    })


@app.route("/reset-password/<token>", methods=["POST"])
def reset_password_submit(token):
    row = get_password_reset_token_row(token)

    if not row:
        return jsonify({"ok": False, "error": "Reset token niet gevonden."}), 404

    if int(row.get("is_used", 0)) == 1:
        return jsonify({"ok": False, "error": "Reset token is al gebruikt."}), 400

    data = json_body()
    password = (data.get("password") or "").strip()

    if len(password) < 8:
        return jsonify({"ok": False, "error": "Wachtwoord moet minimaal 8 tekens zijn."}), 400

    with closing(get_db()) as conn:
        conn.execute(
            "UPDATE customer_users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), row["user_id"]),
        )
        conn.commit()

    mark_password_reset_token_used(token)

    return jsonify({"ok": True})


# =========================
# SIGNUP ROUTES
# =========================

@app.route("/signup/create-checkout", methods=["POST"])
def signup_create_checkout():
    data = json_body()
    email = clamp_text(data.get("email") or "", 200)
    plan_name = clamp_text(data.get("plan_name") or "starter", 50).lower()

    if not validate_email(email):
        return jsonify({"ok": False, "error": "Geldig e-mailadres ontbreekt."}), 400

    if plan_name not in ("starter", "pro", "agency"):
        return jsonify({"ok": False, "error": "Ongeldig plan."}), 400

    if stripe and STRIPE_SECRET_KEY:
        try:
            price_mapping = {
                "starter": STRIPE_PRICE_STARTER_MONTHLY,
                "pro": STRIPE_PRICE_PRO_MONTHLY,
                "agency": STRIPE_PRICE_AGENCY_MONTHLY,
            }
            price_id = price_mapping.get(plan_name, "")

            if not price_id:
                raise RuntimeError("Geen Stripe price id gevonden voor dit plan.")

            session_obj = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=(STRIPE_SUCCESS_URL or (request.host_url.rstrip("/") + "/signup/complete-success")) + "?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=STRIPE_CANCEL_URL or (request.host_url.rstrip("/") + "/signup"),
                customer_email=email,
                metadata={
                    "source": "public_signup",
                    "signup_email": email,
                    "plan_name": plan_name,
                },
            )
            return jsonify({"ok": True, "url": session_obj.url})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    tenant_id = str(uuid.uuid4())
    slug = normalize_slug(email.split("@")[0]) or f"tenant-{secrets.token_hex(3)}"

    with closing(get_db()) as conn:
        conn.execute(
            """
            INSERT INTO tenants (
                id, name, slug, api_key, company_tone, company_description,
                support_email, support_phone, website_url, faq_context,
                plan_name, subscription_status, monthly_message_limit,
                stripe_customer_id, stripe_subscription_id, billing_email,
                billing_cycle, widget_color, is_active, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                "Nieuw bedrijf",
                slug,
                generate_api_key(),
                DEFAULT_COMPANY_TONE,
                DEFAULT_COMPANY_DESCRIPTION,
                email,
                DEFAULT_SUPPORT_PHONE,
                DEFAULT_WEBSITE_URL,
                DEFAULT_FAQ_CONTEXT,
                plan_name,
                "active",
                500 if plan_name == "starter" else (5000 if plan_name == "pro" else 50000),
                "",
                "",
                email,
                "monthly",
                DEFAULT_WIDGET_COLOR,
                1,
                now_ts(),
            ),
        )
        conn.commit()

    token = create_onboarding_token(tenant_id, email)
    base_url = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/")

    return jsonify({
        "ok": True,
        "url": f"{base_url}/signup/complete/{token}",
    })


@app.route("/signup/complete/<token>", methods=["GET"])
def signup_complete_view(token):
    row = get_onboarding_token_row(token)

    if not row:
        return Response("Onboarding token niet gevonden.", status=404)

    if int(row.get("is_used", 0)) == 1:
        return Response("Onboarding token is al gebruikt.", status=400)

    tenant = get_tenant_by_id(row["tenant_id"])
    if not tenant:
        return Response("Tenant niet gevonden.", status=404)

    return Response(render_onboarding_html(token, tenant), mimetype="text/html")


@app.route("/signup/complete/<token>", methods=["POST"])
def signup_complete_save(token):
    row = get_onboarding_token_row(token)

    if not row:
        return jsonify({"ok": False, "error": "Onboarding token niet gevonden."}), 404

    if int(row.get("is_used", 0)) == 1:
        return jsonify({"ok": False, "error": "Onboarding token is al gebruikt."}), 400

    tenant = get_tenant_by_id(row["tenant_id"])
    if not tenant:
        return jsonify({"ok": False, "error": "Tenant niet gevonden."}), 404

    data = json_body()

    full_name = clamp_text(data.get("full_name") or "", 200)
    password = (data.get("password") or "").strip()
    name = clamp_text(data.get("name") or tenant["name"], 200)
    slug = normalize_slug(data.get("slug") or tenant["slug"])
    support_email = clamp_text(data.get("support_email") or tenant["support_email"], 200)
    website_url = clamp_text(data.get("website_url") or tenant["website_url"], 500)
    company_description = clamp_text(data.get("company_description") or tenant["company_description"], 5000)

    if not name:
        return jsonify({"ok": False, "error": "Naam ontbreekt."}), 400
    if not slug:
        return jsonify({"ok": False, "error": "Slug ontbreekt."}), 400
    if not validate_email(support_email):
        return jsonify({"ok": False, "error": "Support e-mail is ongeldig."}), 400
    if len(password) < 8:
        return jsonify({"ok": False, "error": "Wachtwoord moet minimaal 8 tekens zijn."}), 400

    existing_slug = get_tenant_by_slug(slug)
    if existing_slug and existing_slug["id"] != tenant["id"]:
        return jsonify({"ok": False, "error": "Slug bestaat al."}), 400

    with closing(get_db()) as conn:
        conn.execute(
            """
            UPDATE tenants
            SET name = ?, slug = ?, support_email = ?, billing_email = ?,
                website_url = ?, company_description = ?
            WHERE id = ?
            """,
            (
                name,
                slug,
                support_email,
                support_email,
                website_url,
                company_description,
                tenant["id"],
            ),
        )
        conn.commit()

    user, error = create_customer_user(
        tenant["id"],
        support_email,
        password,
        full_name=full_name,
        is_owner=True,
    )
    if error and "bestaat al" not in error.lower():
        return jsonify({"ok": False, "error": error}), 400

    mark_onboarding_token_used(token)
    tenant = get_tenant_by_id(tenant["id"])

    host = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/")
    embed_code = (
        f'<script src="{host}/widget.js" '
        f'data-api-base="{host}" '
        f'data-tenant-key="{tenant["api_key"]}" '
        f'data-title="{tenant["name"]}"></script>'
    )

    return jsonify({
        "ok": True,
        "tenant": {
            "id": tenant["id"],
            "name": tenant["name"],
            "slug": tenant["slug"],
            "api_key": tenant["api_key"],
        },
        "embed_code": embed_code,
    })


# =========================
# RUN
# =========================

if __name__ == "__main__":
    ensure_startup()
    app.run(host="0.0.0.0", port=PORT, debug=True)# =========================
# AUDIT LOGS
# =========================

def create_audit_log(
    tenant_id,
    actor_type: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str = "",
    meta=None,
    ip_address: str = "",
):
    with closing(get_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT,
                actor_type TEXT NOT NULL,
                actor_id TEXT DEFAULT '',
                action TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT DEFAULT '',
                meta_json TEXT,
                ip_address TEXT DEFAULT '',
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO audit_logs (
                id, tenant_id, actor_type, actor_id, action,
                target_type, target_id, meta_json, ip_address, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                tenant_id,
                actor_type,
                actor_id or "",
                action,
                target_type,
                target_id or "",
                json.dumps(meta or {}, ensure_ascii=False),
                ip_address or "",
                now_ts(),
            ),
        )
        conn.commit()


def list_audit_logs(tenant_id=None, limit: int = 100):
    safe_limit = max(1, min(int(limit), 500))
    with closing(get_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT,
                actor_type TEXT NOT NULL,
                actor_id TEXT DEFAULT '',
                action TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT DEFAULT '',
                meta_json TEXT,
                ip_address TEXT DEFAULT '',
                created_at INTEGER NOT NULL
            )
            """
        )
        if tenant_id:
            rows = conn.execute(
                "SELECT * FROM audit_logs WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, safe_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
    return [dict(r) for r in rows]


@app.route("/dashboard/audit-logs", methods=["GET"])
def dashboard_audit_logs():
    if not require_customer():
        return customer_forbidden()

    return jsonify({
        "ok": True,
        "logs": list_audit_logs(session["customer_tenant_id"], 200),
    })


@app.route("/admin/audit-logs", methods=["GET"])
def admin_audit_logs_v2():
    if not require_admin():
        return admin_forbidden()

    return jsonify({
        "ok": True,
        "logs": list_audit_logs(None, 200),
    })


# =========================
# INVITES
# =========================

def create_invite_token(tenant_id: str, email: str, full_name: str, invited_by_user_id: str) -> str:
    token = secrets.token_urlsafe(32)

    with closing(get_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invite_tokens (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                email TEXT NOT NULL,
                full_name TEXT NOT NULL DEFAULT '',
                token TEXT NOT NULL UNIQUE,
                invited_by_user_id TEXT NOT NULL,
                is_used INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                used_at INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO invite_tokens (
                id, tenant_id, email, full_name, token,
                invited_by_user_id, is_used, created_at, used_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                tenant_id,
                email.strip().lower(),
                clamp_text(full_name or "", 200),
                token,
                invited_by_user_id,
                0,
                now_ts(),
                0,
            ),
        )
        conn.commit()

    return token


def get_invite_token_row(token: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM invite_tokens WHERE token = ?",
            (token,),
        ).fetchone()
    return dict(row) if row else None


def mark_invite_token_used(token: str):
    with closing(get_db()) as conn:
        conn.execute(
            "UPDATE invite_tokens SET is_used = 1, used_at = ? WHERE token = ?",
            (now_ts(), token),
        )
        conn.commit()


@app.route("/invite/<token>", methods=["GET"])
def invite_accept_page(token):
    row = get_invite_token_row(token)
    if not row:
        return Response("Invite niet gevonden.", status=404)

    if int(row.get("is_used", 0)) == 1:
        return Response("Invite is al gebruikt.", status=400)

    return Response(render_invite_accept_html(token, row), mimetype="text/html")


@app.route("/invite/<token>", methods=["POST"])
def invite_accept_submit(token):
    row = get_invite_token_row(token)
    if not row:
        return jsonify({"ok": False, "error": "Invite niet gevonden."}), 404

    if int(row.get("is_used", 0)) == 1:
        return jsonify({"ok": False, "error": "Invite is al gebruikt."}), 400

    data = json_body()
    email = clamp_text(data.get("email") or row["email"], 200).lower()
    full_name = clamp_text(data.get("full_name") or row.get("full_name", ""), 200)
    password = (data.get("password") or "").strip()

    if email != row["email"].lower():
        return jsonify({"ok": False, "error": "E-mailadres klopt niet met de invite."}), 400

    user, error = create_customer_user(
        row["tenant_id"],
        email,
        password,
        full_name=full_name,
        is_owner=False,
    )
    if error:
        return jsonify({"ok": False, "error": error}), 400

    mark_invite_token_used(token)

    create_audit_log(
        row["tenant_id"],
        "customer_user",
        user["id"],
        "invite_accepted",
        "customer_user",
        user["id"],
        {"email": user["email"], "full_name": user["full_name"]},
        get_client_ip(),
    )

    return jsonify({"ok": True, "user": {"id": user["id"], "email": user["email"]}})


@app.route("/dashboard/team/invite", methods=["POST"])
def dashboard_team_invite():
    if not require_customer():
        return customer_forbidden()

    current_user = get_customer_user_by_id(session["customer_user_id"])
    if not current_user or int(current_user.get("is_owner", 0)) != 1:
        return jsonify({"ok": False, "error": "Alleen owner kan invites maken."}), 403

    data = json_body()
    email = clamp_text(data.get("email") or "", 200).lower()
    full_name = clamp_text(data.get("full_name") or "", 200)

    if not validate_email(email):
        return jsonify({"ok": False, "error": "Geldig e-mailadres ontbreekt."}), 400

    with closing(get_db()) as conn:
        existing = conn.execute(
            """
            SELECT id
            FROM customer_users
            WHERE tenant_id = ? AND LOWER(email) = LOWER(?)
            LIMIT 1
            """,
            (session["customer_tenant_id"], email),
        ).fetchone()

    if existing:
        return jsonify({"ok": False, "error": "Gebruiker bestaat al."}), 400

    token = create_invite_token(
        session["customer_tenant_id"],
        email,
        full_name,
        current_user["id"],
    )

    base_url = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/")
    invite_url = f"{base_url}/invite/{token}"

    create_audit_log(
        session["customer_tenant_id"],
        "customer_user",
        current_user["id"],
        "team_invite_created",
        "invite_token",
        token,
        {"email": email, "full_name": full_name},
        get_client_ip(),
    )

    return jsonify({
        "ok": True,
        "invite_url": invite_url,
        "token": token,
    })


# =========================
# CSV EXPORT
# =========================

def make_csv_response(filename: str, rows: list, fieldnames: list) -> Response:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fieldnames})

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/dashboard/leads/export.csv", methods=["GET"])
def dashboard_leads_export():
    if not require_customer():
        return customer_forbidden()

    rows = get_tenant_leads(session["customer_tenant_id"])

    return make_csv_response(
        "leads.csv",
        rows,
        ["id", "name", "email", "phone", "message", "source", "created_at"],
    )


# =========================
# BILLING PORTAL
# =========================

@app.route("/dashboard/billing-portal", methods=["POST"])
def dashboard_billing_portal():
    if not require_customer():
        return customer_forbidden()

    tenant = get_tenant_by_id(session["customer_tenant_id"])
    if not tenant:
        return customer_forbidden()

    if stripe and STRIPE_SECRET_KEY and (tenant.get("stripe_customer_id") or "").strip():
        try:
            return_url = STRIPE_SUCCESS_URL or (request.host_url.rstrip("/") + "/dashboard")
            portal = stripe.billing_portal.Session.create(
                customer=tenant["stripe_customer_id"],
                return_url=return_url,
            )
            return jsonify({"ok": True, "url": portal.url})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({
        "ok": True,
        "url": "/dashboard",
        "message": "Geen Stripe billing portal beschikbaar.",
    })


# =========================
# WIDGET JS MET LEAD TAB
# =========================

@app.route("/widget-full.js", methods=["GET"])
def widget_full_js():
    js = r"""
(function () {
    var initialScript = document.currentScript;

    function resolveScriptElement() {
        if (initialScript) return initialScript;
        var scripts = document.querySelectorAll('script[src]');
        for (var i = scripts.length - 1; i >= 0; i--) {
            var src = scripts[i].getAttribute("src") || "";
            if (src.indexOf("/widget-full.js") !== -1 || src.indexOf("/widget.js") !== -1) {
                return scripts[i];
            }
        }
        return null;
    }

    function bootWidget() {
        var currentScript = resolveScriptElement();
        var tenantKey = currentScript ? currentScript.getAttribute("data-tenant-key") : "";
        var apiBase = currentScript ? currentScript.getAttribute("data-api-base") : "";
        var title = currentScript ? currentScript.getAttribute("data-title") : "Chat";

        if (!tenantKey || !apiBase) return;
        if (document.getElementById("assistify-launcher")) return;

        var sessionId = localStorage.getItem("assistify_session_id");
        if (!sessionId) {
            sessionId = "sess-" + Math.random().toString(36).slice(2) + Date.now();
            localStorage.setItem("assistify_session_id", sessionId);
        }

        var launcher = document.createElement("button");
        launcher.id = "assistify-launcher";
        launcher.textContent = title;
        launcher.style.position = "fixed";
        launcher.style.right = "20px";
        launcher.style.bottom = "20px";
        launcher.style.zIndex = "999999";
        launcher.style.padding = "14px 18px";
        launcher.style.border = "none";
        launcher.style.borderRadius = "999px";
        launcher.style.background = "#6d5efc";
        launcher.style.color = "#fff";
        launcher.style.cursor = "pointer";

        var box = document.createElement("div");
        box.id = "assistify-box";
        box.style.display = "none";
        box.style.position = "fixed";
        box.style.right = "20px";
        box.style.bottom = "80px";
        box.style.width = "360px";
        box.style.maxWidth = "calc(100vw - 40px)";
        box.style.height = "560px";
        box.style.background = "#fff";
        box.style.border = "1px solid #ddd";
        box.style.borderRadius = "16px";
        box.style.zIndex = "999999";
        box.style.overflow = "hidden";

        box.innerHTML =
            '<div style="padding:14px;background:#6d5efc;color:#fff;font-weight:700;">' + title + '</div>' +
            '<div style="display:flex;border-bottom:1px solid #eee;">' +
            '<button id="assistify-tab-chat" style="flex:1;padding:10px;border:none;background:#fff;cursor:pointer;">Chat</button>' +
            '<button id="assistify-tab-lead" style="flex:1;padding:10px;border:none;background:#fff;cursor:pointer;">Lead</button>' +
            '</div>' +
            '<div id="assistify-chat-tab">' +
            '<div id="assistify-messages" style="height:320px;overflow:auto;padding:12px;background:#f8fafc;"></div>' +
            '<div style="padding:12px;border-top:1px solid #eee;">' +
            '<textarea id="assistify-input" placeholder="Typ je bericht..." style="width:100%;height:70px;padding:10px;box-sizing:border-box;"></textarea>' +
            '<button id="assistify-send" style="width:100%;margin-top:8px;padding:12px;border:none;border-radius:10px;background:#6d5efc;color:#fff;cursor:pointer;">Versturen</button>' +
            '</div>' +
            '</div>' +
            '<div id="assistify-lead-tab" style="display:none;padding:12px;background:#fff;height:430px;overflow:auto;">' +
            '<input id="assistify-lead-name" placeholder="Naam" style="width:100%;padding:10px;margin-bottom:8px;box-sizing:border-box;">' +
            '<input id="assistify-lead-email" placeholder="E-mail" style="width:100%;padding:10px;margin-bottom:8px;box-sizing:border-box;">' +
            '<input id="assistify-lead-phone" placeholder="Telefoon" style="width:100%;padding:10px;margin-bottom:8px;box-sizing:border-box;">' +
            '<textarea id="assistify-lead-message" placeholder="Waar kunnen we mee helpen?" style="width:100%;height:120px;padding:10px;margin-bottom:8px;box-sizing:border-box;"></textarea>' +
            '<button id="assistify-lead-send" style="width:100%;padding:12px;border:none;border-radius:10px;background:#6d5efc;color:#fff;cursor:pointer;">Lead versturen</button>' +
            '<div id="assistify-lead-status" style="margin-top:8px;font-size:14px;color:#444;"></div>' +
            '</div>';

        document.body.appendChild(launcher);
        document.body.appendChild(box);

        var messages = document.getElementById("assistify-messages");
        var input = document.getElementById("assistify-input");
        var send = document.getElementById("assistify-send");
        var chatTab = document.getElementById("assistify-chat-tab");
        var leadTab = document.getElementById("assistify-lead-tab");

        function switchTab(tab) {
            chatTab.style.display = tab === "chat" ? "block" : "none";
            leadTab.style.display = tab === "lead" ? "block" : "none";
        }

        function addMessage(text, who) {
            var row = document.createElement("div");
            row.style.marginBottom = "10px";
            row.style.textAlign = who === "user" ? "right" : "left";

            var bubble = document.createElement("div");
            bubble.style.display = "inline-block";
            bubble.style.maxWidth = "85%";
            bubble.style.padding = "10px 12px";
            bubble.style.borderRadius = "12px";
            bubble.style.whiteSpace = "pre-wrap";
            bubble.style.background = who === "user" ? "#6d5efc" : "#ffffff";
            bubble.style.color = who === "user" ? "#ffffff" : "#111111";
            bubble.style.border = who === "user" ? "none" : "1px solid #e5e7eb";
            bubble.textContent = text;

            row.appendChild(bubble);
            messages.appendChild(row);
            messages.scrollTop = messages.scrollHeight;
        }

        async function sendMessage() {
            var text = input.value.trim();
            if (!text) return;

            addMessage(text, "user");
            input.value = "";
            send.disabled = true;
            addMessage("Bezig met antwoorden...", "assistant");

            try {
                var res = await fetch(apiBase + "/widget/chat", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-Tenant-Key": tenantKey
                    },
                    body: JSON.stringify({
                        message: text,
                        session_id: sessionId
                    })
                });

                var data = await res.json();
                if (messages.lastChild) messages.removeChild(messages.lastChild);

                if (data.ok) addMessage(data.reply || "Geen antwoord ontvangen.", "assistant");
                else addMessage(data.error || "Er ging iets mis.", "assistant");
            } catch (err) {
                if (messages.lastChild) messages.removeChild(messages.lastChild);
                addMessage("Netwerkfout. Probeer het opnieuw.", "assistant");
            } finally {
                send.disabled = false;
            }
        }

        async function sendLead() {
            var name = document.getElementById("assistify-lead-name").value.trim();
            var email = document.getElementById("assistify-lead-email").value.trim();
            var phone = document.getElementById("assistify-lead-phone").value.trim();
            var message = document.getElementById("assistify-lead-message").value.trim();
            var status = document.getElementById("assistify-lead-status");

            status.textContent = "Bezig...";

            try {
                var res = await fetch(apiBase + "/widget/lead", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-Tenant-Key": tenantKey
                    },
                    body: JSON.stringify({
                        name: name,
                        email: email,
                        phone: phone,
                        message: message
                    })
                });

                var data = await res.json();
                status.textContent = data.ok ? "Lead succesvol verstuurd." : (data.error || "Er ging iets mis.");
            } catch (err) {
                status.textContent = "Netwerkfout. Probeer het opnieuw.";
            }
        }

        launcher.addEventListener("click", function () {
            box.style.display = box.style.display === "none" ? "block" : "none";
        });

        document.getElementById("assistify-tab-chat").addEventListener("click", function () {
            switchTab("chat");
        });

        document.getElementById("assistify-tab-lead").addEventListener("click", function () {
            switchTab("lead");
        });

        send.addEventListener("click", sendMessage);
        document.getElementById("assistify-lead-send").addEventListener("click", sendLead);

        input.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        addMessage("Hoi! Waar kan ik je mee helpen?", "assistant");
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bootWidget);
    } else {
        bootWidget();
    }
})();
"""
    return Response(js, mimetype="application/javascript")
