import os
import sqlite3
import secrets
from flask import Flask, request, redirect, session, render_template_string, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "assistify-v2-secret-change-this")

DB_PATH = "database.db"


# ======================
# DATABASE
# ======================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        plan TEXT DEFAULT 'Starter',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT DEFAULT 'Mijn chatbot',
        website_url TEXT DEFAULT '',
        bot_token TEXT UNIQUE NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    conn.commit()
    conn.close()


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def get_user_bot(user_id):
    conn = get_db()
    bot = conn.execute("SELECT * FROM bots WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return bot


def get_all_shops():
    conn = get_db()
    try:
        shops = conn.execute("SELECT name, slug FROM shops").fetchall()
    except sqlite3.OperationalError:
        shops = []
    conn.close()
    return shops


# ======================
# STYLES
# ======================

BASE_STYLE = """
<style>
  * { box-sizing: border-box; }

  body {
    margin: 0;
    font-family: Arial, Helvetica, sans-serif;
    background: #eef1f5;
    color: #0f172a;
  }

  .container {
    max-width: 1200px;
    margin: 0 auto;
    padding: 24px;
  }

  .navbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 18px 0 28px 0;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
    text-decoration: none;
    color: #0f172a;
    font-size: 28px;
    font-weight: 800;
  }

  .brand img {
    width: 42px;
    height: 42px;
  }

  .nav-links {
    display: flex;
    gap: 14px;
    align-items: center;
    flex-wrap: wrap;
  }

  .nav-links a {
    text-decoration: none;
    color: #0f172a;
    font-weight: 700;
    padding: 10px 16px;
    border-radius: 12px;
  }

  .nav-links a.primary {
    background: #0f172a;
    color: white;
  }

  .nav-links a:hover {
    opacity: 0.92;
  }

  .card {
    background: white;
    border-radius: 28px;
    padding: 40px;
    box-shadow: 0 8px 30px rgba(15, 23, 42, 0.06);
    margin-bottom: 24px;
  }

  .hero {
    display: grid;
    grid-template-columns: 1.2fr 0.8fr;
    gap: 28px;
    align-items: center;
  }

  .hero h1 {
    font-size: 64px;
    line-height: 1.02;
    margin: 0 0 16px 0;
  }

  .hero p {
    font-size: 20px;
    color: #475569;
    line-height: 1.6;
    margin: 0 0 16px 0;
  }

  .hero-buttons {
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
    margin-top: 28px;
  }

  .btn {
    display: inline-block;
    text-decoration: none;
    border: none;
    cursor: pointer;
    padding: 15px 24px;
    border-radius: 14px;
    font-weight: 800;
    font-size: 16px;
  }

  .btn-dark {
    background: #0f172a;
    color: white;
  }

  .btn-purple {
    background: #6d68f6;
    color: white;
  }

  .btn-light {
    background: #f1f5f9;
    color: #0f172a;
  }

  .demo-box {
    background: linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%);
    border: 1px solid #e5e7eb;
    border-radius: 24px;
    padding: 24px;
  }

  .demo-title {
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 0.08em;
    color: #6d68f6;
    text-transform: uppercase;
    margin-bottom: 14px;
  }

  .chat-ui {
    background: #ffffff;
    border-radius: 20px;
    padding: 18px;
    box-shadow: 0 6px 18px rgba(15, 23, 42, 0.06);
  }

  .bubble {
    padding: 14px 16px;
    border-radius: 16px;
    margin-bottom: 12px;
    line-height: 1.5;
    font-size: 15px;
    max-width: 92%;
  }

  .bubble.bot {
    background: #eef2f7;
    color: #0f172a;
  }

  .bubble.user {
    background: #0f172a;
    color: white;
    margin-left: auto;
  }

  .section-title {
    font-size: 34px;
    margin: 0 0 12px 0;
  }

  .section-subtitle {
    font-size: 18px;
    color: #64748b;
    margin-bottom: 26px;
  }

  .grid-4 {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 20px;
  }

  .grid-3 {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 20px;
  }

  .mini-card {
    background: #f8fafc;
    border: 1px solid #e5e7eb;
    border-radius: 22px;
    padding: 24px;
  }

  .mini-card h3 {
    margin: 0 0 10px 0;
    font-size: 22px;
  }

  .mini-card p {
    margin: 0;
    color: #475569;
    line-height: 1.6;
  }

  .step {
    width: 38px;
    height: 38px;
    border-radius: 999px;
    background: #0f172a;
    color: white;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    margin-bottom: 14px;
  }

  .pricing-card {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 24px;
    padding: 26px;
    position: relative;
  }

  .pricing-card.featured {
    border: 2px solid #6d68f6;
    box-shadow: 0 12px 30px rgba(109, 104, 246, 0.10);
  }

  .badge {
    position: absolute;
    top: 18px;
    right: 18px;
    background: #6d68f6;
    color: white;
    font-size: 12px;
    font-weight: 800;
    padding: 7px 10px;
    border-radius: 999px;
  }

  .pricing-card h3 {
    margin: 0 0 10px 0;
    font-size: 28px;
  }

  .price {
    font-size: 42px;
    font-weight: 800;
    margin: 0 0 16px 0;
  }

  .price span {
    font-size: 16px;
    color: #64748b;
    font-weight: 500;
  }

  .pricing-card ul {
    margin: 0 0 24px 0;
    padding-left: 18px;
    line-height: 1.9;
    color: #475569;
  }

  .form-wrap {
    max-width: 520px;
    margin: 60px auto;
  }

  .form-card {
    background: white;
    border-radius: 24px;
    padding: 32px;
    box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
  }

  .form-card h1 {
    margin: 0 0 8px 0;
    font-size: 38px;
  }

  .form-card p {
    color: #64748b;
  }

  input, textarea {
    width: 100%;
    padding: 14px 16px;
    border-radius: 14px;
    border: 1px solid #dbe2ea;
    font-size: 16px;
    margin: 10px 0;
    box-sizing: border-box;
  }

  .error {
    background: #fee2e2;
    color: #991b1b;
    padding: 12px 14px;
    border-radius: 12px;
    margin-bottom: 14px;
  }

  .success {
    background: #dcfce7;
    color: #166534;
    padding: 12px 14px;
    border-radius: 12px;
    margin-bottom: 14px;
  }

  .dashboard-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
  }

  .muted {
    color: #64748b;
  }

  .pill {
    display: inline-block;
    padding: 8px 12px;
    border-radius: 999px;
    background: #e2e8f0;
    font-size: 14px;
    font-weight: 800;
  }

  .code-box {
    background: #0f172a;
    color: white;
    border-radius: 16px;
    padding: 16px;
    overflow-x: auto;
    font-family: monospace;
    font-size: 14px;
    line-height: 1.6;
  }

  .footer {
    text-align: center;
    color: #64748b;
    padding: 10px 0 36px 0;
    font-size: 14px;
  }

  @media (max-width: 1000px) {
    .hero, .grid-4, .grid-3, .dashboard-grid {
      grid-template-columns: 1fr;
    }

    .hero h1 {
      font-size: 46px;
    }
  }

  @media (max-width: 640px) {
    .navbar {
      flex-direction: column;
      align-items: flex-start;
      gap: 16px;
    }

    .hero h1 {
      font-size: 36px;
    }

    .card {
      padding: 24px;
    }
  }
</style>
"""


# ======================
# HTML
# ======================

HOME_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Assistify AI</title>
  {{ css|safe }}
</head>
<body>
  <div class="container">
    <div class="navbar">
      <a href="/" class="brand">
        <img src="https://cdn-icons-png.flaticon.com/512/4712/4712109.png" alt="Assistify AI logo">
        <span>Assistify AI</span>
      </a>
      <div class="nav-links">
        <a href="/pricing">Pricing</a>
        <a href="/signin" class="primary">Login</a>
      </div>
    </div>

    <div class="card hero">
      <div>
        <h1>AI klantenservice voor websites</h1>
        <p>Automatiseer support met AI en verkoop meer via je website.</p>
        <div class="hero-buttons">
          <a class="btn btn-dark" href="/signup">Start demo</a>
          <a class="btn btn-purple" href="/pricing">Bekijk prijzen</a>
        </div>
      </div>

      <div class="demo-box">
        <div class="demo-title">Demo chatbot</div>
        <div class="chat-ui">
          <div class="bubble bot">Hallo! Hoe kan ik je helpen?</div>
          <div class="bubble user">Wat kost jullie service?</div>
          <div class="bubble bot">Je kunt kiezen uit Starter, Pro of Agency. Wil je dat ik de verschillen laat zien?</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2 class="section-title">Features</h2>
      <p class="section-subtitle">Alles wat je nodig hebt om bezoekers automatisch te helpen en leads te verzamelen.</p>
      <div class="grid-4">
        <div class="mini-card">
          <h3>🤖 AI Chatbot</h3>
          <p>Automatische antwoorden voor bezoekers op basis van jouw website en kennis.</p>
        </div>
        <div class="mini-card">
          <h3>📈 Lead generatie</h3>
          <p>De AI verzamelt leads uit gesprekken en zet ze klaar in je dashboard.</p>
        </div>
        <div class="mini-card">
          <h3>⚡ Website training</h3>
          <p>Train AI op je website zodat antwoorden relevant en slim blijven.</p>
        </div>
        <div class="mini-card">
          <h3>💳 SaaS abonnementen</h3>
          <p>Werk met duidelijke plannen voor verschillende soorten klanten.</p>
        </div>
      </div>
    </div>

    <div class="card">
      <h2 class="section-title">Hoe het werkt</h2>
      <p class="section-subtitle">Binnen enkele minuten live op je website.</p>
      <div class="grid-3">
        <div class="mini-card">
          <div class="step">1</div>
          <h3>Voeg widget toe</h3>
          <p>Plaats de chatbot-widget op je website met één eenvoudige installatie.</p>
        </div>
        <div class="mini-card">
          <div class="step">2</div>
          <h3>Train op je content</h3>
          <p>Voeg je website en bedrijfsinformatie toe zodat AI goede antwoorden geeft.</p>
        </div>
        <div class="mini-card">
          <div class="step">3</div>
          <h3>AI helpt bezoekers</h3>
          <p>De chatbot beantwoordt vragen, helpt klanten en verzamelt leads.</p>
        </div>
      </div>
    </div>

    <div class="card">
      <h2 class="section-title">Actieve shops</h2>
      <p class="section-subtitle">Voorbeeldshops op je platform.</p>
      {% if shops %}
        {% for shop in shops %}
          <div class="mini-card" style="margin-bottom:16px;">
            <strong>{{ shop["name"] }}</strong> — /shop/{{ shop["slug"] }}<br><br>
            <a class="btn btn-light" href="/shop/{{ shop['slug'] }}">Open shop</a>
          </div>
        {% endfor %}
      {% else %}
        <div class="mini-card">
          <p>Nog geen actieve shops gevonden.</p>
        </div>
      {% endif %}
    </div>

    <div class="footer">© Assistify AI</div>
  </div>
</body>
</html>
"""

PRICING_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pricing - Assistify AI</title>
  {{ css|safe }}
</head>
<body>
  <div class="container">
    <div class="navbar">
      <a href="/" class="brand">
        <img src="https://cdn-icons-png.flaticon.com/512/4712/4712109.png" alt="Assistify AI logo">
        <span>Assistify AI</span>
      </a>
      <div class="nav-links">
        <a href="/">Home</a>
        <a href="/signin" class="primary">Login</a>
      </div>
    </div>

    <div class="card">
      <h1 class="section-title">Pricing</h1>
      <p class="section-subtitle">Kies het plan dat past bij jouw bedrijf.</p>

      <div class="grid-3">
        <div class="pricing-card">
          <h3>Starter</h3>
          <p class="price">€39.99 <span>/ maand</span></p>
          <ul>
            <li>1 website</li>
            <li>AI chatbot</li>
            <li>Dashboard</li>
          </ul>
          <a class="btn btn-dark" href="/signup">Start Starter</a>
        </div>

        <div class="pricing-card featured">
          <div class="badge">Populair</div>
          <h3>Pro</h3>
          <p class="price">€79.99 <span>/ maand</span></p>
          <ul>
            <li>5 websites</li>
            <li>AI training</li>
            <li>Analytics</li>
          </ul>
          <a class="btn btn-purple" href="/signup">Start Pro</a>
        </div>

        <div class="pricing-card">
          <h3>Agency</h3>
          <p class="price">€199.99 <span>/ maand</span></p>
          <ul>
            <li>Unlimited websites</li>
            <li>White label</li>
            <li>Priority support</li>
          </ul>
          <a class="btn btn-dark" href="/signup">Start Agency</a>
        </div>
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
  <title>Signup - Assistify AI</title>
  {{ css|safe }}
</head>
<body>
  <div class="container">
    <div class="form-wrap">
      <div class="form-card">
        <h1>Account aanmaken</h1>
        <p>Maak je Assistify AI account aan.</p>

        {% if error %}
          <div class="error">{{ error }}</div>
        {% endif %}

        <form method="POST">
          <input type="email" name="email" placeholder="E-mailadres" required>
          <input type="password" name="password" placeholder="Wachtwoord" required>
          <button class="btn btn-dark" type="submit">Maak account</button>
        </form>

        <p>Heb je al een account? <a href="/signin">Log in</a></p>
      </div>
    </div>
  </div>
</body>
</html>
"""

SIGNIN_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Signin - Assistify AI</title>
  {{ css|safe }}
</head>
<body>
  <div class="container">
    <div class="form-wrap">
      <div class="form-card">
        <h1>Login</h1>
        <p>Log in op je Assistify AI dashboard.</p>

        {% if error %}
          <div class="error">{{ error }}</div>
        {% endif %}

        <form method="POST">
          <input type="email" name="email" placeholder="E-mailadres" required>
          <input type="password" name="password" placeholder="Wachtwoord" required>
          <button class="btn btn-dark" type="submit">Login</button>
        </form>

        <p>Nog geen account? <a href="/signup">Maak account</a></p>
      </div>
    </div>
  </div>
</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dashboard - Assistify AI</title>
  {{ css|safe }}
</head>
<body>
  <div class="container">
    <div class="navbar">
      <a href="/" class="brand">
        <img src="https://cdn-icons-png.flaticon.com/512/4712/4712109.png" alt="Assistify AI logo">
        <span>Assistify AI</span>
      </a>
      <div class="nav-links">
        <a href="/pricing">Pricing</a>
        <a href="/logout" class="primary">Uitloggen</a>
      </div>
    </div>

    {% if success %}
      <div class="success">{{ success }}</div>
    {% endif %}

    <div class="dashboard-grid">
      <div class="card">
        <h2>Mijn account</h2>
        <p><strong>Email:</strong> {{ user["email"] }}</p>
        <p><strong>Plan:</strong> <span class="pill">{{ user["plan"] }}</span></p>
      </div>

      <div class="card">
        <h2>Mijn chatbot</h2>
        <p class="muted">Beheer hier je chatbot instellingen.</p>

        <form method="POST">
          <input type="text" name="bot_name" placeholder="Bot naam" value="{{ bot['name'] if bot else '' }}" required>
          <input type="text" name="website_url" placeholder="https://jouwwebsite.nl" value="{{ bot['website_url'] if bot else '' }}">
          <button class="btn btn-purple" type="submit">Opslaan</button>
        </form>
      </div>

      <div class="card">
        <h2>Installatie script</h2>
        <p class="muted">Plaats deze code op je website.</p>
        <div class="code-box">&lt;script src="https://www.assistifyai.nl/widget.js" data-bot="{{ bot['bot_token'] if bot else 'geen-token' }}"&gt;&lt;/script&gt;</div>
      </div>

      <div class="card">
        <h2>Bot status</h2>
        <p><strong>Bot naam:</strong> {{ bot['name'] if bot else 'Nog niet ingesteld' }}</p>
        <p><strong>Website URL:</strong> {{ bot['website_url'] if bot else 'Nog niet ingesteld' }}</p>
        <p><strong>Bot token:</strong> {{ bot['bot_token'] if bot else 'Nog niet aangemaakt' }}</p>
      </div>
    </div>
  </div>
</body>
</html>
"""


# ======================
# ROUTES
# ======================

@app.route("/")
def home():
    init_db()
    shops = get_all_shops()
    return render_template_string(HOME_HTML, css=BASE_STYLE, shops=shops)


@app.route("/pricing")
def pricing():
    return render_template_string(PRICING_HTML, css=BASE_STYLE)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    init_db()
    error = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not email or not password:
            error = "Vul alles in."
            return render_template_string(SIGNUP_HTML, css=BASE_STYLE, error=error)

        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()

        if existing:
            conn.close()
            error = "Dit account bestaat al."
            return render_template_string(SIGNUP_HTML, css=BASE_STYLE, error=error)

        password_hash = generate_password_hash(password)
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO users (email, password_hash, plan) VALUES (?, ?, ?)",
            (email, password_hash, "Starter")
        )
        user_id = cur.lastrowid

        bot_token = secrets.token_hex(8)
        cur.execute(
            "INSERT INTO bots (user_id, name, website_url, bot_token) VALUES (?, ?, ?, ?)",
            (user_id, "Mijn chatbot", "", bot_token)
        )

        conn.commit()
        conn.close()

        session["user_id"] = user_id
        return redirect("/dashboard")

    return render_template_string(SIGNUP_HTML, css=BASE_STYLE, error=error)


@app.route("/signin", methods=["GET", "POST"])
def signin():
    init_db()
    error = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()

        if not user or not check_password_hash(user["password_hash"], password):
            error = "Onjuiste login."
            return render_template_string(SIGNIN_HTML, css=BASE_STYLE, error=error)

        session["user_id"] = user["id"]
        return redirect("/dashboard")

    return render_template_string(SIGNIN_HTML, css=BASE_STYLE, error=error)


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    init_db()
    user = get_current_user()

    if not user:
        return redirect("/signin")

    success = None
    conn = get_db()

    if request.method == "POST":
        bot_name = request.form.get("bot_name", "").strip()
        website_url = request.form.get("website_url", "").strip()

        conn.execute(
            "UPDATE bots SET name = ?, website_url = ? WHERE user_id = ?",
            (bot_name or "Mijn chatbot", website_url, user["id"])
        )
        conn.commit()
        success = "Opgeslagen."

    bot = conn.execute("SELECT * FROM bots WHERE user_id = ?", (user["id"],)).fetchone()
    conn.close()

    return render_template_string(
        DASHBOARD_HTML,
        css=BASE_STYLE,
        user=user,
        bot=bot,
        success=success
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/signin")


@app.route("/api/widget-chat", methods=["POST"])
def api_widget_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    bot_token = (data.get("bot_token") or "").strip()

    if not message:
        return jsonify({"reply": "Stuur eerst een bericht."})

    conn = get_db()
    bot = conn.execute("SELECT * FROM bots WHERE bot_token = ?", (bot_token,)).fetchone()
    conn.close()

    if not bot:
        return jsonify({"reply": "Ongeldige chatbot koppeling."})

    bot_name = bot["name"] or "Assistify AI"

    text = message.lower()

    if "prijs" in text or "kost" in text or "pricing" in text:
        reply = "Je kunt kiezen uit Starter (€39.99), Pro (€79.99) en Agency (€199.99)."
    elif "hallo" in text or "hey" in text or "hoi" in text:
        reply = f"Hallo! Je spreekt met {bot_name}. Hoe kan ik je helpen?"
    elif "website" in text:
        reply = f"Deze chatbot is gekoppeld aan: {bot['website_url'] or 'nog geen website ingesteld'}."
    else:
        reply = f"Bedankt voor je bericht. Dit is de demo-chat van {bot_name}. In v3 koppelen we hier echte AI aan."

    return jsonify({"reply": reply})


@app.route("/widget.js")
def widget_js():
    js = r"""
(function () {
  if (window.AssistifyWidgetLoaded) return;
  window.AssistifyWidgetLoaded = true;

  var currentScript = document.currentScript;
  var botToken = currentScript ? currentScript.getAttribute("data-bot") : "";
  var apiBase = "https://www.assistifyai.nl";

  var style = document.createElement("style");
  style.innerHTML = `
    #assistify-bubble{
      position:fixed;
      right:20px;
      bottom:20px;
      width:62px;
      height:62px;
      border-radius:999px;
      background:#0f172a;
      color:#fff;
      display:flex;
      align-items:center;
      justify-content:center;
      font-size:28px;
      cursor:pointer;
      z-index:999999;
      box-shadow:0 10px 30px rgba(0,0,0,.20);
      font-family:Arial, sans-serif;
    }

    #assistify-panel{
      position:fixed;
      right:20px;
      bottom:94px;
      width:360px;
      max-width:calc(100vw - 24px);
      height:520px;
      background:#ffffff;
      border-radius:22px;
      box-shadow:0 20px 50px rgba(15,23,42,.22);
      overflow:hidden;
      display:none;
      flex-direction:column;
      z-index:999999;
      font-family:Arial, sans-serif;
      border:1px solid #e5e7eb;
    }

    #assistify-header{
      background:#0f172a;
      color:#fff;
      padding:16px 18px;
      display:flex;
      align-items:center;
      justify-content:space-between;
      font-weight:700;
      font-size:16px;
    }

    #assistify-close{
      cursor:pointer;
      font-size:20px;
      line-height:1;
      opacity:.9;
    }

    #assistify-messages{
      flex:1;
      overflow-y:auto;
      padding:16px;
      background:#f8fafc;
    }

    .assistify-msg{
      margin-bottom:12px;
      display:flex;
    }

    .assistify-msg.bot{
      justify-content:flex-start;
    }

    .assistify-msg.user{
      justify-content:flex-end;
    }

    .assistify-bubble-text{
      max-width:82%;
      padding:12px 14px;
      border-radius:16px;
      font-size:14px;
      line-height:1.5;
      word-wrap:break-word;
    }

    .assistify-msg.bot .assistify-bubble-text{
      background:#e2e8f0;
      color:#0f172a;
      border-bottom-left-radius:6px;
    }

    .assistify-msg.user .assistify-bubble-text{
      background:#0f172a;
      color:#fff;
      border-bottom-right-radius:6px;
    }

    #assistify-input-wrap{
      border-top:1px solid #e5e7eb;
      padding:12px;
      display:flex;
      gap:10px;
      background:#fff;
    }

    #assistify-input{
      flex:1;
      border:1px solid #dbe2ea;
      border-radius:14px;
      padding:12px 14px;
      font-size:14px;
      outline:none;
    }

    #assistify-send{
      border:none;
      background:#6d68f6;
      color:#fff;
      padding:12px 16px;
      border-radius:14px;
      font-weight:700;
      cursor:pointer;
    }

    #assistify-note{
      font-size:11px;
      color:#64748b;
      margin-top:8px;
      text-align:center;
      padding:0 10px 10px 10px;
    }

    @media (max-width: 480px){
      #assistify-panel{
        right:12px;
        left:12px;
        width:auto;
        bottom:88px;
        height:70vh;
      }
      #assistify-bubble{
        right:16px;
        bottom:16px;
      }
    }
  `;
  document.head.appendChild(style);

  var bubble = document.createElement("div");
  bubble.id = "assistify-bubble";
  bubble.innerHTML = "💬";

  var panel = document.createElement("div");
  panel.id = "assistify-panel";
  panel.innerHTML = `
    <div id="assistify-header">
      <span>Assistify AI</span>
      <span id="assistify-close">×</span>
    </div>
    <div id="assistify-messages"></div>
    <div id="assistify-input-wrap">
      <input id="assistify-input" type="text" placeholder="Typ je vraag...">
      <button id="assistify-send">Send</button>
    </div>
    <div id="assistify-note">Bot token: ${botToken || "geen token gevonden"}</div>
  `;

  document.body.appendChild(bubble);
  document.body.appendChild(panel);

  var messages = panel.querySelector("#assistify-messages");
  var input = panel.querySelector("#assistify-input");
  var sendBtn = panel.querySelector("#assistify-send");
  var closeBtn = panel.querySelector("#assistify-close");

  function addMessage(role, text){
    var row = document.createElement("div");
    row.className = "assistify-msg " + role;

    var inner = document.createElement("div");
    inner.className = "assistify-bubble-text";
    inner.textContent = text;

    row.appendChild(inner);
    messages.appendChild(row);
    messages.scrollTop = messages.scrollHeight;
  }

  function openPanel(){
    panel.style.display = "flex";
    if (!messages.dataset.started) {
      addMessage("bot", "Hallo! Hoe kan ik je helpen?");
      messages.dataset.started = "1";
    }
  }

  function closePanel(){
    panel.style.display = "none";
  }

  bubble.addEventListener("click", openPanel);
  closeBtn.addEventListener("click", closePanel);

  function sendMessage(){
    var text = input.value.trim();
    if (!text) return;

    addMessage("user", text);
    input.value = "";

    fetch(apiBase + "/api/widget-chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        message: text,
        bot_token: botToken
      })
    })
    .then(function(res){ return res.json(); })
    .then(function(data){
      addMessage("bot", data.reply || "Er ging iets mis.");
    })
    .catch(function(){
      addMessage("bot", "De chatbot kon nu geen antwoord geven.");
    });
  }

  sendBtn.addEventListener("click", sendMessage);
  input.addEventListener("keydown", function(e){
    if (e.key === "Enter") {
      sendMessage();
    }
  });
})();
"""
    return js, 200, {"Content-Type": "application/javascript"}


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
