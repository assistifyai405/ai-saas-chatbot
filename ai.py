import os
import time
import uuid
import json
import secrets
import sqlite3
import smtplib
import csv
import io
import requests
import re

from contextlib import closing
from datetime import timedelta
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
# VERSION
# =========================

APP_VERSION = "v42.0"


# =========================
# CONFIG
# =========================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY","").strip()
MODEL_NAME = os.getenv("OPENAI_MODEL","gpt-4.1-mini").strip()

PORT = int(os.getenv("PORT","5000"))
DB_PATH = os.getenv("DB_PATH","assistify.db").strip()

SECRET_KEY = os.getenv("SECRET_KEY",secrets.token_urlsafe(32)).strip()
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL","").rstrip("/")

SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE","false").lower()=="true"
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE","Lax")

PERMANENT_SESSION_LIFETIME_HOURS = int(os.getenv("PERMANENT_SESSION_LIFETIME_HOURS","24"))

MAX_HISTORY_MESSAGES = 12
MAX_OUTPUT_TOKENS = 900
MAX_ASSISTANT_REPLY_CHARS = 9000

TRIAL_DAYS = 7


# =========================
# PRICING
# =========================

PRICE_STARTER_EUR = 49
PRICE_PRO_EUR = 149
PRICE_AGENCY_EUR = 399


# =========================
# DEFAULT COMPANY
# =========================

DEFAULT_COMPANY_NAME = "Assistify AI"

DEFAULT_COMPANY_DESCRIPTION = """
AI klantenservice die automatisch vragen van website bezoekers
beantwoordt en nieuwe leads opvangt.
"""

DEFAULT_FAQ_CONTEXT = """
Support: maandag t/m vrijdag 09:00–17:00
Reactietijd e-mail: binnen 24 uur
Demo aanvragen kan via de chat of website.
"""

DEFAULT_WIDGET_COLOR = "#6d5efc"


# =========================
# EMAIL
# =========================

SMTP_ENABLED = os.getenv("SMTP_ENABLED","false").lower()=="true"

SMTP_HOST = os.getenv("SMTP_HOST","")
SMTP_PORT = int(os.getenv("SMTP_PORT","587"))

SMTP_USERNAME = os.getenv("SMTP_USERNAME","")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD","")

SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL","support@assistifyai.nl")


# =========================
# ADMIN
# =========================

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME","admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD","admin123")


# =========================
# STRIPE
# =========================

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY","")

STRIPE_PRICE_STARTER_MONTHLY = os.getenv("STRIPE_PRICE_STARTER_MONTHLY","")
STRIPE_PRICE_PRO_MONTHLY = os.getenv("STRIPE_PRICE_PRO_MONTHLY","")
STRIPE_PRICE_AGENCY_MONTHLY = os.getenv("STRIPE_PRICE_AGENCY_MONTHLY","")

if stripe and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


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

CORS(app)

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

rate_limit_store = {}
_startup_done = False


# =========================
# HELPERS
# =========================

def now_ts():
    return int(time.time())


def clamp_text(text,max_len):

    text=(text or "").strip()

    if len(text)>max_len:
        text=text[:max_len]

    return text


def json_body():
    return request.get_json(silent=True) or {}


def generate_api_key():
    return "tenant_"+secrets.token_urlsafe(24)


def get_or_create_session_id(data):

    session_id=(data.get("session_id") or "").strip()

    if session_id:
        return session_id[:120]

    return str(uuid.uuid4())# =========================
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

        # TENANTS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tenants (

            id TEXT PRIMARY KEY,

            name TEXT,
            slug TEXT UNIQUE,
            api_key TEXT UNIQUE,

            support_email TEXT,
            support_phone TEXT,
            website_url TEXT,

            company_description TEXT,
            faq_context TEXT,
            widget_color TEXT,

            plan_name TEXT,
            subscription_status TEXT,

            trial_started_at INTEGER,
            trial_ends_at INTEGER,

            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,

            monthly_message_limit INTEGER,

            is_active INTEGER,
            created_at INTEGER
        )
        """)


        # USERS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_users (

            id TEXT PRIMARY KEY,
            tenant_id TEXT,

            email TEXT,
            password_hash TEXT,
            full_name TEXT,

            is_owner INTEGER,
            is_active INTEGER,

            created_at INTEGER
        )
        """)


        # LEADS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS leads (

            id TEXT PRIMARY KEY,
            tenant_id TEXT,

            name TEXT,
            email TEXT,
            phone TEXT,

            message TEXT,
            source TEXT,

            score INTEGER,
            priority TEXT,
            stage TEXT,
            notes TEXT,

            created_at INTEGER
        )
        """)


        # CHAT MESSAGES
        cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (

            id TEXT PRIMARY KEY,
            tenant_id TEXT,

            session_id TEXT,
            role TEXT,
            content TEXT,

            created_at INTEGER
        )
        """)


        # WEBSITE CRAWLER (NIEUW v42)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS website_pages (

            id TEXT PRIMARY KEY,
            tenant_id TEXT,

            page_url TEXT,
            page_text TEXT,

            created_at INTEGER
        )
        """)


        # USAGE
        cur.execute("""
        CREATE TABLE IF NOT EXISTS usage_events (

            id TEXT PRIMARY KEY,
            tenant_id TEXT,

            event_type TEXT,
            month_key TEXT,

            created_at INTEGER
        )
        """)


        # AUDIT
        cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (

            id TEXT PRIMARY KEY,
            tenant_id TEXT,

            actor_type TEXT,
            actor_id TEXT,

            action TEXT,
            target_type TEXT,
            target_id TEXT,

            meta_json TEXT,
            created_at INTEGER
        )
        """)

        conn.commit()


