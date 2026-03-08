import os
import re
import sqlite3
from pathlib import Path
from functools import wraps
from urllib.parse import quote_plus

from flask import (
    Flask, request, jsonify, render_template_string,
    redirect, url_for, session, Response
)
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
import stripe

# =========================
# ENV / CONFIG
# =========================
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "verander-dit-geheim").strip()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "123456").strip()

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_PRICE_STARTER = os.getenv("STRIPE_PRICE_STARTER", "").strip()

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000").strip().rstrip("/")
DB_PATH = os.getenv("DB_PATH", "saas_v15.db").strip()
PORT = int(os.getenv("PORT", "5000"))

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY ontbreekt in je .env bestand.")

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# =========================
# DATABASE
# =========================
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS shops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        contact_email TEXT DEFAULT '',
        contact_phone TEXT DEFAULT '',
        website TEXT DEFAULT '',
        faq TEXT DEFAULT '',
        theme_color TEXT DEFAULT '#111827',
        plan TEXT DEFAULT 'starter',
        is_active INTEGER DEFAULT 1,
        stripe_customer_id TEXT DEFAULT '',
        stripe_subscription_id TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        price TEXT DEFAULT '',
        url TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(shop_id) REFERENCES shops(id)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        customer_name TEXT DEFAULT '',
        customer_email TEXT DEFAULT '',
        customer_phone TEXT DEFAULT '',
        interest TEXT DEFAULT '',
        full_message TEXT DEFAULT '',
        status TEXT DEFAULT 'nieuw',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(shop_id) REFERENCES shops(id)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(shop_id) REFERENCES shops(id)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(shop_id) REFERENCES shops(id)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS onboarding_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        checkout_session_id TEXT UNIQUE NOT NULL,
        shop_name TEXT NOT NULL,
        shop_slug TEXT NOT NULL,
        client_name TEXT NOT NULL,
        client_email TEXT NOT NULL,
        client_password_hash TEXT NOT NULL,
        company_email TEXT DEFAULT '',
        company_phone TEXT DEFAULT '',
        website TEXT DEFAULT '',
        description TEXT DEFAULT '',
        faq TEXT DEFAULT '',
        theme_color TEXT DEFAULT '#111827',
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()

    existing = c.execute("SELECT id FROM shops WHERE slug = ?", ("demo-shop",)).fetchone()
    if not existing:
        c.execute("""
        INSERT INTO shops (
            slug, name, description, contact_email, contact_phone, website,
            faq, theme_color, plan, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "demo-shop",
            "Demo Shop",
            "Wij verkopen kleding en accessoires en helpen klanten met vragen over producten, prijzen en bestellingen.",
            "info@demoshop.nl",
            "+31 6 12345678",
            "https://demoshop.nl",
            "Verzending binnen 1-3 werkdagen. Retouren binnen 14 dagen. Support via mail of telefoon.",
            "#111827",
            "pro",
            1
        ))
        shop_id = c.lastrowid

        demo_products = [
            ("Hoodie", "Comfortabele premium hoodie", "49.95", "https://demoshop.nl/hoodie"),
            ("T-Shirt", "Zacht katoenen T-shirt", "24.95", "https://demoshop.nl/tshirt"),
            ("Cap", "Stijlvolle verstelbare pet", "19.95", "https://demoshop.nl/cap"),
        ]

        for p in demo_products:
            c.execute("""
            INSERT INTO products (shop_id, name, description, price, url)
            VALUES (?, ?, ?, ?, ?)
            """, (shop_id, p[0], p[1], p[2], p[3]))

        c.execute("""
        INSERT INTO clients (shop_id, name, email, password_hash)
        VALUES (?, ?, ?, ?)
        """, (
            shop_id,
            "Demo Client",
            "client@demo.nl",
            generate_password_hash("demo123")
        ))
        conn.commit()

    conn.close()

# =========================
# AUTH HELPERS
# =========================
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper

def client_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("client_logged_in") or not session.get("client_shop_id"):
            return redirect(url_for("client_login"))
        return f(*args, **kwargs)
    return wrapper

# =========================
# HELPERS
# =========================
EMAIL_REGEX = r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)'
PHONE_REGEX = r'(\+?\d[\d\s\-\(\)]{7,}\d)'

def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "shop"

def unique_slug(base_slug: str) -> str:
    base_slug = slugify(base_slug)
    conn = get_conn()
    slug = base_slug
    i = 2
    while conn.execute("SELECT 1 FROM shops WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base_slug}-{i}"
        i += 1
    conn.close()
    return slug

def get_shop_by_slug(slug):
    conn = get_conn()
    shop = conn.execute("SELECT * FROM shops WHERE slug = ?", (slug,)).fetchone()
    conn.close()
    return shop

def get_shop_by_id(shop_id):
    conn = get_conn()
    shop = conn.execute("SELECT * FROM shops WHERE id = ?", (shop_id,)).fetchone()
    conn.close()
    return shop

def get_products(shop_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM products WHERE shop_id = ? ORDER BY id DESC",
        (shop_id,)
    ).fetchall()
    conn.close()
    return rows

def get_leads(shop_id):
    conn = get_conn()
    rows = conn.execute("""
    SELECT * FROM leads
    WHERE shop_id = ?
    ORDER BY id DESC
    """, (shop_id,)).fetchall()
    conn.close()
    return rows

def get_recent_messages(shop_id, session_id, limit=8):
    conn = get_conn()
    rows = conn.execute("""
    SELECT role, content
    FROM messages
    WHERE shop_id = ? AND session_id = ?
    ORDER BY id DESC
    LIMIT ?
    """, (shop_id, session_id, limit)).fetchall()
    conn.close()
    return list(reversed(rows))

def save_message(shop_id, session_id, role, content):
    conn = get_conn()
    conn.execute("""
    INSERT INTO messages (shop_id, session_id, role, content)
    VALUES (?, ?, ?, ?)
    """, (shop_id, session_id, role, content))
    conn.commit()
    conn.close()

def save_lead(shop_id, name="", email="", phone="", interest="", full_message=""):
    conn = get_conn()
    conn.execute("""
    INSERT INTO leads (shop_id, customer_name, customer_email, customer_phone, interest, full_message)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (shop_id, name, email, phone, interest, full_message))
    conn.commit()
    conn.close()