# =========================
# STARTUP
# =========================

def seed_default_tenant():

    with closing(get_db()) as conn:

        existing = conn.execute(
            "SELECT id FROM tenants WHERE slug=?",
            ("default",)
        ).fetchone()

        if existing:
            return

        tenant_id = str(uuid.uuid4())

        trial_start = now_ts()
        trial_end = now_ts() + (TRIAL_DAYS * 86400)

        conn.execute(
        """
        INSERT INTO tenants
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            tenant_id,

            DEFAULT_COMPANY_NAME,
            "default",
            generate_api_key(),

            "support@assistifyai.nl",
            "+31 6 00000000",
            "https://assistifyai.nl",

            DEFAULT_COMPANY_DESCRIPTION.strip(),
            DEFAULT_FAQ_CONTEXT.strip(),
            DEFAULT_WIDGET_COLOR,

            "starter",
            "active",

            trial_start,
            trial_end,

            "",
            "",

            500,

            1,
            now_ts()
        ))

        conn.execute(
        """
        INSERT INTO customer_users
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            str(uuid.uuid4()),
            tenant_id,
            "support@assistifyai.nl",
            generate_password_hash("changeme123"),
            "Owner",
            1,
            1,
            now_ts()
        ))

        conn.commit()


def ensure_startup():

    global _startup_done

    if _startup_done:
        return

    init_db()
    seed_default_tenant()

    _startup_done = True


# =========================
# LOOKUPS
# =========================

def get_tenant_by_api_key(api_key):

    with closing(get_db()) as conn:

        row = conn.execute(
            """
            SELECT *
            FROM tenants
            WHERE api_key = ?
            AND is_active = 1
            """,
            (api_key,)
        ).fetchone()

    return dict(row) if row else None


def get_tenant_by_slug(slug):

    with closing(get_db()) as conn:

        row = conn.execute(
            """
            SELECT *
            FROM tenants
            WHERE slug = ?
            """,
            (slug,)
        ).fetchone()

    return dict(row) if row else None


def get_customer_user_by_email_and_tenant(email, tenant_id):

    with closing(get_db()) as conn:

        row = conn.execute(
            """
            SELECT *
            FROM customer_users
            WHERE LOWER(email)=LOWER(?)
            AND tenant_id=?
            LIMIT 1
            """,
            ((email or "").strip(), tenant_id)
        ).fetchone()

    return dict(row) if row else None# =========================
# WEBSITE CRAWLER ENGINE
# =========================

def normalize_url(url):

    url = (url or "").strip()

    if not url:
        return ""

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    return url.rstrip("/")


def extract_same_domain_links(base_url, html):

    found = []
    base = normalize_url(base_url)

    matches = re.findall(r'href=["\\\'](.*?)["\\\']', html or "", flags=re.I)

    for href in matches:

        href = (href or "").strip()

        if not href:
            continue

        if href.startswith("#"):
            continue

        if href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
            continue

        if href.startswith("/"):
            full = base + href
        elif href.startswith("http://") or href.startswith("https://"):
            full = href.rstrip("/")
            if not full.startswith(base):
                continue
        else:
            full = base + "/" + href.lstrip("/")

        if full not in found:
            found.append(full)

    return found[:10]


def html_to_text(html):

    text = html or ""

    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = " ".join(text.split())

    return clamp_text(text, 25000)


def fetch_page_html(url):

    try:

        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        r = requests.get(url, headers=headers, timeout=10)

        if r.status_code != 200:
            return ""

        return r.text

    except Exception:
        return ""


def crawl_website_pages(start_url, max_pages=5):

    start_url = normalize_url(start_url)

    if not start_url:
        return []

    to_visit = [start_url]
    visited = set()
    pages = []

    while to_visit and len(pages) < max_pages:

        current = to_visit.pop(0)

        if current in visited:
            continue

        visited.add(current)

        html = fetch_page_html(current)

        if not html:
            continue

        text = html_to_text(html)

        if text:
            pages.append({
                "url": current,
                "text": text
            })

        links = extract_same_domain_links(start_url, html)

        for link in links:
            if link not in visited and link not in to_visit and len(to_visit) < 20:
                to_visit.append(link)

    return pages


def store_website_pages(tenant_id, pages):

    if not pages:
        return 0

    inserted = 0

    with closing(get_db()) as conn:

        for page in pages:

            page_url = clamp_text(page.get("url"), 1000)
            page_text = clamp_text(page.get("text"), 25000)

            if not page_url or not page_text:
                continue

            exists = conn.execute(
                """
                SELECT id
                FROM website_pages
                WHERE tenant_id = ?
                AND page_url = ?
                LIMIT 1
                """,
                (tenant_id, page_url)
            ).fetchone()

            if exists:
                conn.execute(
                    """
                    UPDATE website_pages
                    SET page_text = ?, created_at = ?
                    WHERE tenant_id = ?
                    AND page_url = ?
                    """,
                    (page_text, now_ts(), tenant_id, page_url)
                )
            else:
                conn.execute(
                    """
                    INSERT INTO website_pages
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        str(uuid.uuid4()),
                        tenant_id,
                        page_url,
                        page_text,
                        now_ts()
                    )
                )

            inserted += 1

        conn.commit()

    return inserted


def get_website_training_context(tenant_id, limit=8):

    safe_limit = max(1, min(int(limit), 20))

    with closing(get_db()) as conn:

        rows = conn.execute(
            """
            SELECT page_url, page_text
            FROM website_pages
            WHERE tenant_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (tenant_id, safe_limit)
        ).fetchall()

    chunks = []

    for row in rows:
        chunks.append(f"URL: {row['page_url']}\nInhoud: {row['page_text']}")

    return clamp_text("\n\n".join(chunks), 20000)