def detect_email(text):
    match = re.search(EMAIL_REGEX, text or "")
    return match.group(1) if match else ""

def detect_phone(text):
    match = re.search(PHONE_REGEX, text or "")
    return match.group(1).strip() if match else ""

def detect_name(text):
    text = text or ""
    patterns = [
        r"ik ben ([A-Za-zÀ-ÿ' -]{2,40})",
        r"mijn naam is ([A-Za-zÀ-ÿ' -]{2,40})",
        r"naam[:\s]+([A-Za-zÀ-ÿ' -]{2,40})"
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip().title()
    return ""

def likely_lead(text):
    t = (text or "").lower()
    keywords = [
        "prijs", "kosten", "offerte", "kopen", "bestellen",
        "interesse", "contact", "mail", "email", "telefoon",
        "bellen", "afspraak", "meer info", "aanvraag"
    ]
    return any(k in t for k in keywords)

def build_system_prompt(shop, products):
    product_text = ""
    for p in products[:25]:
        product_text += f"- {p['name']}: {p['description']} | prijs: {p['price']} | link: {p['url']}\n"

    return f"""
Jij bent een professionele AI klantenservice medewerker en verkoopassistent voor {shop['name']}.

Bedrijfsinformatie:
Naam: {shop['name']}
Beschrijving: {shop['description']}
E-mail: {shop['contact_email']}
Telefoon: {shop['contact_phone']}
Website: {shop['website']}
FAQ: {shop['faq']}

Producten:
{product_text}

Regels:
- Antwoord vriendelijk, menselijk en duidelijk.
- Antwoord in het Nederlands, tenzij de klant Engels gebruikt.
- Gebruik alleen info die hierboven staat.
- Verzin geen producten, prijzen of regels die je niet weet.
- Als iemand koopintentie toont, stuur richting aankoop, offerte of contact.
- Vraag subtiel door als dat logisch is.
- Houd antwoorden compact en professioneel.
"""

def get_ai_reply(shop, session_id, user_message):
    products = get_products(shop["id"])
    system_prompt = build_system_prompt(shop, products)
    history = get_recent_messages(shop["id"], session_id, limit=8)

    input_items = [
        {
            "role": "system",
            "content": [{"type": "input_text", "text": system_prompt}]
        }
    ]

    for msg in history:
        input_items.append({
            "role": msg["role"],
            "content": [{"type": "input_text", "text": msg["content"]}]
        })

    input_items.append({
        "role": "user",
        "content": [{"type": "input_text", "text": user_message}]
    })

    response = client.responses.create(
        model="gpt-5-mini",
        input=input_items
    )

    text = getattr(response, "output_text", None)
    if text and text.strip():
        return text.strip()

    return "Sorry, ik kon nu even geen goed antwoord geven."

def create_shop_from_onboarding(order_row, stripe_customer_id="", stripe_subscription_id=""):
    conn = get_conn()
    try:
        # al verwerkt?
        already = conn.execute(
            "SELECT * FROM onboarding_orders WHERE checkout_session_id = ?",
            (order_row["checkout_session_id"],)
        ).fetchone()
        if not already or already["status"] == "completed":
            conn.close()
            return

        slug = unique_slug(order_row["shop_slug"])

        c = conn.cursor()
        c.execute("""
        INSERT INTO shops (
            slug, name, description, contact_email, contact_phone, website,
            faq, theme_color, plan, is_active, stripe_customer_id, stripe_subscription_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            slug,
            order_row["shop_name"],
            order_row["description"],
            order_row["company_email"],
            order_row["company_phone"],
            order_row["website"],
            order_row["faq"],
            order_row["theme_color"] or "#111827",
            "starter",
            1,
            stripe_customer_id,
            stripe_subscription_id
        ))
        shop_id = c.lastrowid

        c.execute("""
        INSERT INTO clients (shop_id, name, email, password_hash)
        VALUES (?, ?, ?, ?)
        """, (
            shop_id,
            order_row["client_name"],
            order_row["client_email"],
            order_row["client_password_hash"]
        ))

        c.execute("""
        UPDATE onboarding_orders
        SET status = 'completed'
        WHERE checkout_session_id = ?
        """, (order_row["checkout_session_id"],))

        conn.commit()
    finally:
        conn.close()

# =========================
# HTML
# =========================
BASE_CSS = """
body { font-family: Arial, sans-serif; background:#f4f7fb; color:#111827; margin:0; }
.wrap { max-width:1100px; margin:0 auto; padding:32px 20px; }
.card { background:white; border-radius:18px; padding:24px; box-shadow:0 10px 30px rgba(0,0,0,0.08); margin-bottom:24px; }
.btn { display:inline-block; background:#111827; color:white; text-decoration:none; padding:12px 16px; border-radius:12px; margin-right:8px; border:none; cursor:pointer; }
input, textarea, select { width:100%; padding:12px; margin:8px 0 14px 0; border:1px solid #d1d5db; border-radius:10px; box-sizing:border-box; }
.item { padding:14px 0; border-bottom:1px solid #e5e7eb; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:24px; }
.grid3 { display:grid; grid-template-columns:repeat(3,1fr); gap:24px; }
@media (max-width: 900px) { .grid2, .grid3 { grid-template-columns:1fr; } }
"""

HOME_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<div class="wrap">
    <div class="card">
        <div class="card">
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
    <img src="https://cdn-icons-png.flaticon.com/512/4712/4712109.png" width="40" alt="Assistify AI logo">
    <h1 style="margin:0;">Assistify AI</h1>
  </div>

  <p>AI klantenservice voor websites</p>
  <p>Deployment + Stripe auto-onboarding + client dashboard + widget.</p>

  <a class="btn" href="/shop/demo-shop">Open demo shop</a>
  <a class="btn" href="/admin/login">Admin login</a>
  <a class="btn" href="/client/login">Client login</a>
  <a class="btn" href="/pricing">Pricing</a>
</div>
<img src="https://cdn-icons-png.flaticon.com/512/4712/4712109.png" width="40">

</div>
        <p>Deployment + Stripe auto-onboarding + client dashboard + widget.</p>
        <a class="btn" href="/shop/demo-shop">Open demo shop</a>
        <a class="btn" href="/admin/login">Admin login</a>
        <a class="btn" href="/client/login">Client login</a>
        <a class="btn" href="/pricing">Pricing</a>
    </div>

    <div class="card">
        <h2>Actieve shops</h2>
        {% for shop in shops %}
            <div class="item">
                <strong>{{ shop["name"] }}</strong> — /shop/{{ shop["slug"] }}<br>
                <a href="/shop/{{ shop['slug'] }}">Open shop</a>
            </div>
        {% endfor %}
    </div>
</div>
</body>
</html>
"""

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }}</title>
<style>{{ css }}
.center { min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px; }
.box { width:100%; max-width:420px; background:white; border-radius:18px; padding:24px; box-shadow:0 10px 30px rgba(0,0,0,0.08); }
.error { color:#b91c1c; margin-bottom:10px; }
</style>
</head>
<body>
<div class="center">
    <div class="box">
        <h2>{{ title }}</h2>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="POST">
            <input type="text" name="email_or_username" placeholder="{{ user_label }}" required>
            <input type="password" name="password" placeholder="Wachtwoord" required>
            <button class="btn" type="submit" style="width:100%;">Inloggen</button>
        </form>
    </div>
</div>
</body>
</html>
"""

PRICING_HTML = """
<!DOCTYPE html>
<html lang='nl'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width, initial-scale=1.0'>
<title>Pricing</title>
<style>{{ css }}</style>
</head>

<body>

<div class='wrap'>

<div class='card'>
<h1>Pricing</h1>
<p>Kies een plan en laat automatisch een shop + client account aanmaken.</p>
<a class='btn' href='/'>Home</a>
</div>

<div class='grid3'>

<div class='card'>
<h2>Starter</h2>
<p>€39 / maand</p>
<p>1 shop, AI chat, leads, widget</p>
<a class='btn' href='/signup?plan=starter'>Start nu</a>
</div>

<div class='card'>
<h2>Pro</h2>
<p>€79 / maand</p>
<p>Meer support en grotere setup</p>
<a class='btn' href='/signup?plan=pro'>Kies Pro</a>
</div>

<div class='card'>
<h2>Agency</h2>
<p>€199 / maand</p>
<p>Meerdere merken / shops</p>
<a class='btn' href='/signup?plan=agency'>Kies Agency</a>
</div>

</div>

</div>

</body>
</html>
"""

SIGNUP_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Start onboarding</title>
<style>{{ css }}</style>
</head>
<body>
<div class="wrap">
    <div class="card">
        <h1>Nieuwe klant onboarding</h1>
        <p>Na betaling wordt automatisch een shop + client account aangemaakt.</p>
        <a class="btn" href="/pricing">Terug</a>
    </div>

    {% if error %}<div class="card" style="color:#b91c1c;">{{ error }}</div>{% endif %}

    <div class="card">
        <form method="POST" action="/create-checkout-session">
            <input name="shop_name" placeholder="Bedrijfsnaam / shop naam" required>
            <input name="shop_slug" placeholder="Gewenste slug bijvoorbeeld mijn-shop" required>
            <input name="client_name" placeholder="Jouw naam" required>
            <input name="client_email" type="email" placeholder="Jouw e-mail" required>
            <input name="client_password" type="password" placeholder="Kies een wachtwoord" required>

            <input name="company_email" type="email" placeholder="Bedrijf e-mail">
            <input name="company_phone" placeholder="Bedrijf telefoon">
            <input name="website" placeholder="Website">
            <textarea name="description" placeholder="Korte bedrijfsbeschrijving"></textarea>
            <textarea name="faq" placeholder="FAQ / verzending / retour / regels"></textarea>
            <input name="theme_color" placeholder="#111827" value="#111827">

            <button class="btn" type="submit">Ga naar Stripe Checkout</button>
        </form>
    </div>
</div>
</body>
</html>
"""

SUCCESS_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Succes</title>
<style>{{ css }}</style>
</head>
<body>
<div class="wrap">
    <div class="card">
        <h1>Betaling ontvangen</h1>
        {% if ready %}
            <p>Je shop is aangemaakt.</p>
            <p><strong>Client login:</strong> <a href="/client/login">/client/login</a></p>
            <p><strong>Shop:</strong> <a href="/shop/{{ slug }}">/shop/{{ slug }}</a></p>
        {% else %}
            <p>De betaling is gelukt. De webhook verwerkt je onboarding nu. Refresh deze pagina over een paar seconden.</p>
        {% endif %}
        <a class="btn" href="/">Home</a>
    </div>
</div>
</body>
</html>
"""

ADMIN_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin Dashboard</title>
<style>{{ css }}</style>
</head>
<body>
<div class="wrap">
    <div class="card">
        <h1>Admin Dashboard</h1>
        <a class="btn" href="/">Home</a>
        <a class="btn" href="/admin/logout">Uitloggen</a>
    </div>

    <div class="card">
        <h2>Shops</h2>
        {% for shop in shops %}
            <div class="item">
                <strong>{{ shop["name"] }}</strong> ({{ shop["slug"] }}) - plan: {{ shop["plan"] }}<br>
                <a href="/shop/{{ shop['slug'] }}">Open</a> |
                <a href="/client/preview/{{ shop['slug'] }}">Beheer-preview</a>
            </div>
        {% endfor %}
    </div>

    <div class="card">
        <h2>Onboarding orders</h2>
        {% for row in onboarding %}
            <div class="item">
                <strong>{{ row["shop_name"] }}</strong> — {{ row["client_email"] }} — {{ row["status"] }}
            </div>
        {% endfor %}
    </div>
</div>
</body>
</html>
"""

CLIENT_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Client Dashboard</title>
<style>{{ css }}</style>
</head>
<body>
<div class="wrap">
    <div class="card">
        <h1>Client Dashboard - {{ shop["name"] }}</h1>
        <a class="btn" href="/shop/{{ shop['slug'] }}">Open live chat</a>
        <a class="btn" href="/client/logout">Uitloggen</a>
    </div>

    <div class="card">
        <h2>Shop info</h2>
        <form method="POST" action="/client/shop/update">
            <input name="name" value="{{ shop['name'] }}" placeholder="shop naam" required>
            <textarea name="description" placeholder="beschrijving">{{ shop['description'] }}</textarea>
            <input name="contact_email" value="{{ shop['contact_email'] }}" placeholder="e-mail">
            <input name="contact_phone" value="{{ shop['contact_phone'] }}" placeholder="telefoon">
            <input name="website" value="{{ shop['website'] }}" placeholder="website">
            <textarea name="faq" placeholder="faq / regels / verzendinfo">{{ shop['faq'] }}</textarea>
            <input name="theme_color" value="{{ shop['theme_color'] }}" placeholder="#111827">
            <button class="btn" type="submit">Opslaan</button>
        </form>
    </div>

    <div class="grid2">
        <div class="card">
            <h2>Product toevoegen</h2>
            <form method="POST" action="/client/product/add">
                <input name="name" placeholder="productnaam" required>
                <textarea name="description" placeholder="omschrijving"></textarea>
                <input name="price" placeholder="prijs">
                <input name="url" placeholder="product url">
                <button class="btn" type="submit">Toevoegen</button>
            </form>
        </div>

        <div class="card">
            <h2>Widget script</h2>
            <textarea rows="6" readonly><script src="{{ base_url }}/widget/{{ shop['slug'] }}.js"></script></textarea>
        </div>
    </div>

    <div class="card">
        <h2>Producten</h2>
        {% for p in products %}
            <div class="item">
                <strong>{{ p["name"] }}</strong><br>
                {{ p["description"] }}<br>
                € {{ p["price"] }}<br>
                {{ p["url"] }}
            </div>
        {% endfor %}
    </div>

    <div class="card">
        <h2>Leads</h2>
        {% for lead in leads %}
            <div class="item">
                <strong>{{ lead["customer_name"] or "Geen naam" }}</strong><br>
                E-mail: {{ lead["customer_email"] or "-" }}<br>
                Telefoon: {{ lead["customer_phone"] or "-" }}<br>
                Interesse: {{ lead["interest"] or "-" }}<br>
                Bericht: {{ lead["full_message"] or "-" }}<br>
                Status: {{ lead["status"] }}<br>
                Tijd: {{ lead["created_at"] }}<br><br>

                <form method="POST" action="/client/lead/{{ lead['id'] }}/status" style="max-width:260px;">
                    <select name="status">
                        <option value="nieuw" {% if lead["status"] == "nieuw" %}selected{% endif %}>nieuw</option>
                        <option value="opgevolgd" {% if lead["status"] == "opgevolgd" %}selected{% endif %}>opgevolgd</option>
                        <option value="gewonnen" {% if lead["status"] == "gewonnen" %}selected{% endif %}>gewonnen</option>
                        <option value="verloren" {% if lead["status"] == "verloren" %}selected{% endif %}>verloren</option>
                    </select>
                    <button class="btn" type="submit">Status opslaan</button>
                </form>
            </div>
        {% endfor %}
    </div>
</div>
</body>
</html>
"""

SHOP_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ shop["name"] }}</title>
<style>
body { margin:0; font-family:Arial, sans-serif; background:#f4f7fb; color:#111827; }
.wrap { max-width:1100px; margin:0 auto; padding:30px 20px; display:grid; grid-template-columns:1fr 1fr; gap:24px; }
.card { background:white; border-radius:18px; padding:24px; box-shadow:0 10px 30px rgba(0,0,0,0.08); }
.messages { height:420px; overflow-y:auto; background:#f9fafb; border:1px solid #e5e7eb; border-radius:12px; padding:14px; margin-bottom:14px; }
.msg { margin-bottom:12px; padding:12px 14px; border-radius:14px; max-width:80%; white-space:pre-wrap; line-height:1.5; }
.user { margin-left:auto; background:{{ shop["theme_color"] }}; color:white; border-bottom-right-radius:4px; }
.bot { margin-right:auto; background:white; border:1px solid #e5e7eb; border-bottom-left-radius:4px; }
input { width:100%; padding:14px; border:1px solid #d1d5db; border-radius:12px; box-sizing:border-box; }
button { margin-top:10px; background:{{ shop["theme_color"] }}; color:white; border:none; padding:12px 16px; border-radius:12px; cursor:pointer; }
.product { padding:12px 0; border-bottom:1px solid #e5e7eb; }
@media (max-width: 900px) { .wrap { grid-template-columns:1fr; } }
</style>
</head>
<body>
<div class="wrap">
    <div class="card">
        <h1>{{ shop["name"] }}</h1>
        <p>{{ shop["description"] }}</p>
        <p><strong>E-mail:</strong> {{ shop["contact_email"] }}<br>
        <strong>Telefoon:</strong> {{ shop["contact_phone"] }}<br>
        <strong>Website:</strong> {{ shop["website"] }}</p>

        <h3>Producten</h3>
        {% for p in products %}
            <div class="product">
                <strong>{{ p["name"] }}</strong><br>
                {{ p["description"] }}<br>
                € {{ p["price"] }}<br>
                <a href="{{ p['url'] }}" target="_blank">Product link</a>
            </div>
        {% endfor %}
    </div>

    <div class="card">
        <h2>AI chat</h2>
        <div id="messages" class="messages">
            <div class="msg bot">Hallo! Welkom bij {{ shop["name"] }}. Waar kan ik je mee helpen?</div>
        </div>
        <input id="messageInput" placeholder="Typ je bericht...">
        <button onclick="sendMessage()">Verstuur</button>
    </div>
</div>

<script>
const sessionId = "session_" + Math.random().toString(36).substring(2, 12);

function addMessage(text, sender) {
    const box = document.getElementById("messages");
    const div = document.createElement("div");
    div.className = "msg " + sender;
    div.textContent = text;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
}

async function sendMessage() {
    const input = document.getElementById("messageInput");
    const text = input.value.trim();
    if (!text) return;

    addMessage(text, "user");
    input.value = "";

    try {
        const res = await fetch("/api/chat/{{ shop['slug'] }}", {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({
                message: text,
                session_id: sessionId
            })
        });
        const data = await res.json();
        addMessage(data.reply || "Er ging iets mis.", "bot");
    } catch (e) {
        addMessage("Serverfout.", "bot");
    }
}

document.getElementById("messageInput").addEventListener("keydown", function(e) {
    if (e.key === "Enter") sendMessage();
});
</script>
</body>
</html>
"""

# =========================
# PUBLIC ROUTES
# =========================
@app.route("/")
def home():
    conn = get_conn()
    shops = conn.execute("SELECT * FROM shops WHERE is_active = 1 ORDER BY id DESC").fetchall()
    conn.close()
    return render_template_string(HOME_HTML, shops=shops, css=BASE_CSS)

@app.route("/pricing")
def pricing():
    return render_template_string(PRICING_HTML, css=BASE_CSS)

@app.route("/signup")
def signup():
    error = request.args.get("error", "")
    return render_template_string(SIGNUP_HTML, css=BASE_CSS, error=error)

@app.route("/success")
def success():
    session_id = (request.args.get("session_id") or "").strip()
    if not session_id:
        return render_template_string(SUCCESS_HTML, css=BASE_CSS, ready=False, slug="")

    conn = get_conn()
    order_row = conn.execute(
        "SELECT * FROM onboarding_orders WHERE checkout_session_id = ?",
        (session_id,)
    ).fetchone()

    ready = False
    slug = ""
    if order_row and order_row["status"] == "completed":
        slug = unique_slug(order_row["shop_slug"])  # placeholder
        # echte slug uit shop op naam/email zoeken is veiliger
        client_row = conn.execute("""
            SELECT shops.slug
            FROM clients
            JOIN shops ON shops.id = clients.shop_id
            WHERE clients.email = ?
            ORDER BY shops.id DESC
            LIMIT 1
        """, (order_row["client_email"],)).fetchone()
        if client_row:
            slug = client_row["slug"]
            ready = True

    conn.close()
    return render_template_string(SUCCESS_HTML, css=BASE_CSS, ready=ready, slug=slug)

@app.route("/shop/<slug>")
def shop_page(slug):
    shop = get_shop_by_slug(slug)
    if not shop or not shop["is_active"]:
        return "Shop niet gevonden", 404

    products = get_products(shop["id"])
    return render_template_string(SHOP_HTML, shop=shop, products=products)

@app.route("/api/chat/<slug>", methods=["POST"])
def api_chat(slug):
    shop = get_shop_by_slug(slug)
    if not shop or not shop["is_active"]:
        return jsonify({"error": "Shop niet gevonden"}), 404

    data = request.get_json(force=True)
    user_message = (data.get("message") or "").strip()
    session_id = (data.get("session_id") or "default-session").strip()

    if not user_message:
        return jsonify({"error": "Leeg bericht"}), 400

    save_message(shop["id"], session_id, "user", user_message)

    email = detect_email(user_message)
    phone = detect_phone(user_message)
    name = detect_name(user_message)

    if likely_lead(user_message) or email or phone or name:
        save_lead(
            shop["id"],
            name=name,
            email=email,
            phone=phone,
            interest=user_message[:200],
            full_message=user_message
        )

    try:
        reply = get_ai_reply(shop, session_id, user_message)
    except Exception:
        reply = (
            f"Sorry, ik kan nu even niet goed antwoorden. "
            f"Neem contact op via {shop['contact_email']} of {shop['contact_phone']}."
        )

    save_message(shop["id"], session_id, "assistant", reply)
    return jsonify({"reply": reply})

@app.route("/widget/<slug>.js")
def widget_js(slug):
    shop = get_shop_by_slug(slug)
    if not shop or not shop["is_active"]:
        return Response("console.error('Shop niet gevonden');", mimetype="application/javascript")

    js = f"""
(function () {{
    if (window.__aiChatWidgetLoaded) return;
    window.__aiChatWidgetLoaded = true;

    const bubble = document.createElement("button");
    bubble.innerText = "Chat met ons";
    bubble.style.position = "fixed";
    bubble.style.bottom = "20px";
    bubble.style.right = "20px";
    bubble.style.background = "{shop['theme_color']}";
    bubble.style.color = "white";
    bubble.style.border = "none";
    bubble.style.padding = "12px 16px";
    bubble.style.borderRadius = "999px";
    bubble.style.cursor = "pointer";
    bubble.style.zIndex = "999999";
    bubble.style.boxShadow = "0 10px 25px rgba(0,0,0,0.2)";

    const frame = document.createElement("iframe");
    frame.src = "{BASE_URL}/shop/{slug}";
    frame.style.position = "fixed";
    frame.style.bottom = "75px";
    frame.style.right = "20px";
    frame.style.width = "380px";
    frame.style.height = "650px";
    frame.style.border = "none";
    frame.style.borderRadius = "16px";
    frame.style.boxShadow = "0 10px 30px rgba(0,0,0,0.25)";
    frame.style.zIndex = "999998";
    frame.style.background = "white";
    frame.style.display = "none";

    bubble.onclick = function() {{
        frame.style.display = frame.style.display === "none" ? "block" : "none";
    }};

    document.body.appendChild(frame);
    document.body.appendChild(bubble);
}})();
"""
    return Response(js, mimetype="application/javascript")

# =========================
# STRIPE AUTO-ONBOARDING
# =========================
@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_STARTER:
        return redirect(url_for("signup", error="Stripe is nog niet ingesteld in .env"))

    shop_name = (request.form.get("shop_name") or "").strip()
    shop_slug = (request.form.get("shop_slug") or "").strip()
    client_name = (request.form.get("client_name") or "").strip()
    client_email = (request.form.get("client_email") or "").strip().lower()
    client_password = (request.form.get("client_password") or "").strip()
    company_email = (request.form.get("company_email") or "").strip()
    company_phone = (request.form.get("company_phone") or "").strip()
    website = (request.form.get("website") or "").strip()
    description = (request.form.get("description") or "").strip()
    faq = (request.form.get("faq") or "").strip()
    theme_color = (request.form.get("theme_color") or "#111827").strip() or "#111827"

    if not shop_name or not shop_slug or not client_name or not client_email or not client_password:
        return redirect(url_for("signup", error="Vul alle verplichte velden in"))

    try:
        checkout_session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_STARTER, "quantity": 1}],
            success_url=f"{BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/signup?error={quote_plus('Betaling geannuleerd')}",
            customer_email=client_email,
            metadata={
                "shop_name": shop_name,
                "shop_slug": slugify(shop_slug),
                "client_name": client_name,
                "client_email": client_email,
                "company_email": company_email,
                "company_phone": company_phone,
                "website": website,
                "description": description,
                "faq": faq,
                "theme_color": theme_color
            }
        )

        conn = get_conn()
        conn.execute("""
        INSERT INTO onboarding_orders (
            checkout_session_id, shop_name, shop_slug, client_name, client_email,
            client_password_hash, company_email, company_phone, website,
            description, faq, theme_color, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            checkout_session.id,
            shop_name,
            slugify(shop_slug),
            client_name,
            client_email,
            generate_password_hash(client_password),
            company_email,
            company_phone,
            website,
            description,
            faq,
            theme_color,
            "pending"
        ))
        conn.commit()
        conn.close()

        return redirect(checkout_session.url, code=303)

    except Exception as e:
        return redirect(url_for("signup", error=f"Stripe fout: {str(e)}"))

@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
        return "Stripe webhook niet ingesteld", 400

    payload = request.get_data(as_text=False)
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception:
        return "Webhook fout", 400

    if event["type"] == "checkout.session.completed":
        obj = event["data"]["object"]
        checkout_session_id = obj.get("id", "")
        customer_id = obj.get("customer", "") or ""
        subscription_id = obj.get("subscription", "") or ""

        conn = get_conn()
        row = conn.execute("""
            SELECT * FROM onboarding_orders
            WHERE checkout_session_id = ?
        """, (checkout_session_id,)).fetchone()
        conn.close()

        if row and row["status"] != "completed":
            create_shop_from_onboarding(
                row,
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id
            )

    return "ok", 200

# =========================
# ADMIN ROUTES
# =========================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        username = (request.form.get("email_or_username") or "").strip()
        password = (request.form.get("password") or "").strip()

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session.clear()
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        else:
            error = "Onjuiste inloggegevens."

    return render_template_string(
        LOGIN_HTML,
        title="Admin Login",
        user_label="Gebruikersnaam",
        error=error,
        css=BASE_CSS
    )

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    conn = get_conn()
    shops = conn.execute("SELECT * FROM shops ORDER BY id DESC").fetchall()
    onboarding = conn.execute("SELECT * FROM onboarding_orders ORDER BY id DESC").fetchall()
    conn.close()
    return render_template_string(
        ADMIN_DASHBOARD_HTML,
        shops=shops,
        onboarding=onboarding,
        css=BASE_CSS
    )

# =========================
# CLIENT ROUTES
# =========================
@app.route("/client/login", methods=["GET", "POST"])
def client_login():
    error = None
    if request.method == "POST":
        email = (request.form.get("email_or_username") or "").strip().lower()
        password = (request.form.get("password") or "").strip()

        conn = get_conn()
        client_row = conn.execute("""
        SELECT clients.*, shops.slug, shops.id AS shop_id
        FROM clients
        JOIN shops ON shops.id = clients.shop_id
        WHERE clients.email = ?
        """, (email,)).fetchone()
        conn.close()

        if client_row and check_password_hash(client_row["password_hash"], password):
            session.clear()
            session["client_logged_in"] = True
            session["client_id"] = client_row["id"]
            session["client_shop_id"] = client_row["shop_id"]
            session["client_shop_slug"] = client_row["slug"]
            return redirect(url_for("client_dashboard"))
        else:
            error = "Onjuiste inloggegevens."

    return render_template_string(
        LOGIN_HTML,
        title="Client Login",
        user_label="E-mail",
        error=error,
        css=BASE_CSS
    )

@app.route("/client/logout")
def client_logout():
    session.clear()
    return redirect(url_for("client_login"))

@app.route("/client/dashboard")
@client_required
def client_dashboard():
    shop = get_shop_by_id(session["client_shop_id"])
    if not shop:
        session.clear()
        return redirect(url_for("client_login"))

    products = get_products(shop["id"])
    leads = get_leads(shop["id"])

    return render_template_string(
        CLIENT_DASHBOARD_HTML,
        shop=shop,
        products=products,
        leads=leads,
        css=BASE_CSS,
        base_url=BASE_URL
    )

@app.route("/client/preview/<slug>")
@admin_required
def client_dashboard_preview(slug):
    shop = get_shop_by_slug(slug)
    if not shop:
        return "Shop niet gevonden", 404

    products = get_products(shop["id"])
    leads = get_leads(shop["id"])

    return render_template_string(
        CLIENT_DASHBOARD_HTML,
        shop=shop,
        products=products,
        leads=leads,
        css=BASE_CSS,
        base_url=BASE_URL
    )

@app.route("/client/shop/update", methods=["POST"])
@client_required
def client_shop_update():
    shop_id = session["client_shop_id"]

    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    contact_email = (request.form.get("contact_email") or "").strip()
    contact_phone = (request.form.get("contact_phone") or "").strip()
    website = (request.form.get("website") or "").strip()
    faq = (request.form.get("faq") or "").strip()
    theme_color = (request.form.get("theme_color") or "#111827").strip() or "#111827"

    conn = get_conn()
    conn.execute("""
    UPDATE shops
    SET name = ?, description = ?, contact_email = ?, contact_phone = ?, website = ?, faq = ?, theme_color = ?
    WHERE id = ?
    """, (name, description, contact_email, contact_phone, website, faq, theme_color, shop_id))
    conn.commit()
    conn.close()

    return redirect(url_for("client_dashboard"))

@app.route("/client/product/add", methods=["POST"])
@client_required
def client_add_product():
    shop_id = session["client_shop_id"]

    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    price = (request.form.get("price") or "").strip()
    url = (request.form.get("url") or "").strip()

    if not name:
        return "Productnaam is verplicht", 400

    conn = get_conn()
    conn.execute("""
    INSERT INTO products (shop_id, name, description, price, url)
    VALUES (?, ?, ?, ?, ?)
    """, (shop_id, name, description, price, url))
    conn.commit()
    conn.close()

    return redirect(url_for("client_dashboard"))

@app.route("/client/lead/<int:lead_id>/status", methods=["POST"])
@client_required
def client_lead_status(lead_id):
    status = (request.form.get("status") or "nieuw").strip()
    allowed = {"nieuw", "opgevolgd", "gewonnen", "verloren"}
    if status not in allowed:
        status = "nieuw"

    conn = get_conn()
    lead = conn.execute("SELECT shop_id FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        return "Lead niet gevonden", 404

    if int(lead["shop_id"]) != int(session["client_shop_id"]):
        conn.close()
        return "Geen toegang", 403

    conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
    conn.commit()
    conn.close()

    return redirect(url_for("client_dashboard"))

# =========================
# START
# =========================
if __name__ == "__main__":
    init_db()

    app.run(debug=True, host="0.0.0.0", port=PORT)