# =========================
# CHAT STORAGE
# =========================

def save_message(tenant_id, session_id, role, content):

    role = (role or "").strip().lower()

    if role not in ("user", "assistant"):
        role = "user"

    content = clamp_text(content or "", 20000)

    if not content:
        return

    with closing(get_db()) as conn:

        conn.execute(
            """
            INSERT INTO messages
            VALUES (?,?,?,?,?,?)
            """,
            (
                str(uuid.uuid4()),
                tenant_id,
                session_id,
                role,
                content,
                now_ts()
            )
        )

        conn.commit()


def build_openai_input(tenant_id, session_id, user_message):

    messages = []

    with closing(get_db()) as conn:

        rows = conn.execute(
            """
            SELECT role, content
            FROM messages
            WHERE tenant_id = ?
            AND session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (tenant_id, session_id, MAX_HISTORY_MESSAGES)
        ).fetchall()

    for r in reversed(rows):

        role = r["role"] if r["role"] in ("user", "assistant") else "user"

        messages.append({
            "role": role,
            "content": [{"type": "input_text", "text": r["content"]}]
        })

    messages.append({
        "role": "user",
        "content": [{"type": "input_text", "text": user_message}]
    })

    return messages


# =========================
# LEAD / SALES DETECTION
# =========================

def detect_lead_intent(message):

    text = (message or "").lower()

    keywords = [
        "prijs",
        "offerte",
        "kosten",
        "demo",
        "contact",
        "interesse",
        "afspraak",
        "bellen",
        "bel mij",
        "bel me",
        "meer informatie",
        "ik wil starten",
        "consult",
        "call plannen",
        "samenwerken"
    ]

    return any(word in text for word in keywords)


def detect_demo_intent(message):

    text = (message or "").lower()

    demo_words = [
        "demo",
        "demo aanvragen",
        "demo plannen",
        "meeting",
        "call",
        "inplannen",
        "afspraak",
        "kennismaking"
    ]

    return any(word in text for word in demo_words)


def get_qualification_prompt():

    return (
        "Mag ik 3 snelle dingen vragen zodat ik je beter kan helpen?\n"
        "1️⃣ Wat voor bedrijf heb je?\n"
        "2️⃣ Hoeveel aanvragen of klanten per maand ongeveer?\n"
        "3️⃣ Wil je vooral support automatiseren of ook sales?"
    )


def score_lead(name, email, phone, message):

    score = 0
    text = (message or "").lower()

    if name:
        score += 10

    if email and "@" in email:
        score += 20

    if phone:
        score += 10

    if detect_lead_intent(message):
        score += 25

    if detect_demo_intent(message):
        score += 20

    if len(text) > 80:
        score += 10

    if score >= 70:
        priority = "hot"
    elif score >= 40:
        priority = "warm"
    else:
        priority = "normal"

    return score, priority


# =========================
# AI
# =========================

def ask_ai(tenant, session_id, user_message):

    if not client:
        return "AI is nog niet geconfigureerd."

    try:

        website_context = get_website_training_context(tenant["id"])

        instructions = f"""
Je bent de AI klantenservice en sales assistent van {tenant.get("name") or DEFAULT_COMPANY_NAME}.

Doelen:
- vragen correct beantwoorden
- koopintentie herkennen
- bezoekers richting demo of contact sturen
- relevante gegevens vragen wanneer iemand interesse toont
- kort, duidelijk en professioneel antwoorden
- geen informatie verzinnen

Bedrijfsinformatie:
{tenant.get("company_description") or DEFAULT_COMPANY_DESCRIPTION}

FAQ:
{tenant.get("faq_context") or DEFAULT_FAQ_CONTEXT}

Website context:
{website_context}
"""

        response = client.responses.create(
            model=MODEL_NAME,
            instructions=instructions.strip(),
            input=build_openai_input(tenant["id"], session_id, user_message),
            max_output_tokens=MAX_OUTPUT_TOKENS
        )

        text = getattr(response, "output_text", "")

        if not text:
            text = "Sorry, ik kon nu geen antwoord genereren."

        if detect_demo_intent(user_message):
            text += "\n\nLaat gerust je naam, e-mail en eventueel telefoonnummer achter zodat we een demo kunnen plannen."
        elif detect_lead_intent(user_message):
            text += "\n\nAls je wilt kan ik een offerte of demo voor je regelen."
        elif "bedrijf" in (user_message or "").lower():
            text += "\n\n" + get_qualification_prompt()

        return clamp_text(text, MAX_ASSISTANT_REPLY_CHARS)

    except Exception:
        return "Er ging iets mis met de AI."# =========================
# LEAD PIPELINE
# =========================

VALID_LEAD_STAGES = ["new", "contacted", "qualified", "won", "lost"]


def normalize_lead_stage(stage):

    stage = (stage or "new").strip().lower()

    if stage not in VALID_LEAD_STAGES:
        return "new"

    return stage


def get_auto_stage_from_message(message):

    if detect_demo_intent(message):
        return "qualified"

    if detect_lead_intent(message):
        return "contacted"

    return "new"


# =========================
# LEADS / CRM
# =========================

def create_lead(tenant_id, name, email, phone, message, source="widget"):

    score, priority = score_lead(name, email, phone, message)
    stage = get_auto_stage_from_message(message)

    lead = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "name": clamp_text(name or "", 200),
        "email": clamp_text(email or "", 200),
        "phone": clamp_text(phone or "", 80),
        "message": clamp_text(message or "", 5000),
        "source": clamp_text(source or "widget", 80),
        "score": int(score),
        "priority": priority,
        "stage": stage,
        "notes": "",
        "created_at": now_ts()
    }

    with closing(get_db()) as conn:

        conn.execute(
        """
        INSERT INTO leads
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            lead["id"],
            lead["tenant_id"],
            lead["name"],
            lead["email"],
            lead["phone"],
            lead["message"],
            lead["source"],
            lead["score"],
            lead["priority"],
            lead["stage"],
            lead["notes"],
            lead["created_at"]
        ))

        conn.commit()

    return lead


def get_tenant_leads(tenant_id, query="", limit=500, stage=""):

    safe_limit = max(1, min(int(limit), 1000))
    q = f"%{(query or '').strip().lower()}%"
    stage = normalize_lead_stage(stage) if (stage or "").strip() else ""

    with closing(get_db()) as conn:

        if stage:
            rows = conn.execute(
                """
                SELECT *
                FROM leads
                WHERE tenant_id = ?
                AND stage = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (tenant_id, stage, safe_limit)
            ).fetchall()

        elif (query or "").strip():

            rows = conn.execute(
                """
                SELECT *
                FROM leads
                WHERE tenant_id = ?
                AND (
                    LOWER(name) LIKE ?
                    OR LOWER(email) LIKE ?
                    OR LOWER(message) LIKE ?
                )
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (tenant_id, q, q, q, safe_limit)
            ).fetchall()

        else:

            rows = conn.execute(
                """
                SELECT *
                FROM leads
                WHERE tenant_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (tenant_id, safe_limit)
            ).fetchall()

    return [dict(r) for r in rows]


def update_lead_stage(lead_id, stage):

    new_stage = normalize_lead_stage(stage)

    with closing(get_db()) as conn:

        conn.execute(
        """
        UPDATE leads
        SET stage = ?
        WHERE id = ?
        """,
        (new_stage, lead_id)
        )

        conn.commit()


def add_lead_note(lead_id, note):

    clean_note = clamp_text(note or "", 3000)

    if not clean_note:
        return

    with closing(get_db()) as conn:

        existing = conn.execute(
            """
            SELECT notes
            FROM leads
            WHERE id = ?
            """,
            (lead_id,)
        ).fetchone()

        current = existing["notes"] if existing and existing["notes"] else ""
        new_notes = (current + "\n" + clean_note).strip()

        conn.execute(
            """
            UPDATE leads
            SET notes = ?
            WHERE id = ?
            """,
            (new_notes, lead_id)
        )

        conn.commit()


def get_lead_pipeline_counts(tenant_id):

    counts = {stage: 0 for stage in VALID_LEAD_STAGES}

    with closing(get_db()) as conn:

        rows = conn.execute(
            """
            SELECT stage, COUNT(*) AS total
            FROM leads
            WHERE tenant_id = ?
            GROUP BY stage
            """,
            (tenant_id,)
        ).fetchall()

    for row in rows:

        stage = normalize_lead_stage(row["stage"])
        counts[stage] = int(row["total"])

    return counts


# =========================
# EMAIL
# =========================

def send_email(to_email, subject, html):

    if not SMTP_ENABLED:
        return False

    if not SMTP_HOST:
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM_EMAIL
    msg["To"] = to_email

    msg.attach(MIMEText(html, "html", "utf-8"))

    try:

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)

        try:
            server.starttls()
        except Exception:
            pass

        if SMTP_USERNAME:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)

        server.sendmail(SMTP_FROM_EMAIL, [to_email], msg.as_string())
        server.quit()

        return True

    except Exception:
        return False


def send_lead_notification(tenant, lead):

    support_email = (tenant.get("support_email") or "").strip()

    if not support_email:
        return False

    html = f"""
<h2>Nieuwe lead via Assistify</h2>

<p><b>Naam:</b> {lead.get('name') or ''}</p>
<p><b>Email:</b> {lead.get('email') or ''}</p>
<p><b>Telefoon:</b> {lead.get('phone') or ''}</p>

<p><b>Score:</b> {lead.get('score')}</p>
<p><b>Priority:</b> {lead.get('priority')}</p>
<p><b>Stage:</b> {lead.get('stage')}</p>

<p><b>Bericht:</b></p>

<p>{lead.get('message')}</p>
"""

    return send_email(
        support_email,
        f"Nieuwe {lead.get('priority')} lead",
        html
    )


def send_sales_followup(tenant, lead):

    email = (lead.get("email") or "").strip()

    if not email:
        return False

    company = tenant.get("name") or DEFAULT_COMPANY_NAME
    stage = normalize_lead_stage(lead.get("stage"))

    if stage == "qualified":

        subject = f"Demo plannen met {company}"

        html = f"""
<h2>Bedankt voor je interesse</h2>

<p>Hoi {lead.get('name') or ''},</p>

<p>Bedankt voor je aanvraag bij {company}. 
Je lijkt interesse te hebben in een demo of gesprek.</p>

<p>Reply gerust op deze mail met een moment dat jou uitkomt.</p>

<p>Groet,<br>{company}</p>
"""

    else:

        subject = f"Bedankt voor je aanvraag - {company}"

        html = f"""
<h2>Bedankt voor je bericht</h2>

<p>Hoi {lead.get('name') or ''},</p>

<p>We hebben je aanvraag goed ontvangen en nemen zo snel mogelijk contact met je op.</p>

<p>Groet,<br>{company}</p>
"""

    return send_email(email, subject, html)


# =========================
# ANALYTICS
# =========================

def get_tenant_stats(tenant_id):

    with closing(get_db()) as conn:

        lead_count = conn.execute(
            "SELECT COUNT(*) AS total FROM leads WHERE tenant_id = ?",
            (tenant_id,)
        ).fetchone()["total"]

        hot_leads = conn.execute(
            "SELECT COUNT(*) AS total FROM leads WHERE tenant_id = ? AND priority='hot'",
            (tenant_id,)
        ).fetchone()["total"]

        qualified = conn.execute(
            "SELECT COUNT(*) AS total FROM leads WHERE tenant_id = ? AND stage='qualified'",
            (tenant_id,)
        ).fetchone()["total"]

        won = conn.execute(
            "SELECT COUNT(*) AS total FROM leads WHERE tenant_id = ? AND stage='won'",
            (tenant_id,)
        ).fetchone()["total"]

        messages = conn.execute(
            "SELECT COUNT(*) AS total FROM messages WHERE tenant_id = ?",
            (tenant_id,)
        ).fetchone()["total"]

        sessions = conn.execute(
            "SELECT COUNT(DISTINCT session_id) AS total FROM messages WHERE tenant_id = ?",
            (tenant_id,)
        ).fetchone()["total"]

        pages = conn.execute(
            "SELECT COUNT(*) AS total FROM website_pages WHERE tenant_id = ?",
            (tenant_id,)
        ).fetchone()["total"]

    return {
        "lead_count_total": int(lead_count),
        "hot_leads": int(hot_leads),
        "qualified_leads": int(qualified),
        "won_leads": int(won),
        "message_count_total": int(messages),
        "session_count_total": int(sessions),
        "website_pages_trained": int(pages),
        "pipeline": get_lead_pipeline_counts(tenant_id)
    }


# =========================
# EMBED CODE
# =========================

def build_widget_embed_code(tenant):

    base = PUBLIC_APP_URL if PUBLIC_APP_URL else request.host_url.rstrip("/")

    return (
        f'<script src="{base}/widget.js" '
        f'data-api-base="{base}" '
        f'data-api-key="{tenant["api_key"]}" '
        f'data-title="{tenant["name"]}" '
        f'data-color="{tenant.get("widget_color") or DEFAULT_WIDGET_COLOR}" '
        f'data-welcome="Hoi! Waar kan ik je mee helpen?"></script>'
    )# =========================
# AUTH HELPERS
# =========================

def require_admin():
    return bool(session.get("admin_logged_in"))


def require_customer():
    return bool(session.get("customer_logged_in")) and bool(session.get("customer_tenant_id"))


def admin_forbidden():
    return jsonify({"ok": False, "error": "Niet geautoriseerd"}), 401


def customer_forbidden():
    return jsonify({"ok": False, "error": "Niet geautoriseerd"}), 401


# =========================
# HTML SHELL
# =========================

def render_shell(title, body):

    return f"""
<!DOCTYPE html>
<html>
<head>

<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">

<title>{title}</title>

<style>

body {{
font-family:Arial;
margin:0;
background:#0c1222;
color:white;
}}

.wrap {{
max-width:1200px;
margin:auto;
padding:24px;
}}

.topbar {{
display:flex;
justify-content:space-between;
margin-bottom:30px;
}}

.brand {{
font-weight:bold;
font-size:20px;
}}

.card {{
background:#121a33;
padding:20px;
border-radius:12px;
margin-bottom:20px;
}}

.grid {{
display:grid;
gap:20px;
}}

.grid-4 {{
grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
}}

.pipeline {{
display:grid;
grid-template-columns:repeat(5,1fr);
gap:12px;
}}

.pipeline-col {{
background:#0f172a;
padding:10px;
border-radius:8px;
}}

.pipeline-col h4 {{
margin:0 0 10px;
}}

.lead-card {{
background:#1e293b;
padding:10px;
border-radius:8px;
margin-bottom:8px;
font-size:14px;
}}

.stat {{
font-size:28px;
font-weight:bold;
}}

button {{
padding:10px 16px;
border:none;
border-radius:8px;
background:#6d5efc;
color:white;
cursor:pointer;
}}

button.secondary {{
background:#1e2a4a;
}}

input,textarea {{
width:100%;
padding:10px;
margin-bottom:10px;
background:#0f172a;
border:1px solid #334155;
color:white;
border-radius:8px;
}}

</style>

</head>
<body>

<div class="wrap">

<div class="topbar">

<div class="brand">Assistify AI</div>

<div>
<a href="/"><button class="secondary">Home</button></a>
<a href="/signup"><button class="secondary">Signup</button></a>
<a href="/dashboard/login"><button class="secondary">Dashboard</button></a>
<a href="/admin"><button class="secondary">Admin</button></a>
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

    return render_shell(
        "Assistify AI",
        f"""
<div class="card">

<h1>AI klantenservice voor je website</h1>

<p>
Beantwoord automatisch vragen van bezoekers en vang leads.
</p>

<button onclick="location.href='/signup'">
Start nu
</button>

</div>

<div class="grid grid-4">

<div class="card">
<h3>Starter</h3>
<div class="stat">€{PRICE_STARTER_EUR}</div>
</div>

<div class="card">
<h3>Pro</h3>
<div class="stat">€{PRICE_PRO_EUR}</div>
</div>

<div class="card">
<h3>Agency</h3>
<div class="stat">€{PRICE_AGENCY_EUR}</div>
</div>

</div>
"""
    )


# =========================
# SIGNUP
# =========================

def render_signup_html():

    return render_shell(
        "Signup",
        """
<div class="card">

<h2>Start met Assistify</h2>

<input id="email" placeholder="jij@bedrijf.nl">

<button onclick="signup('starter')">
Start
</button>

<p id="status"></p>

</div>

<script>

async function signup(plan){

status.innerText="..."

const r=await fetch("/signup/create-checkout",{
method:"POST",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({
email:email.value,
plan_name:plan
})
})

const d=await r.json()

if(d.url){
location.href=d.url
}else{
status.innerText=d.error
}

}

</script>
"""
    )


# =========================
# DASHBOARD LOGIN
# =========================

def render_dashboard_login_html():

    return render_shell(
        "Login",
        """
<div class="card">

<h2>Dashboard login</h2>

<input id="slug" placeholder="tenant slug">
<input id="email" placeholder="email">
<input id="password" type="password" placeholder="password">

<button onclick="login()">Login</button>

<p id="status"></p>

</div>

<script>

async function login(){

const r=await fetch("/dashboard/login",{
method:"POST",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({
tenant_slug:slug.value,
email:email.value,
password:password.value
})
})

const d=await r.json()

if(d.ok){
location.href="/dashboard"
}else{
status.innerText="Login mislukt"
}

}

</script>
"""
    )


# =========================
# DASHBOARD
# =========================

def render_dashboard_html():

    return render_shell(
        "Dashboard",
        """
<div class="grid grid-4">

<div class="card">
<div>Leads</div>
<div class="stat" id="statLeads">-</div>
</div>

<div class="card">
<div>Hot leads</div>
<div class="stat" id="statHot">-</div>
</div>

<div class="card">
<div>Qualified</div>
<div class="stat" id="statQualified">-</div>
</div>

<div class="card">
<div>Website pagina's</div>
<div class="stat" id="statPages">-</div>
</div>

</div>


<div class="card">

<h2>Website AI training</h2>

<input id="websiteUrl" placeholder="https://website.nl">

<button onclick="trainWebsite()">Train website</button>

<p id="trainStatus"></p>

</div>


<div class="card">

<h2>Widget installeren</h2>

<pre id="embed"></pre>

<button onclick="copyEmbed()">Kopieer code</button>

</div>


<div class="card">

<h2>Lead pipeline</h2>

<div class="pipeline">

<div class="pipeline-col">
<h4>New</h4>
<div id="stage_new"></div>
</div>

<div class="pipeline-col">
<h4>Contacted</h4>
<div id="stage_contacted"></div>
</div>

<div class="pipeline-col">
<h4>Qualified</h4>
<div id="stage_qualified"></div>
</div>

<div class="pipeline-col">
<h4>Won</h4>
<div id="stage_won"></div>
</div>

<div class="pipeline-col">
<h4>Lost</h4>
<div id="stage_lost"></div>
</div>

</div>

</div>


<div class="card">

<button onclick="logout()">Logout</button>

</div>


<script>

async function api(path,method="GET",body=null){

const r=await fetch(path,{
method:method,
credentials:"include",
headers:{"Content-Type":"application/json"},
body:body?JSON.stringify(body):null
})

return r.json()

}

async function loadDashboard(){

const d=await api("/dashboard/me")

statLeads.innerText=d.stats.lead_count_total
statHot.innerText=d.stats.hot_leads
statQualified.innerText=d.stats.qualified_leads
statPages.innerText=d.stats.website_pages_trained

const embedCode=await api("/dashboard/embed-code")

embed.innerText=embedCode.embed_code

loadPipeline()

}

async function loadPipeline(){

const d=await api("/dashboard/leads")

const stages={
new:stage_new,
contacted:stage_contacted,
qualified:stage_qualified,
won:stage_won,
lost:stage_lost
}

Object.values(stages).forEach(el=>el.innerHTML="")

d.leads.forEach(l=>{

const el=document.createElement("div")

el.className="lead-card"

el.innerHTML=`<b>${l.name||""}</b><br>${l.email||""}`

if(stages[l.stage]){
stages[l.stage].appendChild(el)
}

})

}

async function trainWebsite(){

trainStatus.innerText="Website analyseren..."

const d=await api(
"/dashboard/train-website",
"POST",
{url:websiteUrl.value}
)

trainStatus.innerText=d.message||"Klaar"

loadDashboard()

}

function copyEmbed(){

navigator.clipboard.writeText(embed.innerText)

}

async function logout(){

await fetch("/dashboard/logout",{method:"POST"})

location.href="/dashboard/login"

}

loadDashboard()

</script>
"""
    )


# =========================
# ADMIN PAGE
# =========================

def render_admin_html():

    return render_shell(
        "Admin",
        """
<div class="card">

<h2>Admin login</h2>

<input id="user" placeholder="username">
<input id="pass" type="password" placeholder="password">

<button onclick="login()">Login</button>

</div>

<script>

async function login(){

const r=await fetch("/admin/login",{
method:"POST",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({
username:user.value,
password:pass.value
})
})

const d=await r.json()

if(d.ok){
location.reload()
}

}

</script>
"""
    )# =========================
# WIDGET JS
# =========================

def render_widget_js():
    return r"""
(function(){

if(window.AssistifyWidgetLoaded) return;
window.AssistifyWidgetLoaded = true;

var s = document.currentScript;
if(!s) return;

var api = s.getAttribute("data-api-base") || "";
var key = s.getAttribute("data-api-key") || "";
var title = s.getAttribute("data-title") || "Chat";
var color = s.getAttribute("data-color") || "#6d5efc";
var welcome = s.getAttribute("data-welcome") || "Hoi! Waar kan ik je mee helpen?";

if(!api || !key) return;

var sid = localStorage.getItem("assistify_sid");
if(!sid){
  sid = "widget-" + Math.random().toString(36).slice(2);
  localStorage.setItem("assistify_sid", sid);
}

var btn = document.createElement("button");
btn.innerText = title;
btn.style.position = "fixed";
btn.style.right = "20px";
btn.style.bottom = "20px";
btn.style.zIndex = "999999";
btn.style.padding = "14px 18px";
btn.style.borderRadius = "999px";
btn.style.border = "none";
btn.style.background = color;
btn.style.color = "#fff";
btn.style.cursor = "pointer";
btn.style.boxShadow = "0 10px 24px rgba(0,0,0,.25)";

var box = document.createElement("div");
box.style.position = "fixed";
box.style.right = "20px";
box.style.bottom = "80px";
box.style.width = "360px";
box.style.height = "560px";
box.style.maxWidth = "calc(100vw - 40px)";
box.style.background = "#0f172a";
box.style.color = "#fff";
box.style.borderRadius = "16px";
box.style.display = "none";
box.style.zIndex = "999999";
box.style.overflow = "hidden";
box.style.border = "1px solid rgba(255,255,255,.08)";

box.innerHTML = `
<div style="padding:12px;background:${color};font-weight:bold">${title}</div>
<div id="assistify_messages" style="height:280px;overflow:auto;padding:10px"></div>
<div style="padding:10px;border-top:1px solid rgba(255,255,255,.1)">
  <textarea id="assistify_input" placeholder="Typ je bericht..." style="width:100%;height:70px;background:#111827;color:#fff;border:1px solid #334155;border-radius:8px;padding:10px"></textarea>
  <button id="assistify_send" style="width:100%;margin-top:6px;background:${color};border:none;padding:10px;color:#fff;border-radius:8px">Versturen</button>
</div>
<div style="padding:10px;border-top:1px solid rgba(255,255,255,.1)">
  <input id="assistify_name" placeholder="Naam" style="width:100%;margin-bottom:8px;background:#111827;color:#fff;border:1px solid #334155;border-radius:8px;padding:10px">
  <input id="assistify_email" placeholder="E-mail" style="width:100%;margin-bottom:8px;background:#111827;color:#fff;border:1px solid #334155;border-radius:8px;padding:10px">
  <input id="assistify_phone" placeholder="Telefoon (optioneel)" style="width:100%;margin-bottom:8px;background:#111827;color:#fff;border:1px solid #334155;border-radius:8px;padding:10px">
  <button id="assistify_lead" style="width:100%;background:#1e2a4a;border:none;padding:10px;color:#fff;border-radius:8px">Vraag demo / contact aan</button>
</div>
`;

document.body.appendChild(btn);
document.body.appendChild(box);

var messages = box.querySelector("#assistify_messages");
var input = box.querySelector("#assistify_input");
var send = box.querySelector("#assistify_send");
var leadBtn = box.querySelector("#assistify_lead");
var nameInput = box.querySelector("#assistify_name");
var emailInput = box.querySelector("#assistify_email");
var phoneInput = box.querySelector("#assistify_phone");

function bubble(t, user){
  var d = document.createElement("div");
  d.style.marginBottom = "8px";
  d.style.padding = "10px";
  d.style.borderRadius = "8px";
  d.style.whiteSpace = "pre-wrap";
  d.style.background = user ? "rgba(109,94,252,.3)" : "rgba(255,255,255,.08)";
  d.textContent = t;
  messages.appendChild(d);
  messages.scrollTop = messages.scrollHeight;
}

async function sendMsg(txt){

  bubble(txt, true);

  try{

    var r = await fetch(api + "/api/chat", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        api_key:key,
        message:txt,
        session_id:sid
      })
    });

    var d = await r.json();

    if(!r.ok){
      bubble(d.error || "Er ging iets mis", false);
      return;
    }

    bubble(d.reply || "...", false);

  }catch(e){
    bubble("Er ging iets mis", false);
  }
}

async function sendLead(){

  var name = (nameInput.value || "").trim();
  var email = (emailInput.value || "").trim();
  var phone = (phoneInput.value || "").trim();
  var message = (input.value || "").trim() || "Ik wil graag contact of een demo.";

  try{

    var r = await fetch(api + "/api/lead", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        api_key:key,
        name:name,
        email:email,
        phone:phone,
        message:message,
        source:"widget"
      })
    });

    var d = await r.json();

    if(!r.ok){
      bubble(d.error || "Lead versturen mislukt", false);
      return;
    }

    bubble("Top, je aanvraag is verstuurd. We nemen contact met je op.", false);

    nameInput.value = "";
    emailInput.value = "";
    phoneInput.value = "";

  }catch(e){
    bubble("Lead versturen mislukt", false);
  }
}

btn.onclick = function(){
  box.style.display = box.style.display === "none" ? "block" : "none";
};

send.onclick = function(){
  var t = input.value.trim();
  if(!t) return;
  input.value = "";
  sendMsg(t);
};

leadBtn.onclick = function(){
  sendLead();
};

input.addEventListener("keydown", function(e){
  if(e.key === "Enter" && !e.shiftKey){
    e.preventDefault();
    send.click();
  }
});

bubble(welcome, false);

})();
"""


# =========================
# BASIC ROUTES
# =========================

@app.before_request
def before_any_request():
    ensure_startup()


@app.route("/")
def home():
    return Response(render_homepage_html(), mimetype="text/html")


@app.route("/signup")
def signup_page():
    return Response(render_signup_html(), mimetype="text/html")


@app.route("/dashboard/login")
def dashboard_login_page():
    return Response(render_dashboard_login_html(), mimetype="text/html")


@app.route("/dashboard")
def dashboard_page():
    return Response(render_dashboard_html(), mimetype="text/html")


@app.route("/admin")
def admin_page():
    return Response(render_admin_html(), mimetype="text/html")


@app.route("/widget.js")
def widget_js():
    return Response(render_widget_js(), mimetype="application/javascript")


# =========================
# DASHBOARD AUTH
# =========================

@app.route("/dashboard/login", methods=["POST"])
def dashboard_login():

    data = json_body()

    user = verify_customer_login(
        data.get("email"),
        data.get("password"),
        data.get("tenant_slug")
    )

    if not user:
        return jsonify({"ok": False}), 401

    session.clear()
    session["customer_logged_in"] = True
    session["customer_user_id"] = user["id"]
    session["customer_tenant_id"] = user["tenant_id"]

    return jsonify({"ok": True})


@app.route("/dashboard/logout", methods=["POST"])
def dashboard_logout():
    session.clear()
    return jsonify({"ok": True})


# =========================
# DASHBOARD API
# =========================

@app.route("/dashboard/me")
def dashboard_me():

    if not require_customer():
        return customer_forbidden()

    tenant = get_tenant_by_api_key(get_tenant_by_slug("default")["api_key"]) if False else None
    tenant_id = session["customer_tenant_id"]

    with closing(get_db()) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM tenants
            WHERE id = ?
            LIMIT 1
            """,
            (tenant_id,)
        ).fetchone()

    if not row:
        return customer_forbidden()

    tenant = dict(row)

    return jsonify({
        "ok": True,
        "tenant": tenant,
        "stats": get_tenant_stats(tenant_id)
    })


@app.route("/dashboard/embed-code")
def dashboard_embed():

    if not require_customer():
        return customer_forbidden()

    tenant_id = session["customer_tenant_id"]

    with closing(get_db()) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM tenants
            WHERE id = ?
            LIMIT 1
            """,
            (tenant_id,)
        ).fetchone()

    if not row:
        return customer_forbidden()

    tenant = dict(row)

    return jsonify({
        "ok": True,
        "embed_code": build_widget_embed_code(tenant)
    })


@app.route("/dashboard/leads")
def dashboard_leads():

    if not require_customer():
        return customer_forbidden()

    tenant_id = session["customer_tenant_id"]
    q = request.args.get("q", "")
    stage = request.args.get("stage", "")

    return jsonify({
        "ok": True,
        "leads": get_tenant_leads(tenant_id, q, 500, stage)
    })


@app.route("/dashboard/train-website", methods=["POST"])
def train_website():

    if not require_customer():
        return customer_forbidden()

    data = json_body()
    url = clamp_text(data.get("url"), 1000)

    if not url:
        return jsonify({"error": "URL ontbreekt"}), 400

    tenant_id = session["customer_tenant_id"]

    pages = crawl_website_pages(url, max_pages=5)

    if not pages:
        return jsonify({"error": "Website kon niet worden gelezen"}), 400

    count = store_website_pages(tenant_id, pages)

    create_audit_log(
        tenant_id=tenant_id,
        actor_type="customer",
        actor_id=session.get("customer_user_id", ""),
        action="train_website",
        target_type="website",
        target_id=url,
        meta={"pages_saved": count}
    )

    return jsonify({
        "ok": True,
        "message": f"Website succesvol getraind ({count} pagina's)"
    })


# =========================
# CHAT API
# =========================

@app.route("/api/chat", methods=["POST"])
def api_chat():

    data = json_body()

    api_key = clamp_text(data.get("api_key"), 200)
    message = clamp_text(data.get("message"), 5000)

    if not api_key or not message:
        return jsonify({"error": "Bericht of API key ontbreekt"}), 400

    tenant = get_tenant_by_api_key(api_key)

    if not tenant:
        return jsonify({"error": "API key ongeldig"}), 401

    session_id = get_or_create_session_id(data)

    save_message(tenant["id"], session_id, "user", message)

    reply = ask_ai(tenant, session_id, message)

    save_message(tenant["id"], session_id, "assistant", reply)

    record_usage_event(tenant["id"], "message")

    return jsonify({
        "ok": True,
        "reply": reply
    })


# =========================
# LEAD API
# =========================

@app.route("/api/lead", methods=["POST"])
def api_lead():

    data = json_body()

    tenant = get_tenant_by_api_key(data.get("api_key"))

    if not tenant:
        return jsonify({"error": "API key ongeldig"}), 401

    lead = create_lead(
        tenant["id"],
        data.get("name"),
        data.get("email"),
        data.get("phone"),
        data.get("message"),
        data.get("source")
    )

    send_lead_notification(tenant, lead)
    send_sales_followup(tenant, lead)
    record_usage_event(tenant["id"], "lead")

    return jsonify({
        "ok": True
    })


# =========================
# STRIPE
# =========================

@app.route("/signup/create-checkout", methods=["POST"])
def create_checkout():

    if not stripe or not STRIPE_SECRET_KEY:
        return jsonify({"error": "Stripe niet actief"}), 500

    data = json_body()

    email = data.get("email")
    plan = data.get("plan_name")

    price_map = {
        "starter": STRIPE_PRICE_STARTER_MONTHLY,
        "pro": STRIPE_PRICE_PRO_MONTHLY,
        "agency": STRIPE_PRICE_AGENCY_MONTHLY
    }

    price_id = price_map.get(plan)

    if not price_id:
        return jsonify({"error": "Plan ongeldig"}), 400

    success_url = PUBLIC_APP_URL + "/dashboard/login"
    cancel_url = PUBLIC_APP_URL + "/signup"

    checkout = stripe.checkout.Session.create(
        mode="subscription",
        payment_method_types=["card"],
        customer_email=email,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url
    )

    return jsonify({"url": checkout.url})


# =========================
# ADMIN
# =========================

@app.route("/admin/login", methods=["POST"])
def admin_login():

    data = json_body()

    if data.get("username") == ADMIN_USERNAME and data.get("password") == ADMIN_PASSWORD:
        session["admin_logged_in"] = True
        return jsonify({"ok": True})

    return jsonify({"ok": False}), 401


# =========================
# SERVER
# =========================

if __name__ == "__main__":

    ensure_startup()

    print("Assistify AI", APP_VERSION)

    app.run(
        host="0.0.0.0",
        port=PORT
    )
