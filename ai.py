import os import time import uuid import json import hmac import secrets import sqlite3 import smtplib import csv import io from datetime import timedelta from contextlib import closing from email.mime.text import MIMEText from email.mime.multipart import MIMEMultipart from dotenv import load_dotenv from flask import Flask, request, jsonify, Response, session from flask_cors import CORS from werkzeug.security import check_password_hash, generate_password_hash from openai import OpenAI load_dotenv() try: import stripe except Exception: stripe = None # ========================= # CONFIG # ========================= APP_VERSION = "v25.0" OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip() MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip() PORT = int(os.getenv("PORT", "5000")) DB_PATH = os.getenv("DB_PATH", "assistify.db").strip() SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32)).strip() SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true" SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax").strip() PERMANENT_SESSION_LIFETIME_HOURS = int(os.getenv("PERMANENT_SESSION_LIFETIME_HOURS", "24")) ALLOWED_ORIGINS = [ origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",") if origin.strip() ] ENABLE_LEAD_CAPTURE = os.getenv("ENABLE_LEAD_CAPTURE", "true").lower() == "true" ENABLE_HISTORY = os.getenv("ENABLE_HISTORY", "true").lower() == "true" RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")) RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "30")) MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "4000")) MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "12")) MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "700")) MAX_ASSISTANT_REPLY_CHARS = int(os.getenv("MAX_ASSISTANT_REPLY_CHARS", "5000")) TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", "72")) PRICE_STARTER_EUR = int(os.getenv("PRICE_STARTER_EUR", "49")) PRICE_PRO_EUR = int(os.getenv("PRICE_PRO_EUR", "149")) PRICE_AGENCY_EUR = int(os.getenv("PRICE_AGENCY_EUR", "399")) DEFAULT_COMPANY_NAME = os.getenv("COMPANY_NAME", "Assistify AI").strip() DEFAULT_COMPANY_TONE = os.getenv( "COMPANY_TONE", "vriendelijk, duidelijk, professioneel en behulpzaam" ).strip() DEFAULT_COMPANY_DESCRIPTION = os.getenv( "COMPANY_DESCRIPTION", "Wij helpen bedrijven met AI klantenservice, automatische support en snelle beantwoording van klantvragen." ).strip() DEFAULT_SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "support@jouwdomein.nl").strip() DEFAULT_SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+31 6 00000000").strip() DEFAULT_WEBSITE_URL = os.getenv("WEBSITE_URL", "https://jouwdomein.nl").strip() DEFAULT_FAQ_CONTEXT = os.getenv( "FAQ_CONTEXT", """ - Openingstijden support: maandag t/m vrijdag van 09:00 tot 17:00. - Reactietijd per e-mail: meestal binnen 24 uur. - Demo aanvragen kan via de website. - Prijzen verschillen per pakket en gebruik. - Technische support loopt via e-mail of het contactformulier. - Bij complexe problemen moet de klant naam, e-mail en probleemomschrijving achterlaten. """.strip() ) DEFAULT_WIDGET_COLOR = os.getenv("DEFAULT_WIDGET_COLOR", "#6d5efc").strip() DEFAULT_TENANT_API_KEY = os.getenv("DEFAULT_TENANT_API_KEY", "default-demo-key").strip() ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip() ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip() ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "").strip() PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip() SMTP_ENABLED = os.getenv("SMTP_ENABLED", "false").lower() == "true" SMTP_HOST = os.getenv("SMTP_HOST", "").strip() SMTP_PORT = int(os.getenv("SMTP_PORT", "587")) SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip() SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip() SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true" SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", DEFAULT_SUPPORT_EMAIL).strip() STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip() STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip() STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "").strip() STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", "").strip() STRIPE_PRICE_STARTER_MONTHLY = os.getenv("STRIPE_PRICE_STARTER_MONTHLY", "").strip() STRIPE_PRICE_PRO_MONTHLY = os.getenv("STRIPE_PRICE_PRO_MONTHLY", "").strip() STRIPE_PRICE_AGENCY_MONTHLY = os.getenv("STRIPE_PRICE_AGENCY_MONTHLY", "").strip() if stripe and STRIPE_SECRET_KEY: stripe.api_key = STRIPE_SECRET_KEY client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None rate_limit_store = {} _startup_done = False # ========================= # APP # ========================= app = Flask(__name__) app.secret_key = SECRET_KEY app.config["SESSION_COOKIE_HTTPONLY"] = True app.config["SESSION_COOKIE_SECURE"] = SESSION_COOKIE_SECURE app.config["SESSION_COOKIE_SAMESITE"] = SESSION_COOKIE_SAMESITE app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=PERMANENT_SESSION_LIFETIME_HOURS) if ALLOWED_ORIGINS == ["*"]: CORS( app, resources={ r"/widget/*": {"origins": "*"}, r"/signup/*": {"origins": "*"}, r"/dashboard/*": {"origins": "*"}, r"/invite/*": {"origins": "*"}, r"/reset-password/*": {"origins": "*"}, r"/admin/*": {"origins": "*"}, r"/stripe/*": {"origins": "*"}, }, supports_credentials=False ) else: CORS(app, resources={r"/*": {"origins": ALLOWED_ORIGINS}}, supports_credentials=True) # ========================= # DATABASE # ========================= def get_db(): conn = sqlite3.connect(DB_PATH, timeout=30) conn.row_factory = sqlite3.Row conn.execute("PRAGMA foreign_keys = ON") conn.execute("PRAGMA journal_mode = WAL") conn.execute("PRAGMA busy_timeout = 30000") return conn def init_db(): with closing(get_db()) as conn: cur = conn.cursor() cur.execute( """ CREATE TABLE IF NOT EXISTS tenants ( id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, api_key TEXT NOT NULL UNIQUE, company_tone TEXT NOT NULL, company_description TEXT NOT NULL, support_email TEXT NOT NULL, support_phone TEXT NOT NULL, website_url TEXT NOT NULL, faq_context TEXT NOT NULL, plan_name TEXT NOT NULL DEFAULT 'starter', subscription_status TEXT NOT NULL DEFAULT 'active', monthly_message_limit INTEGER NOT NULL DEFAULT 500, stripe_customer_id TEXT DEFAULT '', stripe_subscription_id TEXT DEFAULT '', billing_email TEXT DEFAULT '', billing_cycle TEXT DEFAULT 'monthly', widget_color TEXT NOT NULL DEFAULT '#6d5efc', is_active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL ) """ ) cur.execute( """ CREATE TABLE IF NOT EXISTS customer_users ( id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, email TEXT NOT NULL, password_hash TEXT NOT NULL, full_name TEXT NOT NULL DEFAULT '', is_owner INTEGER NOT NULL DEFAULT 1, is_active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, UNIQUE(tenant_id, email), FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE ) """ ) cur.execute( """ CREATE TABLE IF NOT EXISTS leads ( id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT, message TEXT NOT NULL, source TEXT NOT NULL, created_at INTEGER NOT NULL, FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE ) """ ) cur.execute( """ CREATE TABLE IF NOT EXISTS messages ( id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at INTEGER NOT NULL, FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE ) """ ) cur.execute( """ CREATE TABLE IF NOT EXISTS usage_events ( id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, event_type TEXT NOT NULL, month_key TEXT NOT NULL, meta_json TEXT, created_at INTEGER NOT NULL, FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE ) """ ) cur.execute( """ CREATE TABLE IF NOT EXISTS onboarding_tokens ( id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, token TEXT NOT NULL UNIQUE, email TEXT NOT NULL, is_used INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, used_at INTEGER DEFAULT 0, FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE ) """ ) cur.execute( """ CREATE TABLE IF NOT EXISTS invite_tokens ( id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, email TEXT NOT NULL, full_name TEXT NOT NULL DEFAULT '', token TEXT NOT NULL UNIQUE, invited_by_user_id TEXT NOT NULL, is_used INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, used_at INTEGER DEFAULT 0, FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE ) """ ) cur.execute( """ CREATE TABLE IF NOT EXISTS password_reset_tokens ( id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, email TEXT NOT NULL, token TEXT NOT NULL UNIQUE, is_used INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, used_at INTEGER DEFAULT 0, FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE, FOREIGN KEY (user_id) REFERENCES customer_users(id) ON DELETE CASCADE ) """ ) cur.execute( """ CREATE TABLE IF NOT EXISTS audit_logs ( id TEXT PRIMARY KEY, tenant_id TEXT, actor_type TEXT NOT NULL, actor_id TEXT DEFAULT '', action TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT DEFAULT '', meta_json TEXT, ip_address TEXT DEFAULT '', created_at INTEGER NOT NULL, FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE ) """ ) cur.execute("CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants(slug)") cur.execute("CREATE INDEX IF NOT EXISTS idx_tenants_api_key ON tenants(api_key)") cur.execute("CREATE INDEX IF NOT EXISTS idx_customer_users_tenant_id ON customer_users(tenant_id)") cur.execute("CREATE INDEX IF NOT EXISTS idx_customer_users_email ON customer_users(email)") cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_tenant_id ON messages(tenant_id)") cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)") cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_tenant_id ON leads(tenant_id)") cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_id ON audit_logs(tenant_id)") conn.commit() def seed_default_tenant(): with closing(get_db()) as conn: existing = conn.execute("SELECT id FROM tenants WHERE slug = ?", ("default",)).fetchone() if existing: return tenant_id = str(uuid.uuid4()) conn.execute( """ INSERT INTO tenants ( id, name, slug, api_key, company_tone, company_description, support_email, support_phone, website_url, faq_context, plan_name, subscription_status, monthly_message_limit, stripe_customer_id, stripe_subscription_id, billing_email, billing_cycle, widget_color, is_active, created_at ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) """, ( tenant_id, DEFAULT_COMPANY_NAME, "default", DEFAULT_TENANT_API_KEY, DEFAULT_COMPANY_TONE, DEFAULT_COMPANY_DESCRIPTION, DEFAULT_SUPPORT_EMAIL, DEFAULT_SUPPORT_PHONE, DEFAULT_WEBSITE_URL, DEFAULT_FAQ_CONTEXT, "starter", "active", 500, "", "", DEFAULT_SUPPORT_EMAIL, "monthly", DEFAULT_WIDGET_COLOR, 1, int(time.time()), ), ) conn.execute( """ INSERT INTO customer_users ( id, tenant_id, email, password_hash, full_name, is_owner, is_active, created_at ) VALUES (?, ?, ?, ?, ?, ?, ?, ?) """, ( str(uuid.uuid4()), tenant_id, DEFAULT_SUPPORT_EMAIL, generate_password_hash("changeme123"), "Default Owner", 1, 1, int(time.time()), ), ) conn.commit() def ensure_startup(): global _startup_done if _startup_done: return init_db() seed_default_tenant() _startup_done = True # ========================= # HELPERS # ========================= def now_ts() -> int: return int(time.time()) def clamp_text(value: str, max_len: int) -> str: text = (value or "").strip() return text[:max_len].rstrip() if len(text) > max_len else text def normalize_hex_color(value: str) -> str: value = (value or "").strip() if not value: return DEFAULT_WIDGET_COLOR if not value.startswith("#"): value = "#" + value if len(value) == 4: value = "#" + value[1] * 2 + value[2] * 2 + value[3] * 2 if len(value) != 7: return DEFAULT_WIDGET_COLOR valid = "0123456789abcdefABCDEF" for ch in value[1:]: if ch not in valid: return DEFAULT_WIDGET_COLOR return value.lower() def get_client_ip(): forwarded_for = request.headers.get("X-Forwarded-For", "") if forwarded_for: return forwarded_for.split(",")[0].strip() return request.remote_addr or "unknown" def json_body(): return request.get_json(silent=True) or {} def validate_email(email: str) -> bool: email = email.strip() return "@" in email and "." in email and len(email) >= 6 def normalize_slug(value: str) -> str: value = (value or "").strip().lower() allowed = "abcdefghijklmnopqrstuvwxyz0123456789-" result = [] for ch in value.replace(" ", "-").replace("_", "-"): if ch in allowed: result.append(ch) slug = "".join(result).strip("-") while "--" in slug: slug = slug.replace("--", "-") return slug[:60] def unique_slug(base: str) -> str: base_slug = normalize_slug(base) or "bedrijf" slug = base_slug counter = 1 while get_tenant_by_slug(slug): counter += 1 suffix = f"-{counter}" slug = (base_slug[: max(1, 60 - len(suffix))] + suffix).strip("-") return slug def generate_api_key() -> str: return "tenant_" + secrets.token_urlsafe(24) def get_or_create_session_id(data: dict) -> str: session_id = (data.get("session_id") or "").strip() return session_id[:120] if session_id else str(uuid.uuid4()) def detect_lead_intent(message: str) -> bool: text = (message or "").lower() lead_keywords = [ "prijs", "kosten", "demo", "offerte", "samenwerken", "contact", "bellen", "abonnement", "interesse", "pakket", "sales", ] return any(word in text for word in lead_keywords) def cleanup_rate_limit_store(): now = time.time() expired = [] for key, timestamps in list(rate_limit_store.items()): fresh = [ts for ts in timestamps if now - ts <= RATE_LIMIT_WINDOW_SECONDS] if fresh: rate_limit_store[key] = fresh else: expired.append(key) for key in expired: rate_limit_store.pop(key, None) def is_rate_limited(ip: str, tenant_key: str) -> bool: cleanup_rate_limit_store() now = time.time() key = f"{tenant_key}:{ip}" rate_limit_store.setdefault(key, []) recent = [ts for ts in rate_limit_store[key] if now - ts <= RATE_LIMIT_WINDOW_SECONDS] rate_limit_store[key] = recent if len(recent) >= RATE_LIMIT_MAX_REQUESTS: return True rate_limit_store[key].append(now) return False def extract_response_text(response) -> str: text = getattr(response, "output_text", None) if isinstance(text, str) and text.strip(): return clamp_text(text.strip(), MAX_ASSISTANT_REPLY_CHARS) try: output = getattr(response, "output", []) or [] parts = [] for item in output: content = getattr(item, "content", []) or [] for content_item in content: maybe_text = getattr(content_item, "text", None) if isinstance(maybe_text, str) and maybe_text.strip(): parts.append(maybe_text.strip()) return clamp_text("\n".join(parts).strip(), MAX_ASSISTANT_REPLY_CHARS) except Exception: return "" def send_email(to_email: str, subject: str, text_body: str): if not SMTP_ENABLED: return False, "SMTP staat uit." if not SMTP_HOST or not SMTP_USERNAME or not SMTP_PASSWORD or not SMTP_FROM_EMAIL: return False, "SMTP config ontbreekt." msg = MIMEMultipart() msg["From"] = SMTP_FROM_EMAIL msg["To"] = to_email msg["Subject"] = subject msg.attach(MIMEText(text_body, "plain", "utf-8")) server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) try: if SMTP_USE_TLS: server.starttls() server.login(SMTP_USERNAME, SMTP_PASSWORD) server.sendmail(SMTP_FROM_EMAIL, [to_email], msg.as_string()) finally: try: server.quit() except Exception: pass return True, None def send_invite_email(to_email: str, invite_url: str, tenant_name: str): subject = f"Uitnodiging voor {tenant_name}" body = f"""Je bent uitgenodigd voor {tenant_name}. Open deze link om je account aan te maken: {invite_url} Als je deze uitnodiging niet verwachtte, kun je dit bericht negeren. """ return send_email(to_email, subject, body) def send_password_reset_email(to_email: str, reset_url: str, tenant_name: str): subject = f"Wachtwoord reset voor {tenant_name}" body = f"""Je hebt een wachtwoord reset aangevraagd voor {tenant_name}. Open deze link om een nieuw wachtwoord in te stellen: {reset_url} Als jij dit niet was, kun je dit bericht negeren. """ return send_email(to_email, subject, body) def create_audit_log( tenant_id, actor_type: str, actor_id: str, action: str, target_type: str, target_id: str = "", meta=None, ip_address: str = "", ): with closing(get_db()) as conn: conn.execute( """ INSERT INTO audit_logs ( id, tenant_id, actor_type, actor_id, action, target_type, target_id, meta_json, ip_address, created_at ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) """, ( str(uuid.uuid4()), tenant_id, actor_type, actor_id or "", action, target_type, target_id or "", json.dumps(meta or {}, ensure_ascii=False), ip_address or "", now_ts(), ), ) conn.commit() def list_audit_logs(tenant_id=None, limit: int = 100): safe_limit = max(1, min(int(limit), 500)) with closing(get_db()) as conn: if tenant_id: rows = conn.execute( "SELECT * FROM audit_logs WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?", (tenant_id, safe_limit), ).fetchall() else: rows = conn.execute( "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?", (safe_limit,), ).fetchall() return [dict(r) for r in rows] def make_csv_response(filename: str, rows: list, fieldnames: list) -> Response: output = io.StringIO() writer = csv.DictWriter(output, fieldnames=fieldnames) writer.writeheader() for row in rows: writer.writerow({k: row.get(k, "") for k in fieldnames}) return Response( output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'}, ) def token_is_expired(created_at: int) -> bool: try: age = now_ts() - int(created_at or 0) return age > (TOKEN_TTL_HOURS * 3600) except Exception: return True def get_plan_limit(plan_name: str) -> int: mapping = {"starter": 500, "pro": 5000, "agency": 50000} return mapping.get((plan_name or "").strip().lower(), 500) def sync_tenant_subscription( tenant_id: str, plan_name: str = None, subscription_status: str = None, stripe_subscription_id: str = None, ): tenant = get_tenant_by_id(tenant_id) if not tenant: return None new_plan = (plan_name or tenant["plan_name"]).strip().lower() new_status = (subscription_status or tenant["subscription_status"]).strip().lower() new_sub_id = stripe_subscription_id if stripe_subscription_id is not None else tenant.get("stripe_subscription_id", "") monthly_limit = get_plan_limit(new_plan) is_active = 1 if new_status in ("active", "trialing") else 0 with closing(get_db()) as conn: conn.execute( """ UPDATE tenants SET plan_name = ?, subscription_status = ?, stripe_subscription_id = ?, monthly_message_limit = ?, is_active = ? WHERE id = ? """, (new_plan, new_status, new_sub_id, monthly_limit, is_active, tenant_id), ) conn.commit() return get_tenant_by_id(tenant_id) def get_tenant_by_api_key(api_key: str): with closing(get_db()) as conn: row = conn.execute("SELECT * FROM tenants WHERE api_key = ? AND is_active = 1", (api_key,)).fetchone() return dict(row) if row else None def get_tenant_by_slug(slug: str): with closing(get_db()) as conn: row = conn.execute("SELECT * FROM tenants WHERE slug = ?", (slug,)).fetchone() return dict(row) if row else None def get_tenant_by_id(tenant_id: str): with closing(get_db()) as conn: row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone() return dict(row) if row else None def get_tenant_by_stripe_customer_id(customer_id: str): with closing(get_db()) as conn: row = conn.execute("SELECT * FROM tenants WHERE stripe_customer_id = ?", (customer_id,)).fetchone() return dict(row) if row else None def get_tenant_by_stripe_subscription_id(subscription_id: str): with closing(get_db()) as conn: row = conn.execute("SELECT * FROM tenants WHERE stripe_subscription_id = ?", (subscription_id,)).fetchone() return dict(row) if row else None def get_customer_user_by_email_and_tenant(email: str, tenant_id: str): with closing(get_db()) as conn: row = conn.execute( "SELECT * FROM customer_users WHERE tenant_id = ? AND LOWER(email) = LOWER(?) LIMIT 1", (tenant_id, email.strip()), ).fetchone() return dict(row) if row else None def get_customer_user_by_email_global(email: str): with closing(get_db()) as conn: row = conn.execute( "SELECT * FROM customer_users WHERE LOWER(email) = LOWER(?) ORDER BY created_at ASC LIMIT 1", (email.strip(),), ).fetchone() return dict(row) if row else None def get_customer_user_by_id(user_id: str): with closing(get_db()) as conn: row = conn.execute("SELECT * FROM customer_users WHERE id = ?", (user_id,)).fetchone() return dict(row) if row else None def list_customer_users(tenant_id: str): with closing(get_db()) as conn: rows = conn.execute( """ SELECT id, tenant_id, email, full_name, is_owner, is_active, created_at FROM customer_users WHERE tenant_id = ? ORDER BY created_at ASC """, (tenant_id,), ).fetchall() return [dict(r) for r in rows] def create_customer_user(tenant_id: str, email: str, password: str, full_name: str = "", is_owner: bool = False): if not validate_email(email): return None, "Geldig e-mailadres ontbreekt." if len((password or "").strip()) < 8: return None, "Wachtwoord moet minimaal 8 tekens zijn." existing = get_customer_user_by_email_and_tenant(email, tenant_id) if existing: return None, "Gebruiker bestaat al." row = { "id": str(uuid.uuid4()), "tenant_id": tenant_id, "email": email.strip().lower(), "password_hash": generate_password_hash(password.strip()), "full_name": clamp_text(full_name or "", 200), "is_owner": 1 if is_owner else 0, "is_active": 1, "created_at": now_ts(), } with closing(get_db()) as conn: conn.execute( """ INSERT INTO customer_users ( id, tenant_id, email, password_hash, full_name, is_owner, is_active, created_at ) VALUES (?, ?, ?, ?, ?, ?, ?, ?) """, ( row["id"], row["tenant_id"], row["email"], row["password_hash"], row["full_name"], row["is_owner"], row["is_active"], row["created_at"], ), ) conn.commit() return get_customer_user_by_id(row["id"]), None def verify_customer_login(email: str, password: str, tenant_slug: str): tenant = get_tenant_by_slug(tenant_slug) if not tenant: return None with closing(get_db()) as conn: row = conn.execute( """ SELECT cu.*, t.name AS tenant_name, t.slug AS tenant_slug, t.is_active AS tenant_is_active FROM customer_users cu JOIN tenants t ON t.id = cu.tenant_id WHERE LOWER(cu.email) = LOWER(?) AND cu.tenant_id = ? LIMIT 1 """, (email.strip(), tenant["id"]), ).fetchone() if not row: return None user = dict(row) if int(user.get("is_active", 0)) != 1: return None if int(user.get("tenant_is_active", 0)) != 1: return None try: if check_password_hash(user["password_hash"], password): return user except Exception: return None return None def update_customer_password(user_id: str, new_password: str): if len((new_password or "").strip()) < 8: return False, "Wachtwoord moet minimaal 8 tekens zijn." with closing(get_db()) as conn: conn.execute( "UPDATE customer_users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password.strip()), user_id), ) conn.commit() return True, None def create_onboarding_token(tenant_id: str, email: str) -> str: token = secrets.token_urlsafe(32) with closing(get_db()) as conn: conn.execute( """ INSERT INTO onboarding_tokens (id, tenant_id, token, email, is_used, created_at, used_at) VALUES (?, ?, ?, ?, ?, ?, ?) """, (str(uuid.uuid4()), tenant_id, token, email.strip(), 0, now_ts(), 0), ) conn.commit() return token def get_onboarding_token_row(token: str): with closing(get_db()) as conn: row = conn.execute("SELECT * FROM onboarding_tokens WHERE token = ?", (token,)).fetchone() return dict(row) if row else None def mark_onboarding_token_used(token: str): with closing(get_db()) as conn: conn.execute("UPDATE onboarding_tokens SET is_used = 1, used_at = ? WHERE token = ?", (now_ts(), token)) conn.commit() def create_invite_token(tenant_id: str, email: str, full_name: str, invited_by_user_id: str) -> str: token = secrets.token_urlsafe(32) with closing(get_db()) as conn: conn.execute( """ INSERT INTO invite_tokens (id, tenant_id, email, full_name, token, invited_by_user_id, is_used, created_at, used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) """, ( str(uuid.uuid4()), tenant_id, email.strip().lower(), clamp_text(full_name or "", 200), token, invited_by_user_id, 0, now_ts(), 0, ), ) conn.commit() return token def get_invite_token_row(token: str): with closing(get_db()) as conn: row = conn.execute("SELECT * FROM invite_tokens WHERE token = ?", (token,)).fetchone() return dict(row) if row else None def mark_invite_token_used(token: str): with closing(get_db()) as conn: conn.execute("UPDATE invite_tokens SET is_used = 1, used_at = ? WHERE token = ?", (now_ts(), token)) conn.commit() def create_password_reset_token(tenant_id: str, user_id: str, email: str) -> str: token = secrets.token_urlsafe(32) with closing(get_db()) as conn: conn.execute( """ INSERT INTO password_reset_tokens (id, tenant_id, user_id, email, token, is_used, created_at, used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) """, (str(uuid.uuid4()), tenant_id, user_id, email.strip().lower(), token, 0, now_ts(), 0), ) conn.commit() return token def get_password_reset_token_row(token: str): with closing(get_db()) as conn: row = conn.execute("SELECT * FROM password_reset_tokens WHERE token = ?", (token,)).fetchone() return dict(row) if row else None def mark_password_reset_token_used(token: str): with closing(get_db()) as conn: conn.execute("UPDATE password_reset_tokens SET is_used = 1, used_at = ? WHERE token = ?", (now_ts(), token)) conn.commit() def record_usage_event(tenant_id: str, event_type: str, meta=None): with closing(get_db()) as conn: conn.execute( """ INSERT INTO usage_events (id, tenant_id, event_type, month_key, meta_json, created_at) VALUES (?, ?, ?, ?, ?, ?) """, ( str(uuid.uuid4()), tenant_id, event_type, time.strftime("%Y-%m", time.localtime()), json.dumps(meta or {}, ensure_ascii=False), now_ts(), ), ) conn.commit() def get_monthly_usage_count(tenant_id: str, event_type: str) -> int: mk = time.strftime("%Y-%m", time.localtime()) with closing(get_db()) as conn: row = conn.execute( "SELECT COUNT(*) AS total FROM usage_events WHERE tenant_id = ? AND event_type = ? AND month_key = ?", (tenant_id, event_type, mk), ).fetchone() return int(row["total"] if row else 0) def tenant_can_chat(tenant: dict): if not tenant: return False, "Tenant niet gevonden." if int(tenant.get("is_active", 0)) != 1: return False, "Tenant is niet actief." status = (tenant.get("subscription_status") or "").strip().lower() if status not in ("active", "trialing"): return False, "Abonnement is niet actief." monthly_limit = int(tenant.get("monthly_message_limit", 0) or 0) used = get_monthly_usage_count(tenant["id"], "message") if monthly_limit > 0 and used >= monthly_limit: return False, "Maandelijkse limiet bereikt." return True, None def build_system_prompt(tenant: dict) -> str: return f""" Je bent de AI klantenservice-assistent van {tenant['name']}. Doel: - Beantwoord klantvragen duidelijk en correct. - Schrijf in het Nederlands, tenzij de gebruiker een andere taal gebruikt. - Gebruik een {tenant['company_tone']} toon. - Houd antwoorden praktisch, kort en duidelijk. - Verzinnen mag niet. Als iets onbekend is, zeg dat eerlijk. - Verwijs bij complexe, gevoelige of account-specifieke zaken naar menselijke support. - Als iemand interesse toont in samenwerken, prijs, demo of contact, mag je vriendelijk sturen richting lead of contactaanvraag. Bedrijfsomschrijving: {tenant['company_description']} Vaste bedrijfsinfo / FAQ: {tenant['faq_context']} Contactgegevens: - E-mail: {tenant['support_email']} - Telefoon: {tenant['support_phone']} - Website: {tenant['website_url']} """.strip() def build_openai_input(tenant_id: str, session_id: str, user_message: str): messages = [] if ENABLE_HISTORY: with closing(get_db()) as conn: rows = conn.execute( """ SELECT role, content FROM ( SELECT role, content, created_at, rowid FROM messages WHERE tenant_id = ? AND session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ? ) ORDER BY created_at ASC, rowid ASC """, (tenant_id, session_id, MAX_HISTORY_MESSAGES), ).fetchall() for row in rows: role = row["role"] if row["role"] in ("user", "assistant", "system", "developer") else "user" content = (row["content"] or "").strip() if content: messages.append( { "role": role, "content": [{"type": "input_text", "text": content}], } ) messages.append({"role": "user", "content": [{"type": "input_text", "text": user_message.strip()}]}) return messages def save_message(tenant_id: str, session_id: str, role: str, content: str): if not ENABLE_HISTORY: return clean_role = (role or "").strip().lower() if clean_role not in ("user", "assistant", "system", "developer"): clean_role = "user" clean_content = clamp_text(content or "", 20000) if not clean_content: return with closing(get_db()) as conn: conn.execute( """ INSERT INTO messages (id, tenant_id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?) """, (str(uuid.uuid4()), tenant_id, session_id, clean_role, clean_content, now_ts()), ) conn.commit() def ask_ai(tenant: dict, session_id: str, user_message: str) -> str: if not client: raise RuntimeError("OPENAI_API_KEY ontbreekt.") response = client.responses.create( model=MODEL_NAME, instructions=build_system_prompt(tenant), input=build_openai_input(tenant["id"], session_id, user_message), max_output_tokens=MAX_OUTPUT_TOKENS, ) return extract_response_text(response) def create_handoff_hint(tenant: dict) -> str: return f"Voor persoonlijke hulp kun je contact opnemen via {tenant['support_email']} of {tenant['support_phone']}." def create_public_signup_checkout(email: str, plan_name: str): if not stripe or not STRIPE_SECRET_KEY: raise RuntimeError("Stripe is niet geconfigureerd.") if not STRIPE_SUCCESS_URL or not STRIPE_CANCEL_URL: raise RuntimeError("STRIPE_SUCCESS_URL of STRIPE_CANCEL_URL ontbreekt.") if not validate_email(email): raise RuntimeError("Geldig e-mailadres ontbreekt.") plan = (plan_name or "").strip().lower() price_mapping = { "starter": STRIPE_PRICE_STARTER_MONTHLY, "pro": STRIPE_PRICE_PRO_MONTHLY, "agency": STRIPE_PRICE_AGENCY_MONTHLY, } price_id = price_mapping.get(plan, "") if not price_id: raise RuntimeError("Geen Stripe price id gevonden voor dit plan.") return stripe.checkout.Session.create( mode="subscription", line_items=[{"price": price_id, "quantity": 1}], success_url=STRIPE_SUCCESS_URL + "?session_id={CHECKOUT_SESSION_ID}", cancel_url=STRIPE_CANCEL_URL, customer_email=email, metadata={ "source": "public_signup", "signup_email": email, "plan_name": plan, "billing_cycle": "monthly", }, ) def create_selfserve_tenant_from_checkout(email: str, plan_name: str, stripe_customer_id: str, stripe_subscription_id: str): tenant_id = str(uuid.uuid4()) slug = unique_slug(email.split("@")[0] if email else "nieuw-bedrijf") with closing(get_db()) as conn: conn.execute( """ INSERT INTO tenants ( id, name, slug, api_key, company_tone, company_description, support_email, support_phone, website_url, faq_context, plan_name, subscription_status, monthly_message_limit, stripe_customer_id, stripe_subscription_id, billing_email, billing_cycle, widget_color, is_active, created_at ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) """, ( tenant_id, "Nieuw bedrijf", slug, generate_api_key(), DEFAULT_COMPANY_TONE, DEFAULT_COMPANY_DESCRIPTION, email or DEFAULT_SUPPORT_EMAIL, DEFAULT_SUPPORT_PHONE, DEFAULT_WEBSITE_URL, DEFAULT_FAQ_CONTEXT, plan_name, "active", get_plan_limit(plan_name), stripe_customer_id, stripe_subscription_id, email or DEFAULT_SUPPORT_EMAIL, "monthly", DEFAULT_WIDGET_COLOR, 1, now_ts(), ), ) conn.commit() token = create_onboarding_token(tenant_id, email or "") return get_tenant_by_id(tenant_id), token def create_stripe_portal_for_tenant(tenant: dict): if not stripe or not STRIPE_SECRET_KEY: raise RuntimeError("Stripe is niet geconfigureerd.") customer_id = (tenant.get("stripe_customer_id") or "").strip() if not customer_id: raise RuntimeError("Tenant heeft nog geen Stripe customer id.") return_url = STRIPE_SUCCESS_URL or request.host_url.rstrip("/") + "/dashboard" return stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url) def get_tenant_stats(tenant_id: str): month_key = time.strftime("%Y-%m", time.localtime()) with closing(get_db()) as conn: lead_count = conn.execute("SELECT COUNT(*) AS total FROM leads WHERE tenant_id = ?", (tenant_id,)).fetchone()["total"] sessions_count = conn.execute("SELECT COUNT(DISTINCT session_id) AS total FROM messages WHERE tenant_id = ?", (tenant_id,)).fetchone()["total"] total_messages = conn.execute("SELECT COUNT(*) AS total FROM messages WHERE tenant_id = ?", (tenant_id,)).fetchone()["total"] monthly_messages = conn.execute( "SELECT COUNT(*) AS total FROM usage_events WHERE tenant_id = ? AND event_type = 'message' AND month_key = ?", (tenant_id, month_key), ).fetchone()["total"] monthly_leads = conn.execute( "SELECT COUNT(*) AS total FROM usage_events WHERE tenant_id = ? AND event_type = 'lead' AND month_key = ?", (tenant_id, month_key), ).fetchone()["total"] tenant = get_tenant_by_id(tenant_id) limit_value = int(tenant["monthly_message_limit"]) if tenant else 0 return { "month_key": month_key, "lead_count_total": int(lead_count), "session_count_total": int(sessions_count), "message_count_total": int(total_messages), "message_count_current_month": int(monthly_messages), "lead_count_current_month": int(monthly_leads), "monthly_message_limit": limit_value, "monthly_message_remaining": max(limit_value - int(monthly_messages), 0) if limit_value > 0 else None, } def get_overview_stats(): tenants = get_all_tenants() month_key = time.strftime("%Y-%m", time.localtime()) with closing(get_db()) as conn: total_leads = conn.execute("SELECT COUNT(*) AS total FROM leads").fetchone()["total"] total_messages = conn.execute("SELECT COUNT(*) AS total FROM messages").fetchone()["total"] total_sessions = conn.execute("SELECT COUNT(DISTINCT session_id) AS total FROM messages").fetchone()["total"] monthly_messages = conn.execute( "SELECT COUNT(*) AS total FROM usage_events WHERE event_type = 'message' AND month_key = ?", (month_key,), ).fetchone()["total"] monthly_leads = conn.execute( "SELECT COUNT(*) AS total FROM usage_events WHERE event_type = 'lead' AND month_key = ?", (month_key,), ).fetchone()["total"] return { "month_key": month_key, "tenant_count": len(tenants), "lead_count_total": int(total_leads), "message_count_total": int(total_messages), "session_count_total": int(total_sessions), "message_count_current_month": int(monthly_messages), "lead_count_current_month": int(monthly_leads), } def get_all_tenants(): with closing(get_db()) as conn: rows = conn.execute( """ SELECT id, name, slug, api_key, company_tone, company_description, support_email, support_phone, website_url, faq_context, plan_name, subscription_status, monthly_message_limit, stripe_customer_id, stripe_subscription_id, billing_email, billing_cycle, widget_color, is_active, created_at FROM tenants ORDER BY created_at DESC """ ).fetchall() return [dict(row) for row in rows] def get_tenant_leads(tenant_id: str, query: str = ""): q = f"%{query.strip().lower()}%" with closing(get_db()) as conn: if query.strip(): rows = conn.execute( """ SELECT id, tenant_id, name, email, phone, message, source, created_at FROM leads WHERE tenant_id = ? AND ( LOWER(name) LIKE ? OR LOWER(email) LIKE ? OR LOWER(COALESCE(phone, '')) LIKE ? OR LOWER(message) LIKE ? ) ORDER BY created_at DESC LIMIT 500 """, (tenant_id, q, q, q, q), ).fetchall() else: rows = conn.execute( """ SELECT id, tenant_id, name, email, phone, message, source, created_at FROM leads WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 500 """, (tenant_id,), ).fetchall() return [dict(row) for row in rows] def get_tenant_chat_sessions(tenant_id: str, limit: int = 100): safe_limit = max(1, min(int(limit), 500)) with closing(get_db()) as conn: rows = conn.execute( """ SELECT session_id, COUNT(*) AS total_messages, MAX(created_at) AS last_message_at, MIN(created_at) AS first_message_at FROM messages WHERE tenant_id = ? GROUP BY session_id ORDER BY last_message_at DESC LIMIT ? """, (tenant_id, safe_limit), ).fetchall() return [dict(r) for r in rows] def get_session_messages(tenant_id: str, session_id: str, limit: int = 200): safe_limit = max(1, min(int(limit), 500)) with closing(get_db()) as conn: rows = conn.execute( """ SELECT id, session_id, role, content, created_at FROM messages WHERE tenant_id = ? AND session_id = ? ORDER BY created_at ASC, rowid ASC LIMIT ? """, (tenant_id, session_id, safe_limit), ).fetchall() return [dict(r) for r in rows] def update_tenant_settings(tenant_id: str, data: dict): tenant = get_tenant_by_id(tenant_id) if not tenant: return None, "Tenant niet gevonden." name = clamp_text(data.get("name") or tenant["name"], 200) support_email = clamp_text(data.get("support_email") or tenant["support_email"], 200) website_url = clamp_text(data.get("website_url") or tenant["website_url"], 500) widget_color = normalize_hex_color(data.get("widget_color") or tenant.get("widget_color") or DEFAULT_WIDGET_COLOR) company_description = clamp_text(data.get("company_description") or tenant["company_description"], 5000) faq_context = clamp_text(data.get("faq_context") or tenant["faq_context"], 15000) if not name: return None, "Naam ontbreekt." if not validate_email(support_email): return None, "Support e-mail is ongeldig." with closing(get_db()) as conn: conn.execute( """ UPDATE tenants SET name = ?, support_email = ?, billing_email = ?, website_url = ?, widget_color = ?, company_description = ?, faq_context = ? WHERE id = ? """, (name, support_email, support_email, website_url, widget_color, company_description, faq_context, tenant_id), ) conn.commit() return get_tenant_by_id(tenant_id), None def rotate_tenant_api_key(tenant_id: str): tenant = get_tenant_by_id(tenant_id) if not tenant: return None new_key = generate_api_key() with closing(get_db()) as conn: conn.execute("UPDATE tenants SET api_key = ? WHERE id = ?", (new_key, tenant_id)) conn.commit() return get_tenant_by_id(tenant_id) def require_admin() -> bool: return bool(session.get("admin_logged_in")) def require_customer() -> bool: return bool(session.get("customer_logged_in")) and bool(session.get("customer_tenant_id")) def admin_forbidden(): return jsonify({"ok": False, "error": "Niet geautoriseerd."}), 401 def customer_forbidden(): return jsonify({"ok": False, "error": "Niet geautoriseerd."}), 401 # ========================= # HTML # ========================= def render_shell(title: str, body: str) -> str: return f'''<!DOCTYPE html> <html lang="nl"> <head> <meta charset="UTF-8" /> <meta name="viewport" content="width=device-width, initial-scale=1.0" /> <title>{title}</title> <style> :root {{ --bg:#07101f; --bg2:#0b1224; --panel:rgba(16,24,40,.78); --panel-2:rgba(19,28,47,.92); --line:rgba(255,255,255,.08); --text:#f8fafc; --muted:#a5b4cf; --primary:#6d5efc; --primary-2:#8b7cff; --success:#1ec98f; --danger:#ff6b81; --shadow:0 24px 80px rgba(0,0,0,.35); --radius:24px; }} * {{ box-sizing:border-box; }} html, body {{ margin:0; padding:0; }} body {{ font-family:Inter, Arial, sans-serif; color:var(--text); background: radial-gradient(circle at top left, rgba(109,94,252,.28), transparent 28%), radial-gradient(circle at top right, rgba(30,201,143,.14), transparent 24%), linear-gradient(180deg, #08101f 0%, #091223 100%); min-height:100vh; }} a {{ color:#9bb2ff; text-decoration:none; }} .wrap {{ max-width:1280px; margin:0 auto; padding:28px 18px 60px; }} .topnav {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:22px; padding:16px 18px; border:1px solid var(--line); background:rgba(255,255,255,.03); border-radius:20px; backdrop-filter: blur(12px); }} .brand {{ display:flex; align-items:center; gap:12px; font-weight:800; letter-spacing:.2px; }} .brand-badge {{ width:42px; height:42px; border-radius:14px; background:linear-gradient(135deg, var(--primary), var(--primary-2)); display:flex; align-items:center; justify-content:center; font-size:18px; box-shadow:0 12px 28px rgba(109,94,252,.35); }} .nav-actions {{ display:flex; gap:10px; flex-wrap:wrap; }} .hero {{ padding:28px; border-radius:30px; background: linear-gradient(135deg, rgba(109,94,252,.18), rgba(255,255,255,.03)); border:1px solid var(--line); box-shadow:var(--shadow); margin-bottom:18px; }} .grid {{ display:grid; gap:18px; }} .grid-2 {{ grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); }} .grid-3 {{ grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); }} .grid-4 {{ grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); }} .card {{ background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); padding:20px; box-shadow:var(--shadow); backdrop-filter: blur(14px); }} .card-soft {{ background:linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02)); }} h1, h2, h3 {{ margin:0 0 10px; line-height:1.08; }} h1 {{ font-size:clamp(34px, 5vw, 60px); }} h2 {{ font-size:26px; }} h3 {{ font-size:18px; }} p {{ margin:0 0 12px; }} .muted {{ color:var(--muted); }} .stats {{ font-size:30px; font-weight:800; letter-spacing:-0.02em; }} .kicker {{ display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:999px; background:rgba(109,94,252,.14); color:#d9d4ff; border:1px solid rgba(139,124,255,.22); font-size:13px; margin-bottom:14px; }} input, textarea, select, button {{ width:100%; padding:14px 16px; border-radius:16px; border:1px solid rgba(255,255,255,.12); background:rgba(7,16,31,.75); color:#fff; font-size:15px; outline:none; margin-bottom:10px; }} input:focus, textarea:focus, select:focus {{ border-color:rgba(139,124,255,.72); box-shadow:0 0 0 4px rgba(109,94,252,.12); }} textarea {{ min-height:120px; resize:vertical; }} button {{ border:none; cursor:pointer; font-weight:800; background:linear-gradient(135deg, var(--primary), var(--primary-2)); box-shadow:0 14px 32px rgba(109,94,252,.28); transition:transform .12s ease, opacity .12s ease; }} button:hover {{ transform:translateY(-1px); }} button.secondary {{ background:rgba(255,255,255,.08); box-shadow:none; }} button.ghost {{ background:transparent; border:1px solid var(--line); box-shadow:none; }} button.success {{ background:linear-gradient(135deg, #11b87d, #22d39a); }} button.danger {{ background:linear-gradient(135deg, #f5536d, #ff7a8e); }} .btn-row {{ display:flex; gap:10px; flex-wrap:wrap; }} .btn-row > * {{ width:auto; }} .hero-actions {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:18px; }} .hero-actions button {{ width:auto; padding:14px 20px; }} .pill {{ display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:999px; background:rgba(255,255,255,.06); border:1px solid var(--line); font-size:13px; }} pre {{ margin:0; padding:14px; border-radius:16px; overflow:auto; background:rgba(2,6,23,.75); border:1px solid var(--line); white-space:pre-wrap; word-break:break-word; }} table {{ width:100%; border-collapse:collapse; font-size:14px; }} th, td {{ padding:12px 10px; border-bottom:1px solid rgba(255,255,255,.06); text-align:left; vertical-align:top; }} thead th {{ color:#c9d5f0; font-weight:700; }} .small {{ font-size:13px; }} .hr {{ height:1px; background:linear-gradient(90deg, transparent, rgba(255,255,255,.14), transparent); margin:16px 0; }} .price-card {{ position:relative; overflow:hidden; }} .price-card::after {{ content:""; position:absolute; inset:auto -40px -40px auto; width:120px; height:120px; background:radial-gradient(circle, rgba(109,94,252,.28), transparent 60%); }} .price-tag {{ font-size:38px; font-weight:900; margin:8px 0 4px; }} .price-sub {{ color:#cbd5e1; font-size:14px; margin-bottom:14px; }} .section-title {{ display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:10px; }} .copyline {{ display:flex; gap:10px; flex-wrap:wrap; }} .copyline > *:first-child {{ flex:1 1 320px; }} .list-card {{ padding:14px; border-radius:18px; border:1px solid var(--line); background:rgba(255,255,255,.03); margin-bottom:12px; }} .footer-note {{ text-align:center; color:var(--muted); font-size:13px; margin-top:20px; }} .message-user {{ background:rgba(109,94,252,.14); border:1px solid rgba(139,124,255,.25); }} .message-assistant {{ background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.08); }} @media (max-width: 740px) {{ .wrap {{ padding:18px 14px 40px; }} .topnav {{ padding:14px; }} .hero {{ padding:20px; }} .card {{ padding:16px; border-radius:20px; }} }} </style> </head> <body> <div class="wrap"> <div class="topnav"> <div class="brand"> <div class="brand-badge">✦</div> <div> <div style="font-size:18px;">Assistify AI</div> <div class="muted small">AI klantenservice SaaS</div> </div> </div> <div class="nav-actions"> <a href="/"><button class="ghost" style="width:auto;">Home</button></a> <a href="/signup"><button class="ghost" style="width:auto;">Signup</button></a> <a href="/dashboard/login"><button class="ghost" style="width:auto;">Dashboard</button></a> <a href="/admin"><button class="ghost" style="width:auto;">Admin</button></a> </div> </div> {body} <div class="footer-note">Assistify AI • {APP_VERSION}</div> </div> </body> </html>''' def render_homepage_html(): return render_shell( "Assistify AI", f''' <div class="hero"> <div class="kicker">⚡ Automatische support • Leads • Billing • Dashboard</div> <h1>AI klantenservice die omzet helpt maken.</h1> <p class="muted" style="max-width:760px;font-size:18px;"> Geef je website een slimme assistent die klantvragen beantwoordt, leads opvangt en je team tijd bespaart. Alles in één modern dashboard met signup, billing, teambeheer en chatinzichten. </p> <div class="hero-actions"> <button onclick="window.location.href='/signup'">Start met Assistify</button> <button class="secondary" onclick="window.location.href='/dashboard/login'">Klantdashboard</button> <button class="ghost" onclick="window.location.href='/admin'">Admin</button> </div> </div> <div class="grid grid-3"> <div class="card card-soft"> <h3>🤖 Slimme widget</h3> <p class="muted">Laat bezoekers direct antwoord krijgen via een snelle AI chat-widget op je website.</p> </div> <div class="card card-soft"> <h3>📥 Leads verzamelen</h3> <p class="muted">Sales- en supportaanvragen komen automatisch binnen in je dashboard.</p> </div> <div class="card card-soft"> <h3>💬 Chat inzichten</h3> <p class="muted">Bekijk sessies en transcripts zodat je ziet wat klanten echt vragen.</p> </div> </div> <div class="grid grid-3" style="margin-top:18px;"> <div class="card price-card"> <div class="pill">Starter</div> <div class="price-tag">€{PRICE_STARTER_EUR}</div> <div class="price-sub">per maand</div> <p class="muted">500 berichten per maand voor kleine bedrijven en starters.</p> </div> <div class="card price-card"> <div class="pill">Pro</div> <div class="price-tag">€{PRICE_PRO_EUR}</div> <div class="price-sub">per maand</div> <p class="muted">5.000 berichten per maand voor teams die serieus willen opschalen.</p> </div> <div class="card price-card"> <div class="pill">Agency</div> <div class="price-tag">€{PRICE_AGENCY_EUR}</div> <div class="price-sub">per maand</div> <p class="muted">50.000 berichten per maand voor agencies en meerdere klanten.</p> </div> </div> ''', ) def render_signup_html(): return render_shell( "Assistify Signup", f''' <div class="hero"> <div class="kicker">🚀 Nieuwe klant onboarding</div> <h1>Start met Assistify AI</h1> <p class="muted" style="font-size:18px;">Kies je plan, vul je e-mail in en rond je betaling af via Stripe.</p> </div> <div class="card"> <div class="section-title"> <h2>Kies je pakket</h2> <div class="pill">Agency vervangt Enterprise</div> </div> <input id="email" placeholder="jij@bedrijf.nl" /> <div class="grid grid-3" style="margin-top:8px;"> <div class="card price-card"> <h3>Starter</h3> <div class="price-tag">€{PRICE_STARTER_EUR}</div> <div class="price-sub">per maand • 500 berichten</div> <p class="muted">Voor starters en kleine bedrijven.</p> <button onclick="startSignup('starter')">Start Starter</button> </div> <div class="card price-card"> <h3>Pro</h3> <div class="price-tag">€{PRICE_PRO_EUR}</div> <div class="price-sub">per maand • 5.000 berichten</div> <p class="muted">Voor groeiende support- en sales teams.</p> <button onclick="startSignup('pro')">Start Pro</button> </div> <div class="card price-card"> <h3>Agency</h3> <div class="price-tag">€{PRICE_AGENCY_EUR}</div> <div class="price-sub">per maand • 50.000 berichten</div> <p class="muted">Voor agencies en meerdere klanten.</p> <button onclick="startSignup('agency')">Start Agency</button> </div> </div> <p id="status" class="muted" style="margin-top:10px;"></p> </div> <script> async function startSignup(plan) {{ status.textContent = "Bezig met checkout aanmaken..."; try {{ const res = await fetch("/signup/create-checkout", {{ method: "POST", headers: {{"Content-Type":"application/json"}}, body: JSON.stringify({{email: email.value.trim(), plan_name: plan}}) }}); const data = await res.json(); if (!res.ok) throw new Error(data.error || "Er ging iets mis."); window.location.href = data.url; }} catch (e) {{ status.textContent = e.message; }} }} </script> ''', ) def render_signup_success_html(): return render_shell( "Betaling gelukt", ''' <div class="card"> <div class="kicker">✅ Betaling ontvangen</div> <h2>We maken je setup-link klaar</h2> <p class="muted">Een ogenblik, we halen je onboarding-link op.</p> <p id="status">Bezig...</p> </div> <script> (async function() { const params = new URLSearchParams(window.location.search); const sessionId = params.get("session_id"); if (!sessionId) { status.textContent = "Geen session_id gevonden."; return; } try { const res = await fetch("/signup/finalize?session_id=" + encodeURIComponent(sessionId)); const data = await res.json(); if (!res.ok) throw new Error(data.error || "Er ging iets mis."); window.location.href = data.redirect_url; } catch (e) { status.textContent = e.message; } })(); </script> ''', ) def render_signup_cancel_html(): return render_shell( "Betaling geannuleerd", ''' <div class="card"> <div class="kicker">↩ Betaling geannuleerd</div> <h2>Je checkout is gestopt</h2> <p class="muted">Je kunt het meteen opnieuw proberen.</p> <div class="btn-row"> <button onclick="window.location.href='/signup'">Terug naar signup</button> </div> </div> ''', ) def render_onboarding_html(token: str, tenant: dict): host = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/") color = tenant.get("widget_color") or DEFAULT_WIDGET_COLOR embed_code = f'''<script src="{host}/widget.js" data-api-base="{host}" data-tenant-key="{tenant["api_key"]}" data-title="{tenant["name"]}" data-color="{color}" ></script>''' return render_shell( "Assistify Onboarding", f''' <div class="hero"> <div class="kicker">🎉 Nieuwe omgeving klaar</div> <h1>Welkom bij Assistify</h1> <p class="muted">Stel hieronder je eerste gegevens in en maak je dashboard-login aan.</p> </div> <div class="grid grid-2"> <div class="card"> <h2>Eerste setup</h2> <input id="full_name" placeholder="Jouw naam" /> <input id="password" type="password" placeholder="Nieuw wachtwoord (minimaal 8 tekens)" /> <input id="name" value="{tenant['name']}" placeholder="Bedrijfsnaam" /> <input id="slug" value="{tenant['slug']}" placeholder="slug" /> <input id="support_email" value="{tenant['support_email']}" placeholder="support@email.nl" /> <input id="website_url" value="{tenant['website_url']}" placeholder="https://jouwdomein.nl" /> <input id="widget_color" value="{tenant.get('widget_color', DEFAULT_WIDGET_COLOR)}" placeholder="#6d5efc" /> <textarea id="company_description">{tenant['company_description']}</textarea> <button onclick="saveSetup()">Opslaan</button> <p id="status" class="muted"></p> </div> <div class="card"> <h2>Integratie</h2> <div class="muted">Tenant slug</div> <pre>{tenant['slug']}</pre> <div class="hr"></div> <div class="muted">API key</div> <pre id="api_key">{tenant['api_key']}</pre> <div class="hr"></div> <div class="muted">Embed code</div> <pre id="embed">{embed_code}</pre> </div> </div> <script> async function saveSetup() {{ status.textContent = "Bezig..."; try {{ const res = await fetch("/signup/complete/{token}", {{ method: "POST", headers: {{"Content-Type":"application/json"}}, body: JSON.stringify({{ full_name: full_name.value.trim(), password: password.value.trim(), name: name.value.trim(), slug: slug.value.trim(), support_email: support_email.value.trim(), website_url: website_url.value.trim(), widget_color: widget_color.value.trim(), company_description: company_description.value.trim() }}) }}); const data = await res.json(); if (!res.ok) throw new Error(data.error || "Er ging iets mis."); api_key.textContent = data.tenant.api_key || ""; embed.textContent = data.embed_code || ""; status.innerHTML = 'Opgeslagen. Login via <a href="/dashboard/login">/dashboard/login</a> met je e-mail + tenant slug.'; }} catch(e) {{ status.textContent = e.message; }} }} </script> ''', ) def render_dashboard_login_html(): return render_shell( "Dashboard Login", ''' <div class="hero"> <div class="kicker">🔐 Klantomgeving</div> <h1>Klantdashboard login</h1> <p class="muted">Log in met je e-mail, wachtwoord en tenant slug.</p> </div> <div class="card" style="max-width:720px;margin:0 auto;"> <input id="tenant_slug" placeholder="tenant-slug" /> <input id="email" placeholder="jij@bedrijf.nl" /> <input id="password" type="password" placeholder="Wachtwoord" /> <div class="btn-row"> <button onclick="login()">Inloggen</button> <button class="secondary" onclick="window.location.href='/reset-password/request'">Wachtwoord vergeten</button> </div> <p id="status" class="muted"></p> </div> <script> async function login() { status.textContent = "Bezig..."; try { const res = await fetch("/dashboard/login", { method: "POST", headers: {"Content-Type":"application/json"}, credentials: "include", body: JSON.stringify({ tenant_slug: tenant_slug.value.trim(), email: email.value.trim(), password: password.value.trim() }) }); const data = await res.json(); if (!res.ok) throw new Error(data.error || "Er ging iets mis."); window.location.href = "/dashboard"; } catch(e) { status.textContent = e.message; } } </script> ''', ) def render_dashboard_html(): return render_shell( "Assistify Dashboard", ''' <div class="hero"> <div class="section-title"> <div> <div class="kicker">📈 Klantdashboard</div> <h1>Beheer je Assistify omgeving</h1> <p class="muted">Instellingen, API key, leads, teamleden, billing en chats in één overzicht.</p> </div> <div class="btn-row"> <button class="secondary" onclick="openPortal()">Billing portal</button> <button class="ghost" onclick="window.location.href='/reset-password/request'">Reset wachtwoord</button> <button class="danger" onclick="logout()">Uitloggen</button> </div> </div> </div> <div class="grid grid-4"> <div class="card"><div class="muted">Plan</div><div class="stats" id="statPlan">-</div></div> <div class="card"><div class="muted">Status</div><div class="stats" id="statStatus">-</div></div> <div class="card"><div class="muted">Berichten deze maand</div><div class="stats" id="statMessages">-</div></div> <div class="card"><div class="muted">Resterend</div><div class="stats" id="statRemaining">-</div></div> </div> <div class="grid grid-2" style="margin-top:18px;"> <div class="card"> <div class="section-title"> <h2>Bedrijfsinstellingen</h2> <div class="pill">Live widget styling</div> </div> <input id="name" placeholder="Bedrijfsnaam" /> <input id="support_email" placeholder="support@email.nl" /> <input id="website_url" placeholder="https://jouwdomein.nl" /> <input id="widget_color" placeholder="#6d5efc" /> <textarea id="company_description" placeholder="Bedrijfsomschrijving"></textarea> <textarea id="faq_context" placeholder="FAQ context"></textarea> <button onclick="saveSettings()">Opslaan</button> <p id="saveStatus" class="muted"></p> </div> <div class="card"> <div class="section-title"> <h2>Integratie</h2> <div class="pill" id="tenantSlugPill">slug</div> </div> <div class="muted">API key</div> <div class="copyline" style="margin:8px 0 12px;"> <pre id="apiKeyBox"></pre> <button class="secondary" onclick="copyApiKey()" style="width:auto;">Kopieer</button> <button class="secondary" onclick="rotateApiKey()" style="width:auto;">Vernieuw key</button> </div> <div class="muted">Embed code</div> <pre id="embedBox"></pre> <p id="apiStatus" class="muted" style="margin-top:10px;"></p> </div> </div> <div class="grid grid-2" style="margin-top:18px;"> <div class="card"> <div class="section-title"> <h2>Teamleden</h2> <div class="pill">Owner kan invites maken</div> </div> <table> <thead><tr><th>Naam</th><th>E-mail</th><th>Owner</th><th>Actief</th></tr></thead> <tbody id="teamTable"></tbody> </table> <div class="hr"></div> <h3>Nieuwe invite</h3> <input id="invite_full_name" placeholder="Naam" /> <input id="invite_email" placeholder="email@bedrijf.nl" /> <button onclick="sendInvite()">Invite versturen</button> <pre id="inviteResult"></pre> </div> <div class="card"> <div class="section-title"> <h2>Leads</h2> <div class="btn-row"> <button class="secondary" onclick="loadLeads()">Verversen</button> <button class="secondary" onclick="exportLeads()">CSV export</button> </div> </div> <input id="leadSearch" placeholder="Zoek lead..." onkeydown="if(event.key==='Enter') loadLeads();" /> <div id="leadsBox"></div> </div> </div> <div class="grid grid-2" style="margin-top:18px;"> <div class="card"> <div class="section-title"> <h2>Chat sessies</h2> <div class="pill">Live transcript viewer</div> </div> <button class="secondary" onclick="loadSessions()">Sessies verversen</button> <div id="sessionsBox" style="margin-top:12px;"></div> </div> <div class="card"> <div class="section-title"> <h2>Transcript</h2> <div class="pill" id="activeSessionPill">geen sessie</div> </div> <div id="transcriptBox"><p class="muted">Selecteer een sessie om het gesprek te bekijken.</p></div> </div> </div> <div class="card" style="margin-top:18px;"> <div class="section-title"> <h2>Audit logs</h2> <div class="pill">Activiteit in je omgeving</div> </div> <table> <thead><tr><th>Tijd</th><th>Actie</th><th>Target</th><th>Meta</th></tr></thead> <tbody id="auditTable"></tbody> </table> </div> <script> let tenant = null; function escapeHtml(value) { return String(value ?? "") .replaceAll("&", "&amp;") .replaceAll("<", "&lt;") .replaceAll(">", "&gt;") .replaceAll('"', "&quot;") .replaceAll("'", "&#039;"); } function safeJsonPretty(value) { try { return JSON.stringify(JSON.parse(value || "{}"), null, 2); } catch { return "{}"; } } async function api(path, options={}) { const headers = options.headers || {}; if (!headers["Content-Type"] && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json"; const res = await fetch(path, {...options, headers, credentials:"include"}); const data = await res.json().catch(() => ({})); if (!res.ok) throw new Error(data.error || "Er ging iets mis."); return data; } function formatDate(ts){ return ts ? new Date(ts*1000).toLocaleString() : "-"; } async function loadDashboard() { const me = await api("/dashboard/me"); const statsData = await api("/dashboard/stats"); const teamData = await api("/dashboard/team"); const auditData = await api("/dashboard/audit-logs"); tenant = me.tenant; name.value = tenant.name || ""; support_email.value = tenant.support_email || ""; website_url.value = tenant.website_url || ""; widget_color.value = tenant.widget_color || ""; company_description.value = tenant.company_description || ""; faq_context.value = tenant.faq_context || ""; statPlan.textContent = tenant.plan_name || "-"; statStatus.textContent = tenant.subscription_status || "-"; statMessages.textContent = statsData.stats.message_count_current_month ?? "-"; statRemaining.textContent = statsData.stats.monthly_message_remaining ?? "-"; tenantSlugPill.textContent = tenant.slug || "slug"; apiKeyBox.textContent = me.tenant.api_key || ""; embedBox.textContent = me.embed_code || ""; teamTable.innerHTML = (teamData.users || []).map(u => <tr><td>${escapeHtml(u.full_name || "-")}</td><td>${escapeHtml(u.email)}</td><td>${u.is_owner ? "Ja" : "Nee"}</td><td>${u.is_active ? "Ja" : "Nee"}</td></tr> ).join(""); auditTable.innerHTML = (auditData.logs || []).map(x => <tr> <td>${escapeHtml(formatDate(x.created_at))}</td> <td>${escapeHtml(x.action)}</td> <td>${escapeHtml((x.target_type || "") + ":" + (x.target_id || "-"))}</td> <td><pre>${escapeHtml(safeJsonPretty(x.meta_json))}</pre></td> </tr> ).join(""); await loadLeads(); await loadSessions(); } async function loadLeads() { try { const q = encodeURIComponent((leadSearch.value || "").trim()); const data = await api("/dashboard/leads?q=" + q); leadsBox.innerHTML = (data.leads || []).length ? data.leads.map(l => <div class="list-card"> <strong>${escapeHtml(l.name)}</strong> <span class="muted">(${escapeHtml(l.email)})</span><br> <span class="muted">${escapeHtml(l.phone || "-")}</span><br> <span class="muted">${escapeHtml(formatDate(l.created_at))} • ${escapeHtml(l.source)}</span> <div style="margin-top:8px;">${escapeHtml(l.message)}</div> </div> ).join("") : "<p class='muted'>Nog geen leads gevonden.</p>"; } catch (e) { leadsBox.innerHTML = "<p class='muted'>" + escapeHtml(e.message) + "</p>"; } } async function loadSessions() { try { const data = await api("/dashboard/chat/sessions"); sessionsBox.innerHTML = (data.sessions || []).length ? data.sessions.map(s => <div class="list-card"> <strong>${escapeHtml(s.session_id)}</strong><br> <span class="muted">${escapeHtml(String(s.total_messages))} berichten • Laatste: ${escapeHtml(formatDate(s.last_message_at))}</span> <div style="margin-top:10px;"> <button class="secondary" style="width:auto;" onclick="loadTranscript('${encodeURIComponent(s.session_id)}')">Open transcript</button> </div> </div> ).join("") : "<p class='muted'>Nog geen chatsessies gevonden.</p>"; } catch (e) { sessionsBox.innerHTML = "<p class='muted'>" + escapeHtml(e.message) + "</p>"; } } async function loadTranscript(encodedSessionId) { const sessionId = decodeURIComponent(encodedSessionId); activeSessionPill.textContent = sessionId; transcriptBox.innerHTML = "<p class='muted'>Transcript laden...</p>"; try { const data = await api("/dashboard/chat/session/" + encodeURIComponent(sessionId)); transcriptBox.innerHTML = (data.messages || []).length ? data.messages.map(m => <div class="list-card ${m.role === 'user' ? 'message-user' : 'message-assistant'}"> <strong>${escapeHtml(m.role)}</strong> <div class="muted" style="margin-top:4px;">${escapeHtml(formatDate(m.created_at))}</div> <div style="margin-top:8px; white-space:pre-wrap;">${escapeHtml(m.content)}</div> </div> ).join("") : "<p class='muted'>Geen berichten gevonden.</p>"; } catch (e) { transcriptBox.innerHTML = "<p class='muted'>" + escapeHtml(e.message) + "</p>"; } } async function saveSettings() { saveStatus.textContent = "Bezig..."; try { const data = await api("/dashboard/settings", { method:"POST", body: JSON.stringify({ name: name.value.trim(), support_email: support_email.value.trim(), website_url: website_url.value.trim(), widget_color: widget_color.value.trim(), company_description: company_description.value.trim(), faq_context: faq_context.value.trim() }) }); apiKeyBox.textContent = data.tenant.api_key || ""; embedBox.textContent = data.embed_code || ""; saveStatus.textContent = "Opgeslagen."; await loadDashboard(); } catch(e) { saveStatus.textContent = e.message; } } async function sendInvite() { try { const data = await api("/dashboard/team/invite", { method:"POST", body: JSON.stringify({ email: invite_email.value.trim(), full_name: invite_full_name.value.trim() }) }); inviteResult.textContent = JSON.stringify(data, null, 2); await loadDashboard(); } catch(e) { inviteResult.textContent = e.message; } } async function openPortal() { try { const data = await api("/dashboard/billing-portal", {method:"POST", body: JSON.stringify({})}); if (data.url) window.open(data.url, "_blank"); } catch(e) { alert(e.message); } } async function rotateApiKey() { apiStatus.textContent = "API key vernieuwen..."; try { const data = await api("/dashboard/rotate-api-key", {method:"POST", body: JSON.stringify({})}); apiKeyBox.textContent = data.tenant.api_key || ""; embedBox.textContent = data.embed_code || ""; apiStatus.textContent = "Nieuwe API key opgeslagen."; } catch(e) { apiStatus.textContent = e.message; } } function copyApiKey() { navigator.clipboard.writeText(apiKeyBox.textContent || ""); apiStatus.textContent = "API key gekopieerd."; } function exportLeads() { const q = encodeURIComponent((leadSearch.value || "").trim()); window.location.href = "/dashboard/leads/export.csv?q=" + q; } async function logout() { await api("/dashboard/logout", {method:"POST", body: JSON.stringify({})}); window.location.href = "/dashboard/login"; } (async()=>{ try { await loadDashboard(); } catch(e) { window.location.href = "/dashboard/login"; } })(); </script> ''', ) def render_admin_html(): return render_shell( "Assistify Admin", ''' <div id="loginView" class="card" style="max-width:720px;margin:0 auto;"> <div class="kicker">🛡 Admin omgeving</div> <h2>Admin login</h2> <input id="u" placeholder="admin" /> <input id="p" type="password" placeholder="wachtwoord" /> <button onclick="login()">Inloggen</button> <p id="status" class="muted"></p> </div> <div id="appView" style="display:none;"> <div class="hero"> <div class="section-title"> <div> <div class="kicker">📊 Overzicht</div> <h1>Assistify Admin Dashboard</h1> <p class="muted">Bekijk tenants, gebruik en audit logs in een nettere adminomgeving.</p> </div> </div> </div> <div id="overview" class="grid grid-4"></div> <div class="grid grid-2" style="margin-top:18px;"> <div class="card"> <h2>Tenants</h2> <table><thead><tr><th>Naam</th><th>Slug</th><th>Plan</th><th>Status</th></tr></thead><tbody id="tenants"></tbody></table> </div> <div class="card"> <h2>Audit logs</h2> <table><thead><tr><th>Tijd</th><th>Actor</th><th>Actie</th><th>Target</th></tr></thead><tbody id="auditLogs"></tbody></table> </div> </div> </div> <script> async function api(path, options={}) { const headers = options.headers || {}; if (!headers["Content-Type"] && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json"; const res = await fetch(path, {...options, headers, credentials:"include"}); const data = await res.json().catch(() => ({})); if (!res.ok) throw new Error(data.error || "Er ging iets mis."); return data; } function formatDate(ts){ return ts ? new Date(ts*1000).toLocaleString() : "-"; } async function login() { try { await api("/admin/login", {method:"POST", body:JSON.stringify({username:u.value.trim(), password:p.value.trim()})}); load(); } catch(e) { status.textContent = e.message; } } async function load() { loginView.style.display = "none"; appView.style.display = "block"; const s = await api("/admin/stats/overview"); overview.innerHTML = <div class="card"><div class="muted">Tenants</div><div class="stats">${s.stats.tenant_count}</div></div> <div class="card"><div class="muted">Leads</div><div class="stats">${s.stats.lead_count_total}</div></div> <div class="card"><div class="muted">Berichten</div><div class="stats">${s.stats.message_count_total}</div></div> <div class="card"><div class="muted">Sessies</div><div class="stats">${s.stats.session_count_total}</div></div> ; const t = await api("/admin/tenants"); tenants.innerHTML = (t.tenants || []).map(x => <tr><td>${x.name}</td><td>${x.slug}</td><td>${x.plan_name}</td><td>${x.subscription_status}</td></tr> ).join(""); const logs = await api("/admin/audit-logs"); auditLogs.innerHTML = (logs.logs || []).map(x => <tr> <td>${formatDate(x.created_at)}</td> <td>${x.actor_type}:${x.actor_id || "-"}</td> <td>${x.action}</td> <td>${x.target_type}:${x.target_id || "-"}</td> </tr> ).join(""); } (async()=>{ try { await api("/admin/me"); load(); } catch(e) {} })(); </script> ''', ) def render_invite_accept_html(token: str, invite_row: dict): return render_shell( "Team Invite", f''' <div class="card" style="max-width:760px;margin:0 auto;"> <div class="kicker">👥 Team uitnodiging</div> <h2>Je bent uitgenodigd</h2> <p class="muted">Maak hieronder je account aan.</p> <input id="full_name" value="{invite_row.get('full_name','')}" placeholder="Naam" /> <input id="email" value="{invite_row.get('email','')}" placeholder="E-mail" /> <input id="password" type="password" placeholder="Wachtwoord (minimaal 8 tekens)" /> <button onclick="acceptInvite()">Account maken</button> <p id="status" class="muted"></p> </div> <script> async function acceptInvite() {{ status.textContent = "Bezig..."; try {{ const res = await fetch("/invite/{token}", {{ method: "POST", headers: {{"Content-Type":"application/json"}}, body: JSON.stringify({{ full_name: full_name.value.trim(), email: email.value.trim(), password: password.value.trim() }}) }}); const data = await res.json(); if (!res.ok) throw new Error(data.error || "Er ging iets mis."); status.innerHTML = 'Account aangemaakt. Login via <a href="/dashboard/login">/dashboard/login</a> met tenant slug.'; }} catch(e) {{ status.textContent = e.message; }} }} </script> ''', ) def render_reset_request_html(): return render_shell( "Wachtwoord reset aanvragen", ''' <div class="card" style="max-width:760px;margin:0 auto;"> <div class="kicker">🔑 Wachtwoord reset</div> <h2>Vraag een reset aan</h2> <input id="email" placeholder="jij@bedrijf.nl" /> <button onclick="requestReset()">Reset aanvragen</button> <pre id="result"></pre> </div> <script> async function requestReset() { try { const res = await fetch("/reset-password/request", { method:"POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({email: email.value.trim()}) }); const data = await res.json(); if (!res.ok) throw new Error(data.error || "Er ging iets mis."); result.textContent = JSON.stringify(data, null, 2); } catch(e) { result.textContent = e.message; } } </script> ''', ) def render_reset_password_html(token: str): return render_shell( "Nieuw wachtwoord", f''' <div class="card" style="max-width:760px;margin:0 auto;"> <div class="kicker">🔒 Nieuw wachtwoord</div> <h2>Stel een nieuw wachtwoord in</h2> <input id="password" type="password" placeholder="Nieuw wachtwoord" /> <button onclick="saveNewPassword()">Opslaan</button> <p id="status" class="muted"></p> </div> <script> async function saveNewPassword() {{ status.textContent = "Bezig..."; try {{ const res = await fetch("/reset-password/{token}", {{ method:"POST", headers: {{"Content-Type":"application/json"}}, body: JSON.stringify({{password: password.value.trim()}}) }}); const data = await res.json(); if (!res.ok) throw new Error(data.error || "Er ging iets mis."); status.innerHTML = 'Wachtwoord opgeslagen. Login via <a href="/dashboard/login">/dashboard/login</a>'; }} catch(e) {{ status.textContent = e.message; }} }} </script> ''', ) # ========================= # HOOKS # ========================= @app.before_request def before_any_request(): ensure_startup() @app.after_request def add_security_headers(response): response.headers["X-Content-Type-Options"] = "nosniff" response.headers["X-Frame-Options"] = "SAMEORIGIN" response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin" response.headers["Cache-Control"] = "no-store" return response # ========================= # PUBLIC ROUTES # ========================= @app.route("/", methods=["GET"]) def home(): return Response(render_homepage_html(), mimetype="text/html") @app.route("/health", methods=["GET"]) def health(): return jsonify( { "ok": True, "version": APP_VERSION, "openai_configured": bool(OPENAI_API_KEY), "model": MODEL_NAME, "database": DB_PATH, "stripe_configured": bool(stripe and STRIPE_SECRET_KEY), "stripe_webhook_configured": bool(STRIPE_WEBHOOK_SECRET), "smtp_enabled": SMTP_ENABLED, "startup_done": _startup_done, } ) @app.route("/version", methods=["GET"]) def version(): return jsonify({"ok": True, "version": APP_VERSION, "model": MODEL_NAME}) @app.route("/admin", methods=["GET"]) def admin_dashboard(): return Response(render_admin_html(), mimetype="text/html") @app.route("/signup", methods=["GET"]) def signup_page(): return Response(render_signup_html(), mimetype="text/html") @app.route("/signup/success", methods=["GET"]) def signup_success(): return Response(render_signup_success_html(), mimetype="text/html") @app.route("/signup/cancel", methods=["GET"]) def signup_cancel(): return Response(render_signup_cancel_html(), mimetype="text/html") @app.route("/dashboard/login", methods=["GET"]) def dashboard_login_page(): return Response(render_dashboard_login_html(), mimetype="text/html") @app.route("/dashboard", methods=["GET"]) def dashboard_page(): return Response(render_dashboard_html(), mimetype="text/html") @app.route("/invite/<token>", methods=["GET"]) def invite_accept_page(token): row = get_invite_token_row(token) if not row: return Response("Invite niet gevonden.", status=404) if int(row.get("is_used", 0)) == 1: return Response("Invite is al gebruikt.", status=400) if token_is_expired(row.get("created_at", 0)): return Response("Invite is verlopen.", status=400) return Response(render_invite_accept_html(token, row), mimetype="text/html") @app.route("/reset-password/request", methods=["GET"]) def reset_request_page(): return Response(render_reset_request_html(), mimetype="text/html") @app.route("/reset-password/<token>", methods=["GET"]) def reset_password_page(token): row = get_password_reset_token_row(token) if not row: return Response("Reset token niet gevonden.", status=404) if int(row.get("is_used", 0)) == 1: return Response("Reset token is al gebruikt.", status=400) if token_is_expired(row.get("created_at", 0)): return Response("Reset token is verlopen.", status=400) return Response(render_reset_password_html(token), mimetype="text/html") # ========================= # STRIPE WEBHOOK # ========================= @app.route("/stripe/webhook", methods=["POST"]) def stripe_webhook(): if not stripe or not STRIPE_SECRET_KEY: return jsonify({"ok": False, "error": "Stripe is niet geconfigureerd."}), 503 payload = request.get_data() sig_header = request.headers.get("Stripe-Signature", "") try: if STRIPE_WEBHOOK_SECRET: event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET) else: event = json.loads(payload.decode("utf-8")) except Exception as e: return jsonify({"ok": False, "error": "Webhook validatie mislukt.", "details": str(e)}), 400 event_type = event.get("type", "") data_object = ((event.get("data") or {}).get("object") or {}) try: if event_type == "checkout.session.completed": metadata = data_object.get("metadata") or {} if metadata.get("source") == "public_signup": customer_id = str(data_object.get("customer") or "").strip() subscription_id = str(data_object.get("subscription") or "").strip() plan_name = str(metadata.get("plan_name") or "starter").strip().lower() tenant = get_tenant_by_stripe_customer_id(customer_id) if customer_id else None if tenant: sync_tenant_subscription( tenant["id"], plan_name=plan_name, subscription_status="active", stripe_subscription_id=subscription_id, ) create_audit_log( tenant["id"], "stripe", customer_id, "stripe_checkout_completed", "tenant", tenant["id"], {"plan_name": plan_name, "subscription_id": subscription_id}, get_client_ip(), ) elif event_type in ("customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"): subscription_id = str(data_object.get("id") or "").strip() customer_id = str(data_object.get("customer") or "").strip() status = str(data_object.get("status") or "").strip().lower() tenant = get_tenant_by_stripe_subscription_id(subscription_id) if not tenant and customer_id: tenant = get_tenant_by_stripe_customer_id(customer_id) if tenant: plan_name = tenant["plan_name"] items = (((data_object.get("items") or {}).get("data")) or []) price_ids = [str(((item.get("price") or {}).get("id")) or "").strip() for item in items] if STRIPE_PRICE_AGENCY_MONTHLY and STRIPE_PRICE_AGENCY_MONTHLY in price_ids: plan_name = "agency" elif STRIPE_PRICE_PRO_MONTHLY and STRIPE_PRICE_PRO_MONTHLY in price_ids: plan_name = "pro" elif STRIPE_PRICE_STARTER_MONTHLY and STRIPE_PRICE_STARTER_MONTHLY in price_ids: plan_name = "starter" sync_tenant_subscription( tenant["id"], plan_name=plan_name, subscription_status=status, stripe_subscription_id=subscription_id, ) create_audit_log( tenant["id"], "stripe", customer_id, "stripe_subscription_synced", "tenant", tenant["id"], {"status": status, "subscription_id": subscription_id, "plan_name": plan_name}, get_client_ip(), ) return jsonify({"ok": True}) except Exception as e: return jsonify({"ok": False, "error": "Webhook verwerking mislukt.", "details": str(e)}), 500 # ========================= # SIGNUP FLOW # ========================= @app.route("/signup/create-checkout", methods=["POST"]) def signup_create_checkout(): data = json_body() email = clamp_text(data.get("email") or "", 200) plan_name = clamp_text(data.get("plan_name") or "starter", 50).lower() if plan_name not in ("starter", "pro", "agency"): return jsonify({"ok": False, "error": "Ongeldig plan."}), 400 try: session_obj = create_public_signup_checkout(email, plan_name) return jsonify({"ok": True, "url": session_obj.url, "session_id": session_obj.id}) except Exception as e: return jsonify({"ok": False, "error": str(e)}), 400 @app.route("/signup/finalize", methods=["GET"]) def signup_finalize(): if not stripe or not STRIPE_SECRET_KEY: return jsonify({"ok": False, "error": "Stripe is niet geconfigureerd."}), 503 session_id = (request.args.get("session_id") or "").strip() if not session_id: return jsonify({"ok": False, "error": "session_id ontbreekt."}), 400 try: checkout_session = stripe.checkout.Session.retrieve(session_id) if not checkout_session: return jsonify({"ok": False, "error": "Checkout sessie niet gevonden."}), 404 metadata = checkout_session.get("metadata") or {} if metadata.get("source") != "public_signup": return jsonify({"ok": False, "error": "Dit is geen public signup checkout."}), 400 customer_id = str(checkout_session.get("customer", "") or "").strip() subscription_id = str(checkout_session.get("subscription", "") or "").strip() email = str( checkout_session.get("customer_email") or ((checkout_session.get("customer_details") or {}).get("email")) or metadata.get("signup_email") or "" ).strip() plan_name = str(metadata.get("plan_name") or "starter").strip().lower() existing = get_tenant_by_stripe_customer_id(customer_id) if customer_id else None if existing: sync_tenant_subscription(existing["id"], plan_name=plan_name, subscription_status="active", stripe_subscription_id=subscription_id) token = create_onboarding_token(existing["id"], email) base_url = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/") return jsonify({"ok": True, "redirect_url": f"{base_url}/signup/complete/{token}"}) tenant, token = create_selfserve_tenant_from_checkout(email, plan_name, customer_id, subscription_id) base_url = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/") return jsonify({"ok": True, "tenant_id": tenant["id"], "redirect_url": f"{base_url}/signup/complete/{token}"}) except Exception as e: return jsonify({"ok": False, "error": "Signup finaliseren mislukt.", "details": str(e)}), 500 @app.route("/signup/complete/<token>", methods=["GET"]) def signup_complete_view(token): row = get_onboarding_token_row(token) if not row: return Response("Onboarding token niet gevonden.", status=404) if int(row.get("is_used", 0)) == 1: return Response("Onboarding token is al gebruikt.", status=400) if token_is_expired(row.get("created_at", 0)): return Response("Onboarding token is verlopen.", status=400) tenant = get_tenant_by_id(row["tenant_id"]) if not tenant: return Response("Tenant niet gevonden.", status=404) return Response(render_onboarding_html(token, tenant), mimetype="text/html") @app.route("/signup/complete/<token>", methods=["POST"]) def signup_complete_save(token): row = get_onboarding_token_row(token) if not row: return jsonify({"ok": False, "error": "Onboarding token niet gevonden."}), 404 if int(row.get("is_used", 0)) == 1: return jsonify({"ok": False, "error": "Onboarding token is al gebruikt."}), 400 if token_is_expired(row.get("created_at", 0)): return jsonify({"ok": False, "error": "Onboarding token is verlopen."}), 400 tenant = get_tenant_by_id(row["tenant_id"]) if not tenant: return jsonify({"ok": False, "error": "Tenant niet gevonden."}), 404 data = json_body() full_name = clamp_text(data.get("full_name") or "", 200) password = (data.get("password") or "").strip() name = clamp_text(data.get("name") or tenant["name"], 200) slug = normalize_slug(data.get("slug") or tenant["slug"]) support_email = clamp_text(data.get("support_email") or tenant["support_email"], 200) website_url = clamp_text(data.get("website_url") or tenant["website_url"], 500) widget_color = normalize_hex_color(data.get("widget_color") or tenant.get("widget_color") or DEFAULT_WIDGET_COLOR) company_description = clamp_text(data.get("company_description") or tenant["company_description"], 5000) if not name: return jsonify({"ok": False, "error": "Naam ontbreekt."}), 400 if not slug: return jsonify({"ok": False, "error": "Slug ontbreekt."}), 400 if not validate_email(support_email): return jsonify({"ok": False, "error": "Support e-mail is ongeldig."}), 400 if len(password) < 8: return jsonify({"ok": False, "error": "Wachtwoord moet minimaal 8 tekens zijn."}), 400 existing_slug = get_tenant_by_slug(slug) if existing_slug and existing_slug["id"] != tenant["id"]: return jsonify({"ok": False, "error": "Slug bestaat al."}), 400 with closing(get_db()) as conn: conn.execute( """ UPDATE tenants SET name = ?, slug = ?, support_email = ?, billing_email = ?, website_url = ?, widget_color = ?, company_description = ? WHERE id = ? """, (name, slug, support_email, support_email, website_url, widget_color, company_description, tenant["id"]), ) conn.commit() existing_user = get_customer_user_by_email_and_tenant(support_email, tenant["id"]) if not existing_user: user, error = create_customer_user(tenant["id"], support_email, password, full_name=full_name, is_owner=True) if error: return jsonify({"ok": False, "error": error}), 400 create_audit_log( tenant["id"], "customer_user", user["id"], "owner_created_from_onboarding", "customer_user", user["id"], {"email": user["email"]}, get_client_ip(), ) mark_onboarding_token_used(token) tenant = get_tenant_by_id(tenant["id"]) create_audit_log( tenant["id"], "system", "", "onboarding_completed", "tenant", tenant["id"], {"name": tenant["name"], "slug": tenant["slug"], "support_email": tenant["support_email"]}, get_client_ip(), ) host = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/") embed_code = f'''<script src="{host}/widget.js" data-api-base="{host}" data-tenant-key="{tenant["api_key"]}" data-title="{tenant["name"]}" data-color="{tenant.get("widget_color") or DEFAULT_WIDGET_COLOR}" ></script>''' return jsonify( { "ok": True, "tenant": { "id": tenant["id"], "name": tenant["name"], "slug": tenant["slug"], "api_key": tenant["api_key"], "widget_color": tenant.get("widget_color") or DEFAULT_WIDGET_COLOR, }, "embed_code": embed_code, } ) # ========================= # INVITE FLOW # ========================= @app.route("/invite/<token>", methods=["POST"]) def invite_accept_submit(token): row = get_invite_token_row(token) if not row: return jsonify({"ok": False, "error": "Invite niet gevonden."}), 404 if int(row.get("is_used", 0)) == 1: return jsonify({"ok": False, "error": "Invite is al gebruikt."}), 400 if token_is_expired(row.get("created_at", 0)): return jsonify({"ok": False, "error": "Invite is verlopen."}), 400 data = json_body() email = clamp_text(data.get("email") or row["email"], 200).lower() full_name = clamp_text(data.get("full_name") or row.get("full_name", ""), 200) password = (data.get("password") or "").strip() if email != row["email"].lower(): return jsonify({"ok": False, "error": "E-mailadres klopt niet met de invite."}), 400 user, error = create_customer_user(row["tenant_id"], email, password, full_name=full_name, is_owner=False) if error: return jsonify({"ok": False, "error": error}), 400 mark_invite_token_used(token) create_audit_log( row["tenant_id"], "customer_user", user["id"], "invite_accepted", "customer_user", user["id"], {"email": user["email"], "full_name": user["full_name"]}, get_client_ip(), ) return jsonify({"ok": True, "user": {"id": user["id"], "email": user["email"]}}) # ========================= # RESET PASSWORD # ========================= @app.route("/reset-password/request", methods=["POST"]) def reset_password_request_submit(): data = json_body() email = clamp_text(data.get("email") or "", 200) user = get_customer_user_by_email_global(email) if not user: return jsonify({"ok": False, "error": "Gebruiker niet gevonden."}), 404 token = create_password_reset_token(user["tenant_id"], user["id"], user["email"]) base_url = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/") reset_url = f"{base_url}/reset-password/{token}" email_sent = False email_error = None try: tenant = get_tenant_by_id(user["tenant_id"]) tenant_name = tenant["name"] if tenant else "Assistify" email_sent, email_error = send_password_reset_email(user["email"], reset_url, tenant_name) except Exception as e: email_sent = False email_error = str(e) create_audit_log( user["tenant_id"], "customer_user", user["id"], "password_reset_requested", "customer_user", user["id"], {"email": user["email"], "email_sent": email_sent, "email_error": email_error}, get_client_ip(), ) return jsonify( { "ok": True, "reset_url": reset_url, "email_sent": email_sent, "email_error": email_error, } ) @app.route("/reset-password/<token>", methods=["POST"]) def reset_password_submit(token): row = get_password_reset_token_row(token) if not row: return jsonify({"ok": False, "error": "Reset token niet gevonden."}), 404 if int(row.get("is_used", 0)) == 1: return jsonify({"ok": False, "error": "Reset token is al gebruikt."}), 400 if token_is_expired(row.get("created_at", 0)): return jsonify({"ok": False, "error": "Reset token is verlopen."}), 400 data = json_body() password = (data.get("password") or "").strip() ok, error = update_customer_password(row["user_id"], password) if not ok: return jsonify({"ok": False, "error": error}), 400 mark_password_reset_token_used(token) create_audit_log( row["tenant_id"], "customer_user", row["user_id"], "password_reset_completed", "customer_user", row["user_id"], {"email": row["email"]}, get_client_ip(), ) return jsonify({"ok": True}) # ========================= # WIDGET # ========================= @app.route("/widget.js", methods=["GET"]) def widget_js(): js = r''' (function () { var initialScript = document.currentScript; function resolveScriptElement() { if (initialScript) return initialScript; var scripts = document.querySelectorAll('script[src]'); for (var i = scripts.length - 1; i >= 0; i--) { var src = scripts[i].getAttribute("src") || ""; if (src.indexOf("/widget.js") !== -1 || src.indexOf("widget.js") !== -1) { return scripts[i]; } } return null; } function bootWidget() { var currentScript = resolveScriptElement(); var tenantKey = currentScript ? currentScript.getAttribute("data-tenant-key") : ""; var apiBase = currentScript ? currentScript.getAttribute("data-api-base") : ""; var title = currentScript ? currentScript.getAttribute("data-title") : "Chat"; var accent = currentScript ? currentScript.getAttribute("data-color") : "#6d5efc"; if (!tenantKey || !apiBase) return; if (document.getElementById("assistify-launcher")) return; var sessionId = localStorage.getItem("assistify_session_id"); if (!sessionId) { sessionId = "sess-" + Math.random().toString(36).slice(2) + Date.now(); localStorage.setItem("assistify_session_id", sessionId); } var root = document.createElement("div"); root.innerHTML = <div id="assistify-launcher" style="position:fixed;bottom:20px;right:20px;z-index:999999;background:${accent};color:#fff;border:none;border-radius:999px;padding:14px 18px;cursor:pointer;font-family:Arial,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.18);">${title}</div> <div id="assistify-box" style="display:none;position:fixed;bottom:80px;right:20px;z-index:999999;width:360px;max-width:calc(100vw - 40px);height:560px;background:#fff;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.18);overflow:hidden;font-family:Arial,sans-serif;border:1px solid #e5e7eb;"> <div style="background:${accent};color:#fff;padding:14px 16px;font-weight:700;">${title}</div> <div id="assistify-tabs" style="display:flex;border-bottom:1px solid #eee;"> <button data-tab="chat" style="flex:1;padding:10px;border:none;background:#fff;cursor:pointer;color:#111;">Chat</button> <button data-tab="lead" style="flex:1;padding:10px;border:none;background:#fff;cursor:pointer;color:#111;">Lead</button> </div> <div id="assistify-chat-tab"> <div id="assistify-messages" style="height:320px;overflow:auto;padding:14px;background:#f9fafb;"></div> <div style="padding:12px;border-top:1px solid #eee;background:#fff;"> <textarea id="assistify-input" placeholder="Typ je bericht..." style="width:100%;height:70px;resize:none;border:1px solid #ddd;border-radius:10px;padding:10px;box-sizing:border-box;font-family:Arial,sans-serif;color:#111;background:#fff;"></textarea> <button id="assistify-send" style="width:100%;margin-top:8px;background:${accent};color:#fff;border:none;border-radius:10px;padding:12px;cursor:pointer;font-weight:600;">Versturen</button> </div> </div> <div id="assistify-lead-tab" style="display:none;padding:12px;background:#fff;height:430px;overflow:auto;"> <input id="assistify-lead-name" placeholder="Naam" style="width:100%;padding:10px;margin-bottom:8px;border:1px solid #ddd;border-radius:10px;color:#111;background:#fff;"> <input id="assistify-lead-email" placeholder="E-mail" style="width:100%;padding:10px;margin-bottom:8px;border:1px solid #ddd;border-radius:10px;color:#111;background:#fff;"> <input id="assistify-lead-phone" placeholder="Telefoon" style="width:100%;padding:10px;margin-bottom:8px;border:1px solid #ddd;border-radius:10px;color:#111;background:#fff;"> <textarea id="assistify-lead-message" placeholder="Waar kunnen we mee helpen?" style="width:100%;height:120px;padding:10px;margin-bottom:8px;border:1px solid #ddd;border-radius:10px;color:#111;background:#fff;"></textarea> <button id="assistify-lead-send" style="width:100%;background:${accent};color:#fff;border:none;border-radius:10px;padding:12px;cursor:pointer;font-weight:600;">Lead versturen</button> <div id="assistify-lead-status" style="margin-top:8px;font-size:14px;color:#444;"></div> </div> </div> ; document.body.appendChild(root); var launcher = document.getElementById("assistify-launcher"); var box = document.getElementById("assistify-box"); var input = document.getElementById("assistify-input"); var send = document.getElementById("assistify-send"); var messages = document.getElementById("assistify-messages"); var chatTab = document.getElementById("assistify-chat-tab"); var leadTab = document.getElementById("assistify-lead-tab"); var tabButtons = document.querySelectorAll("#assistify-tabs button"); function switchTab(tab) { chatTab.style.display = tab === "chat" ? "block" : "none"; leadTab.style.display = tab === "lead" ? "block" : "none"; } tabButtons.forEach(function(btn) { btn.addEventListener("click", function() { switchTab(btn.getAttribute("data-tab")); }); }); function addMessage(text, who) { var row = document.createElement("div"); row.style.marginBottom = "10px"; row.style.textAlign = who === "user" ? "right" : "left"; var bubble = document.createElement("div"); bubble.style.display = "inline-block"; bubble.style.maxWidth = "85%"; bubble.style.padding = "10px 12px"; bubble.style.borderRadius = "12px"; bubble.style.whiteSpace = "pre-wrap"; bubble.style.lineHeight = "1.4"; bubble.style.fontSize = "14px"; bubble.style.background = who === "user" ? accent : "#ffffff"; bubble.style.color = who === "user" ? "#ffffff" : "#111111"; bubble.style.border = who === "user" ? "none" : "1px solid #e5e7eb"; bubble.textContent = text; row.appendChild(bubble); messages.appendChild(row); messages.scrollTop = messages.scrollHeight; } async function sendMessage() { var text = input.value.trim(); if (!text) return; addMessage(text, "user"); input.value = ""; send.disabled = true; addMessage("Bezig met antwoorden...", "assistant"); try { var res = await fetch(apiBase + "/widget/chat", { method: "POST", headers: {"Content-Type": "application/json", "X-Tenant-Key": tenantKey}, body: JSON.stringify({message: text, session_id: sessionId}) }); var data = await res.json(); if (messages.lastChild) messages.removeChild(messages.lastChild); if (data.ok) addMessage(data.reply || "Geen antwoord ontvangen.", "assistant"); else addMessage(data.error || "Er ging iets mis.", "assistant"); } catch (err) { if (messages.lastChild) messages.removeChild(messages.lastChild); addMessage("Netwerkfout. Probeer het opnieuw.", "assistant"); } finally { send.disabled = false; } } async function sendLead() { var name = document.getElementById("assistify-lead-name").value.trim(); var email = document.getElementById("assistify-lead-email").value.trim(); var phone = document.getElementById("assistify-lead-phone").value.trim(); var message = document.getElementById("assistify-lead-message").value.trim(); var status = document.getElementById("assistify-lead-status"); status.textContent = "Bezig..."; try { var res = await fetch(apiBase + "/widget/lead", { method: "POST", headers: {"Content-Type": "application/json", "X-Tenant-Key": tenantKey}, body: JSON.stringify({name: name, email: email, phone: phone, message: message}) }); var data = await res.json(); status.textContent = data.ok ? "Lead succesvol verstuurd." : (data.error || "Er ging iets mis."); } catch (err) { status.textContent = "Netwerkfout. Probeer het opnieuw."; } } launcher.addEventListener("click", function () { box.style.display = box.style.display === "none" ? "block" : "none"; }); send.addEventListener("click", sendMessage); document.getElementById("assistify-lead-send").addEventListener("click", sendLead); input.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } }); addMessage("Hoi! Waar kan ik je mee helpen?", "assistant"); } if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bootWidget); else bootWidget(); })(); ''' return Response(js, mimetype="application/javascript") @app.route("/widget/chat", methods=["POST"]) def widget_chat(): ip = get_client_ip() tenant_key = request.headers.get("X-Tenant-Key", "").strip() if not tenant_key: return jsonify({"ok": False, "error": "Tenant key ontbreekt."}), 401 tenant = get_tenant_by_api_key(tenant_key) if not tenant: return jsonify({"ok": False, "error": "Ongeldige tenant key."}), 401 allowed, reason = tenant_can_chat(tenant) if not allowed: return jsonify({"ok": False, "error": reason}), 403 if is_rate_limited(ip, tenant_key): return jsonify({"ok": False, "error": "Te veel aanvragen. Probeer later opnieuw."}), 429 try: data = json_body() user_message = clamp_text(data.get("message") or "", MAX_MESSAGE_LENGTH) session_id = get_or_create_session_id(data) if not user_message: return jsonify({"ok": False, "error": "Bericht is leeg."}), 400 answer = ask_ai(tenant, session_id, user_message) if not answer: answer = "Sorry, ik kon nu geen goed antwoord genereren. " + create_handoff_hint(tenant) if ENABLE_LEAD_CAPTURE and detect_lead_intent(user_message): answer += "\n\nAls je wilt, kun je ook direct je gegevens achterlaten in het lead-tabblad." save_message(tenant["id"], session_id, "user", user_message) save_message(tenant["id"], session_id, "assistant", answer) record_usage_event(tenant["id"], "message", {"session_id": session_id}) stats = get_tenant_stats(tenant["id"]) return jsonify( { "ok": True, "reply": answer, "session_id": session_id, "tenant": tenant["slug"], "version": APP_VERSION, "usage": { "current_month_messages": stats["message_count_current_month"], "monthly_message_limit": stats["monthly_message_limit"], "monthly_message_remaining": stats["monthly_message_remaining"], }, } ) except Exception as e: return jsonify({"ok": False, "error": "Er ging iets mis in de AI backend.", "details": str(e), "version": APP_VERSION}), 500 @app.route("/widget/lead", methods=["POST"]) def widget_lead(): tenant_key = request.headers.get("X-Tenant-Key", "").strip() if not tenant_key: return jsonify({"ok": False, "error": "Tenant key ontbreekt."}), 401 tenant = get_tenant_by_api_key(tenant_key) if not tenant: return jsonify({"ok": False, "error": "Ongeldige tenant key."}), 401 try: data = json_body() name = clamp_text(data.get("name") or "", 200) email = clamp_text(data.get("email") or "", 200) phone = clamp_text(data.get("phone") or "", 100) message = clamp_text(data.get("message") or "", 5000) if not name: return jsonify({"ok": False, "error": "Naam ontbreekt."}), 400 if not email or not validate_email(email): return jsonify({"ok": False, "error": "Geldig e-mailadres ontbreekt."}), 400 if not message: return jsonify({"ok": False, "error": "Bericht ontbreekt."}), 400 with closing(get_db()) as conn: lead_id = str(uuid.uuid4()) conn.execute( """ INSERT INTO leads (id, tenant_id, name, email, phone, message, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) """, (lead_id, tenant["id"], name, email, phone, message, "widget", now_ts()), ) conn.commit() record_usage_event(tenant["id"], "lead", {"source": "widget"}) create_audit_log( tenant["id"], "public_widget", "", "lead_created", "lead", lead_id, {"email": email, "name": name}, get_client_ip(), ) return jsonify({"ok": True, "message": "Lead succesvol opgeslagen."}) except Exception as e: return jsonify({"ok": False, "error": "Lead opslaan mislukt.", "details": str(e)}), 500 # ========================= # AUTH # ========================= @app.route("/admin/login", methods=["POST"]) def admin_login(): data = json_body() username = (data.get("username") or "").strip() password = (data.get("password") or "").strip() valid = False if ADMIN_PASSWORD_HASH: try: valid = username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password) except Exception: valid = False else: valid = username == ADMIN_USERNAME and bool(ADMIN_PASSWORD) and hmac.compare_digest(ADMIN_PASSWORD, password) if not valid: return jsonify({"ok": False, "error": "Ongeldige inloggegevens."}), 401 session.clear() session["admin_logged_in"] = True session["admin_username"] = username session.permanent = True create_audit_log(None, "admin", username, "admin_login", "session", "", {"username": username}, get_client_ip()) return jsonify({"ok": True, "message": "Ingelogd."}) @app.route("/admin/me", methods=["GET"]) def admin_me(): if not require_admin(): return admin_forbidden() return jsonify({"ok": True, "admin": {"username": session.get("admin_username", ADMIN_USERNAME)}}) @app.route("/dashboard/login", methods=["POST"]) def dashboard_login(): data = json_body() tenant_slug = (data.get("tenant_slug") or "").strip().lower() email = (data.get("email") or "").strip() password = (data.get("password") or "").strip() if not tenant_slug or not email or not password: return jsonify({"ok": False, "error": "Tenant slug, e-mail en wachtwoord zijn verplicht."}), 400 user = verify_customer_login(email, password, tenant_slug) if not user: return jsonify({"ok": False, "error": "Ongeldige inloggegevens."}), 401 session.clear() session["customer_logged_in"] = True session["customer_user_id"] = user["id"] session["customer_tenant_id"] = user["tenant_id"] session["customer_email"] = user["email"] session.permanent = True create_audit_log( user["tenant_id"], "customer_user", user["id"], "customer_login", "session", "", {"email": user["email"], "tenant_slug": tenant_slug}, get_client_ip(), ) return jsonify({"ok": True}) @app.route("/dashboard/logout", methods=["POST"]) def dashboard_logout(): tenant_id = session.get("customer_tenant_id") user_id = session.get("customer_user_id") if tenant_id and user_id: create_audit_log(tenant_id, "customer_user", user_id, "customer_logout", "session", "", {}, get_client_ip()) for key in ["customer_logged_in", "customer_user_id", "customer_tenant_id", "customer_email"]: session.pop(key, None) return jsonify({"ok": True}) # ========================= # DASHBOARD # ========================= @app.route("/dashboard/me", methods=["GET"]) def dashboard_me(): if not require_customer(): return customer_forbidden() tenant = get_tenant_by_id(session["customer_tenant_id"]) user = get_customer_user_by_id(session["customer_user_id"]) if not tenant or not user: return customer_forbidden() host = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/") embed_code = f'''<script src="{host}/widget.js" data-api-base="{host}" data-tenant-key="{tenant["api_key"]}" data-title="{tenant["name"]}" data-color="{tenant.get("widget_color") or DEFAULT_WIDGET_COLOR}" ></script>''' return jsonify( { "ok": True, "user": { "id": user["id"], "email": user["email"], "full_name": user.get("full_name", ""), }, "tenant": { "id": tenant["id"], "name": tenant["name"], "slug": tenant["slug"], "api_key": tenant["api_key"], "plan_name": tenant["plan_name"], "subscription_status": tenant["subscription_status"], "support_email": tenant["support_email"], "website_url": tenant["website_url"], "widget_color": tenant.get("widget_color") or DEFAULT_WIDGET_COLOR, "company_description": tenant["company_description"], "faq_context": tenant["faq_context"], }, "embed_code": embed_code, } ) @app.route("/dashboard/stats", methods=["GET"]) def dashboard_stats(): if not require_customer(): return customer_forbidden() tenant = get_tenant_by_id(session["customer_tenant_id"]) if not tenant: return customer_forbidden() return jsonify({"ok": True, "stats": get_tenant_stats(tenant["id"])}) @app.route("/dashboard/settings", methods=["POST"]) def dashboard_settings(): if not require_customer(): return customer_forbidden() before = get_tenant_by_id(session["customer_tenant_id"]) tenant, error = update_tenant_settings(session["customer_tenant_id"], json_body()) if error: return jsonify({"ok": False, "error": error}), 400 host = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/") embed_code = f'''<script src="{host}/widget.js" data-api-base="{host}" data-tenant-key="{tenant["api_key"]}" data-title="{tenant["name"]}" data-color="{tenant.get("widget_color") or DEFAULT_WIDGET_COLOR}" ></script>''' create_audit_log( tenant["id"], "customer_user", session["customer_user_id"], "tenant_settings_updated", "tenant", tenant["id"], { "before": { "name": before["name"], "support_email": before["support_email"], "website_url": before["website_url"], "widget_color": before.get("widget_color"), }, "after": { "name": tenant["name"], "support_email": tenant["support_email"], "website_url": tenant["website_url"], "widget_color": tenant.get("widget_color"), }, }, get_client_ip(), ) return jsonify( { "ok": True, "tenant": { "id": tenant["id"], "name": tenant["name"], "slug": tenant["slug"], "api_key": tenant["api_key"], "widget_color": tenant.get("widget_color") or DEFAULT_WIDGET_COLOR, }, "embed_code": embed_code, } ) @app.route("/dashboard/rotate-api-key", methods=["POST"]) def dashboard_rotate_api_key(): if not require_customer(): return customer_forbidden() user = get_customer_user_by_id(session["customer_user_id"]) if not user or int(user.get("is_owner", 0)) != 1: return jsonify({"ok": False, "error": "Alleen owner kan de API key vernieuwen."}), 403 tenant = rotate_tenant_api_key(session["customer_tenant_id"]) if not tenant: return jsonify({"ok": False, "error": "Tenant niet gevonden."}), 404 host = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/") embed_code = f'''<script src="{host}/widget.js" data-api-base="{host}" data-tenant-key="{tenant["api_key"]}" data-title="{tenant["name"]}" data-color="{tenant.get("widget_color") or DEFAULT_WIDGET_COLOR}" ></script>''' create_audit_log( tenant["id"], "customer_user", session["customer_user_id"], "api_key_rotated", "tenant", tenant["id"], {}, get_client_ip(), ) return jsonify( { "ok": True, "tenant": { "id": tenant["id"], "name": tenant["name"], "slug": tenant["slug"], "api_key": tenant["api_key"], "widget_color": tenant.get("widget_color") or DEFAULT_WIDGET_COLOR, }, "embed_code": embed_code, } ) @app.route("/dashboard/team", methods=["GET"]) def dashboard_team(): if not require_customer(): return customer_forbidden() return jsonify({"ok": True, "users": list_customer_users(session["customer_tenant_id"])}) @app.route("/dashboard/team/invite", methods=["POST"]) def dashboard_team_invite(): if not require_customer(): return customer_forbidden() current_user = get_customer_user_by_id(session["customer_user_id"]) if not current_user or int(current_user.get("is_owner", 0)) != 1: return jsonify({"ok": False, "error": "Alleen owner kan invites maken."}), 403 data = json_body() email = clamp_text(data.get("email") or "", 200).lower() full_name = clamp_text(data.get("full_name") or "", 200) if not validate_email(email): return jsonify({"ok": False, "error": "Geldig e-mailadres ontbreekt."}), 400 if get_customer_user_by_email_and_tenant(email, session["customer_tenant_id"]): return jsonify({"ok": False, "error": "Gebruiker bestaat al."}), 400 token = create_invite_token(session["customer_tenant_id"], email, full_name, current_user["id"]) base_url = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/") invite_url = f"{base_url}/invite/{token}" email_sent = False email_error = None try: tenant = get_tenant_by_id(session["customer_tenant_id"]) tenant_name = tenant["name"] if tenant else "Assistify" email_sent, email_error = send_invite_email(email, invite_url, tenant_name) except Exception as e: email_sent = False email_error = str(e) create_audit_log( session["customer_tenant_id"], "customer_user", current_user["id"], "team_invite_created", "invite_token", token, {"email": email, "full_name": full_name, "email_sent": email_sent, "email_error": email_error}, get_client_ip(), ) return jsonify({"ok": True# =========================
# HELPERS
# =========================
def now_ts() -> int:
    return int(time.time())


def clamp_text(value: str, max_len: int) -> str:
    text = (value or "").strip()
    return text[:max_len].rstrip() if len(text) > max_len else text


def normalize_hex_color(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return DEFAULT_WIDGET_COLOR

    if not value.startswith("#"):
        value = "#" + value

    if len(value) == 4:
        value = "#" + value[1] * 2 + value[2] * 2 + value[3] * 2

    if len(value) != 7:
        return DEFAULT_WIDGET_COLOR

    valid = "0123456789abcdefABCDEF"
    for ch in value[1:]:
        if ch not in valid:
            return DEFAULT_WIDGET_COLOR

    return value.lower()


def get_client_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def json_body():
    return request.get_json(silent=True) or {}


def validate_email(email: str) -> bool:
    email = email.strip()
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


def unique_slug(base: str) -> str:
    base_slug = normalize_slug(base) or "bedrijf"
    slug = base_slug
    counter = 1

    while get_tenant_by_slug(slug):
        counter += 1
        suffix = f"-{counter}"
        slug = (base_slug[: max(1, 60 - len(suffix))] + suffix).strip("-")

    return slug


def generate_api_key() -> str:
    return "tenant_" + secrets.token_urlsafe(24)


def get_or_create_session_id(data: dict) -> str:
    session_id = (data.get("session_id") or "").strip()
    return session_id[:120] if session_id else str(uuid.uuid4())


def detect_lead_intent(message: str) -> bool:
    text = (message or "").lower()
    lead_keywords = [
        "prijs",
        "kosten",
        "demo",
        "offerte",
        "samenwerken",
        "contact",
        "bellen",
        "abonnement",
        "interesse",
        "pakket",
        "sales",
    ]
    return any(word in text for word in lead_keywords)


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


def extract_response_text(response) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return clamp_text(text.strip(), MAX_ASSISTANT_REPLY_CHARS)

    try:
        output = getattr(response, "output", []) or []
        parts = []

        for item in output:
            content = getattr(item, "content", []) or []
            for content_item in content:
                maybe_text = getattr(content_item, "text", None)
                if isinstance(maybe_text, str) and maybe_text.strip():
                    parts.append(maybe_text.strip())

        return clamp_text("\n".join(parts).strip(), MAX_ASSISTANT_REPLY_CHARS)
    except Exception:
        return ""


def send_email(to_email: str, subject: str, text_body: str):
    if not SMTP_ENABLED:
        return False, "SMTP staat uit."

    if not SMTP_HOST or not SMTP_USERNAME or not SMTP_PASSWORD or not SMTP_FROM_EMAIL:
        return False, "SMTP config ontbreekt."

    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain", "utf-8"))

    server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
    try:
        if SMTP_USE_TLS:
            server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM_EMAIL, [to_email], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass

    return True, None


def send_invite_email(to_email: str, invite_url: str, tenant_name: str):
    subject = f"Uitnodiging voor {tenant_name}"
    body = f"""Je bent uitgenodigd voor {tenant_name}.

Open deze link om je account aan te maken:
{invite_url}

Als je deze uitnodiging niet verwachtte, kun je dit bericht negeren.
"""
    return send_email(to_email, subject, body)


def send_password_reset_email(to_email: str, reset_url: str, tenant_name: str):
    subject = f"Wachtwoord reset voor {tenant_name}"
    body = f"""Je hebt een wachtwoord reset aangevraagd voor {tenant_name}.

Open deze link om een nieuw wachtwoord in te stellen:
{reset_url}

Als jij dit niet was, kun je dit bericht negeren.
"""
    return send_email(to_email, subject, body)


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
            INSERT INTO audit_logs (
                id, tenant_id, actor_type, actor_id, action, target_type,
                target_id, meta_json, ip_address, created_at
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


def token_is_expired(created_at: int) -> bool:
    try:
        age = now_ts() - int(created_at or 0)
        return age > (TOKEN_TTL_HOURS * 3600)
    except Exception:
        return True


def get_plan_limit(plan_name: str) -> int:
    mapping = {"starter": 500, "pro": 5000, "agency": 50000}
    return mapping.get((plan_name or "").strip().lower(), 500)


def sync_tenant_subscription(
    tenant_id: str,
    plan_name: str = None,
    subscription_status: str = None,
    stripe_subscription_id: str = None,
):
    tenant = get_tenant_by_id(tenant_id)
    if not tenant:
        return None

    new_plan = (plan_name or tenant["plan_name"]).strip().lower()
    new_status = (subscription_status or tenant["subscription_status"]).strip().lower()
    new_sub_id = (
        stripe_subscription_id
        if stripe_subscription_id is not None
        else tenant.get("stripe_subscription_id", "")
    )

    monthly_limit = get_plan_limit(new_plan)
    is_active = 1 if new_status in ("active", "trialing") else 0

    with closing(get_db()) as conn:
        conn.execute(
            """
            UPDATE tenants
            SET plan_name = ?, subscription_status = ?, stripe_subscription_id = ?,
                monthly_message_limit = ?, is_active = ?
            WHERE id = ?
            """,
            (new_plan, new_status, new_sub_id, monthly_limit, is_active, tenant_id),
        )
        conn.commit()

    return get_tenant_by_id(tenant_id)# =========================
# TENANT & USER QUERIES
# =========================

def get_tenant_by_api_key(api_key: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM tenants WHERE api_key = ? AND is_active = 1",
            (api_key,)
        ).fetchone()

    return dict(row) if row else None


def get_tenant_by_slug(slug: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM tenants WHERE slug = ?",
            (slug,)
        ).fetchone()

    return dict(row) if row else None


def get_tenant_by_id(tenant_id: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM tenants WHERE id = ?",
            (tenant_id,)
        ).fetchone()

    return dict(row) if row else None


def get_tenant_by_stripe_customer_id(customer_id: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM tenants WHERE stripe_customer_id = ?",
            (customer_id,)
        ).fetchone()

    return dict(row) if row else None


def get_tenant_by_stripe_subscription_id(subscription_id: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM tenants WHERE stripe_subscription_id = ?",
            (subscription_id,)
        ).fetchone()

    return dict(row) if row else None


def get_customer_user_by_email_and_tenant(email: str, tenant_id: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            """
            SELECT * FROM customer_users
            WHERE tenant_id = ?
            AND LOWER(email) = LOWER(?)
            LIMIT 1
            """,
            (tenant_id, email.strip()),
        ).fetchone()

    return dict(row) if row else None


def get_customer_user_by_email_global(email: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            """
            SELECT * FROM customer_users
            WHERE LOWER(email) = LOWER(?)
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (email.strip(),),
        ).fetchone()

    return dict(row) if row else None


def get_customer_user_by_id(user_id: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM customer_users WHERE id = ?",
            (user_id,),
        ).fetchone()

    return dict(row) if row else None


def list_customer_users(tenant_id: str):
    with closing(get_db()) as conn:
        rows = conn.execute(
            """
            SELECT id, tenant_id, email, full_name,
                   is_owner, is_active, created_at
            FROM customer_users
            WHERE tenant_id = ?
            ORDER BY created_at ASC
            """,
            (tenant_id,),
        ).fetchall()

    return [dict(r) for r in rows]


# =========================
# USER MANAGEMENT
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

    existing = get_customer_user_by_email_and_tenant(email, tenant_id)
    if existing:
        return None, "Gebruiker bestaat al."

    row = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "email": email.strip().lower(),
        "password_hash": generate_password_hash(password.strip()),
        "full_name": clamp_text(full_name or "", 200),
        "is_owner": 1 if is_owner else 0,
        "is_active": 1,
        "created_at": now_ts(),
    }

    with closing(get_db()) as conn:
        conn.execute(
            """
            INSERT INTO customer_users (
                id, tenant_id, email, password_hash,
                full_name, is_owner, is_active, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["tenant_id"],
                row["email"],
                row["password_hash"],
                row["full_name"],
                row["is_owner"],
                row["is_active"],
                row["created_at"],
            ),
        )
        conn.commit()

    return get_customer_user_by_id(row["id"]), None


def verify_customer_login(email: str, password: str, tenant_slug: str):
    tenant = get_tenant_by_slug(tenant_slug)
    if not tenant:
        return None

    with closing(get_db()) as conn:
        row = conn.execute(
            """
            SELECT cu.*, 
                   t.name AS tenant_name,
                   t.slug AS tenant_slug,
                   t.is_active AS tenant_is_active
            FROM customer_users cu
            JOIN tenants t ON t.id = cu.tenant_id
            WHERE LOWER(cu.email) = LOWER(?)
            AND cu.tenant_id = ?
            LIMIT 1
            """,
            (email.strip(), tenant["id"]),
        ).fetchone()

    if not row:
        return None

    user = dict(row)

    if int(user.get("is_active", 0)) != 1:
        return None

    if int(user.get("tenant_is_active", 0)) != 1:
        return None

    try:
        if check_password_hash(user["password_hash"], password):
            return user
    except Exception:
        return None

    return None


def update_customer_password(user_id: str, new_password: str):
    if len((new_password or "").strip()) < 8:
        return False, "Wachtwoord moet minimaal 8 tekens zijn."

    with closing(get_db()) as conn:
        conn.execute(
            "UPDATE customer_users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password.strip()), user_id),
        )
        conn.commit()

    return True, None


# =========================
# TOKEN HELPERS
# =========================

def create_onboarding_token(tenant_id: str, email: str) -> str:
    token = secrets.token_urlsafe(32)

    with closing(get_db()) as conn:
        conn.execute(
            """
            INSERT INTO onboarding_tokens
            (id, tenant_id, token, email, is_used, created_at, used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                tenant_id,
                token,
                email.strip(),
                0,
                now_ts(),
                0,
            ),
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
            """
            UPDATE onboarding_tokens
            SET is_used = 1, used_at = ?
            WHERE token = ?
            """,
            (now_ts(), token),
        )
        conn.commit()


def create_invite_token(
    tenant_id: str,
    email: str,
    full_name: str,
    invited_by_user_id: str,
) -> str:

    token = secrets.token_urlsafe(32)

    with closing(get_db()) as conn:
        conn.execute(
            """
            INSERT INTO invite_tokens
            (id, tenant_id, email, full_name, token,
             invited_by_user_id, is_used, created_at, used_at)
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

    return tokendef get_invite_token_row(token: str):
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT * FROM invite_tokens WHERE token = ?",
            (token,),
        ).fetchone()

    return dict(row) if row else None


def mark_invite_token_used(token: str):
    with closing(get_db()) as conn:
        conn.execute(
            """
            UPDATE invite_tokens
            SET is_used = 1, used_at = ?
            WHERE token = ?
            """,
            (now_ts(), token),
        )
        conn.commit()


def create_password_reset_token(tenant_id: str, user_id: str, email: str) -> str:
    token = secrets.token_urlsafe(32)

    with closing(get_db()) as conn:
        conn.execute(
            """
            INSERT INTO password_reset_tokens
            (id, tenant_id, user_id, email, token, is_used, created_at, used_at)
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
            "SELECT * FROM password_reset_tokens WHERE token = ?",
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


# =========================
# USAGE / LIMITS
# =========================

def record_usage_event(tenant_id: str, event_type: str, meta=None):
    with closing(get_db()) as conn:
        conn.execute(
            """
            INSERT INTO usage_events
            (id, tenant_id, event_type, month_key, meta_json, created_at)
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

    status = (tenant.get("subscription_status") or "").strip().lower()
    if status not in ("active", "trialing"):
        return False, "Abonnement is niet actief."

    monthly_limit = int(tenant.get("monthly_message_limit", 0) or 0)
    used = get_monthly_usage_count(tenant["id"], "message")

    if monthly_limit > 0 and used >= monthly_limit:
        return False, "Maandelijkse limiet bereikt."

    return True, None


# =========================
# AI
# =========================

def build_system_prompt(tenant: dict) -> str:
    return f"""
Je bent de AI klantenservice-assistent van {tenant['name']}.

Doel:
- Beantwoord klantvragen duidelijk en correct.
- Schrijf in het Nederlands, tenzij de gebruiker een andere taal gebruikt.
- Gebruik een {tenant['company_tone']} toon.
- Houd antwoorden praktisch, kort en duidelijk.
- Verzinnen mag niet. Als iets onbekend is, zeg dat eerlijk.
- Verwijs bij complexe, gevoelige of account-specifieke zaken naar menselijke support.
- Als iemand interesse toont in samenwerken, prijs, demo of contact, mag je vriendelijk sturen richting lead of contactaanvraag.

Bedrijfsomschrijving:
{tenant['company_description']}

Vaste bedrijfsinfo / FAQ:
{tenant['faq_context']}

Contactgegevens:
- E-mail: {tenant['support_email']}
- Telefoon: {tenant['support_phone']}
- Website: {tenant['website_url']}
""".strip()


def build_openai_input(tenant_id: str, session_id: str, user_message: str):
    messages = []

    if ENABLE_HISTORY:
        with closing(get_db()) as conn:
            rows = conn.execute(
                """
                SELECT role, content
                FROM (
                    SELECT role, content, created_at, rowid
                    FROM messages
                    WHERE tenant_id = ? AND session_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                )
                ORDER BY created_at ASC, rowid ASC
                """,
                (tenant_id, session_id, MAX_HISTORY_MESSAGES),
            ).fetchall()

        for row in rows:
            role = row["role"] if row["role"] in ("user", "assistant", "system", "developer") else "user"
            content = (row["content"] or "").strip()

            if content:
                messages.append(
                    {
                        "role": role,
                        "content": [{"type": "input_text", "text": content}],
                    }
                )

    messages.append(
        {
            "role": "user",
            "content": [{"type": "input_text", "text": user_message.strip()}],
        }
    )

    return messages


def save_message(tenant_id: str, session_id: str, role: str, content: str):
    if not ENABLE_HISTORY:
        return

    clean_role = (role or "").strip().lower()
    if clean_role not in ("user", "assistant", "system", "developer"):
        clean_role = "user"

    clean_content = clamp_text(content or "", 20000)
    if not clean_content:
        return

    with closing(get_db()) as conn:
        conn.execute(
            """
            INSERT INTO messages
            (id, tenant_id, session_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                tenant_id,
                session_id,
                clean_role,
                clean_content,
                now_ts(),
            ),
        )
        conn.commit()


def ask_ai(tenant: dict, session_id: str, user_message: str) -> str:
    if not client:
        raise RuntimeError("OPENAI_API_KEY ontbreekt.")

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
        error_text = str(e).lower()

        if "timeout" in error_text or "timed out" in error_text:
            return (
                "Sorry, het antwoord duurde te lang. "
                + create_handoff_hint(tenant)
            )

        raise


def create_handoff_hint(tenant: dict) -> str:
    return f"Voor persoonlijke hulp kun je contact opnemen via {tenant['support_email']} of {tenant['support_phone']}."


# =========================
# STRIPE HELPERS
# =========================

def create_public_signup_checkout(email: str, plan_name: str):
    if not stripe or not STRIPE_SECRET_KEY:
        raise RuntimeError("Stripe is niet geconfigureerd.")

    if not STRIPE_SUCCESS_URL or not STRIPE_CANCEL_URL:
        raise RuntimeError("STRIPE_SUCCESS_URL of STRIPE_CANCEL_URL ontbreekt.")

    if not validate_email(email):
        raise RuntimeError("Geldig e-mailadres ontbreekt.")

    plan = (plan_name or "").strip().lower()

    price_mapping = {
        "starter": STRIPE_PRICE_STARTER_MONTHLY,
        "pro": STRIPE_PRICE_PRO_MONTHLY,
        "agency": STRIPE_PRICE_AGENCY_MONTHLY,
    }

    price_id = price_mapping.get(plan, "")
    if not price_id:
        raise RuntimeError("Geen Stripe price id gevonden voor dit plan.")

    return stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=STRIPE_SUCCESS_URL + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=STRIPE_CANCEL_URL,
        customer_email=email,
        metadata={
            "source": "public_signup",
            "signup_email": email,
            "plan_name": plan,
            "billing_cycle": "monthly",
        },
    )


def create_selfserve_tenant_from_checkout(
    email: str,
    plan_name: str,
    stripe_customer_id: str,
    stripe_subscription_id: str,
):
    tenant_id = str(uuid.uuid4())
    slug = unique_slug(email.split("@")[0] if email else "nieuw-bedrijf")

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
                email or DEFAULT_SUPPORT_EMAIL,
                DEFAULT_SUPPORT_PHONE,
                DEFAULT_WEBSITE_URL,
                DEFAULT_FAQ_CONTEXT,
                plan_name,
                "active",
                get_plan_limit(plan_name),
                stripe_customer_id,
                stripe_subscription_id,
                email or DEFAULT_SUPPORT_EMAIL,
                "monthly",
                DEFAULT_WIDGET_COLOR,
                1,
                now_ts(),
            ),
        )
        conn.commit()

    token = create_onboarding_token(tenant_id, email or "")
    return get_tenant_by_id(tenant_id), token


def create_stripe_portal_for_tenant(tenant: dict):
    if not stripe or not STRIPE_SECRET_KEY:
        raise RuntimeError("Stripe is niet geconfigureerd.")

    customer_id = (tenant.get("stripe_customer_id") or "").strip()
    if not customer_id:
        raise RuntimeError("Tenant heeft nog geen Stripe customer id.")

    return_url = STRIPE_SUCCESS_URL or request.host_url.rstrip("/") + "/dashboard"
    return stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)# =========================
# STATS
# =========================

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
            WHERE tenant_id = ?
            AND event_type = 'message'
            AND month_key = ?
            """,
            (tenant_id, month_key),
        ).fetchone()["total"]

        monthly_leads = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM usage_events
            WHERE tenant_id = ?
            AND event_type = 'lead'
            AND month_key = ?
            """,
            (tenant_id, month_key),
        ).fetchone()["total"]

    tenant = get_tenant_by_id(tenant_id)
    limit_value = int(tenant["monthly_message_limit"]) if tenant else 0

    return {
        "month_key": month_key,
        "lead_count_total": int(lead_count),
        "session_count_total": int(sessions_count),
        "message_count_total": int(total_messages),
        "message_count_current_month": int(monthly_messages),
        "lead_count_current_month": int(monthly_leads),
        "monthly_message_limit": limit_value,
        "monthly_message_remaining": max(limit_value - int(monthly_messages), 0)
        if limit_value > 0
        else None,
    }


def get_all_tenants():
    with closing(get_db()) as conn:
        rows = conn.execute(
            """
            SELECT
                id, name, slug, api_key,
                company_tone, company_description,
                support_email, support_phone, website_url,
                faq_context,
                plan_name, subscription_status,
                monthly_message_limit,
                stripe_customer_id, stripe_subscription_id,
                billing_email, billing_cycle,
                widget_color, is_active, created_at
            FROM tenants
            ORDER BY created_at DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_overview_stats():
    tenants = get_all_tenants()
    month_key = time.strftime("%Y-%m", time.localtime())

    with closing(get_db()) as conn:
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
            WHERE event_type = 'message'
            AND month_key = ?
            """,
            (month_key,),
        ).fetchone()["total"]

        monthly_leads = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM usage_events
            WHERE event_type = 'lead'
            AND month_key = ?
            """,
            (month_key,),
        ).fetchone()["total"]

    return {
        "month_key": month_key,
        "tenant_count": len(tenants),
        "lead_count_total": int(total_leads),
        "message_count_total": int(total_messages),
        "session_count_total": int(total_sessions),
        "message_count_current_month": int(monthly_messages),
        "lead_count_current_month": int(monthly_leads),
    }


# =========================
# LEADS
# =========================

def get_tenant_leads(tenant_id: str, query: str = ""):
    q = f"%{query.strip().lower()}%"

    with closing(get_db()) as conn:
        if query.strip():
            rows = conn.execute(
                """
                SELECT
                    id, tenant_id, name, email,
                    phone, message, source, created_at
                FROM leads
                WHERE tenant_id = ?
                AND (
                    LOWER(name) LIKE ?
                    OR LOWER(email) LIKE ?
                    OR LOWER(COALESCE(phone, '')) LIKE ?
                    OR LOWER(message) LIKE ?
                )
                ORDER BY created_at DESC
                LIMIT 500
                """,
                (tenant_id, q, q, q, q),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    id, tenant_id, name, email,
                    phone, message, source, created_at
                FROM leads
                WHERE tenant_id = ?
                ORDER BY created_at DESC
                LIMIT 500
                """,
                (tenant_id,),
            ).fetchall()

    return [dict(row) for row in rows]


# =========================
# CHAT SESSIONS
# =========================

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
            SELECT
                id, session_id, role,
                content, created_at
            FROM messages
            WHERE tenant_id = ?
            AND session_id = ?
            ORDER BY created_at ASC, rowid ASC
            LIMIT ?
            """,
            (tenant_id, session_id, safe_limit),
        ).fetchall()

    return [dict(r) for r in rows]


# =========================
# TENANT SETTINGS
# =========================

def update_tenant_settings(tenant_id: str, data: dict):
    tenant = get_tenant_by_id(tenant_id)
    if not tenant:
        return None, "Tenant niet gevonden."

    name = clamp_text(data.get("name") or tenant["name"], 200)
    support_email = clamp_text(data.get("support_email") or tenant["support_email"], 200)
    website_url = clamp_text(data.get("website_url") or tenant["website_url"], 500)

    widget_color = normalize_hex_color(
        data.get("widget_color")
        or tenant.get("widget_color")
        or DEFAULT_WIDGET_COLOR
    )

    company_description = clamp_text(
        data.get("company_description") or tenant["company_description"],
        5000,
    )

    faq_context = clamp_text(
        data.get("faq_context") or tenant["faq_context"],
        15000,
    )

    if not name:
        return None, "Naam ontbreekt."

    if not validate_email(support_email):
        return None, "Support e-mail is ongeldig."

    with closing(get_db()) as conn:
        conn.execute(
            """
            UPDATE tenants
            SET name = ?, support_email = ?, billing_email = ?,
                website_url = ?, widget_color = ?,
                company_description = ?, faq_context = ?
            WHERE id = ?
            """,
            (
                name,
                support_email,
                support_email,
                website_url,
                widget_color,
                company_description,
                faq_context,
                tenant_id,
            ),
        )
        conn.commit()

    return get_tenant_by_id(tenant_id), None


def rotate_tenant_api_key(tenant_id: str):
    tenant = get_tenant_by_id(tenant_id)
    if not tenant:
        return None

    new_key = generate_api_key()

    with closing(get_db()) as conn:
        conn.execute(
            "UPDATE tenants SET api_key = ? WHERE id = ?",
            (new_key, tenant_id),
        )
        conn.commit()

    return get_tenant_by_id(tenant_id)# =========================
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
# HTML
# =========================

def render_shell(title: str, body: str) -> str:
    return f'''<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title}</title>
<style>
:root {{
  --bg:#07101f;
  --bg2:#0b1224;
  --panel:rgba(16,24,40,.78);
  --panel-2:rgba(19,28,47,.92);
  --line:rgba(255,255,255,.08);
  --text:#f8fafc;
  --muted:#a5b4cf;
  --primary:#6d5efc;
  --primary-2:#8b7cff;
  --success:#1ec98f;
  --danger:#ff6b81;
  --shadow:0 24px 80px rgba(0,0,0,.35);
  --radius:24px;
}}
* {{ box-sizing:border-box; }}
html, body {{ margin:0; padding:0; }}
body {{
  font-family:Inter, Arial, sans-serif;
  color:var(--text);
  background:
    radial-gradient(circle at top left, rgba(109,94,252,.28), transparent 28%),
    radial-gradient(circle at top right, rgba(30,201,143,.14), transparent 24%),
    linear-gradient(180deg, #08101f 0%, #091223 100%);
  min-height:100vh;
}}
a {{ color:#9bb2ff; text-decoration:none; }}
.wrap {{ max-width:1280px; margin:0 auto; padding:28px 18px 60px; }}
.topnav {{
  display:flex; align-items:center; justify-content:space-between; gap:12px;
  margin-bottom:22px; padding:16px 18px; border:1px solid var(--line);
  background:rgba(255,255,255,.03); border-radius:20px; backdrop-filter: blur(12px);
}}
.brand {{ display:flex; align-items:center; gap:12px; font-weight:800; letter-spacing:.2px; }}
.brand-badge {{
  width:42px; height:42px; border-radius:14px;
  background:linear-gradient(135deg, var(--primary), var(--primary-2));
  display:flex; align-items:center; justify-content:center; font-size:18px;
  box-shadow:0 12px 28px rgba(109,94,252,.35);
}}
.nav-actions {{ display:flex; gap:10px; flex-wrap:wrap; }}
.hero {{
  padding:28px; border-radius:30px;
  background: linear-gradient(135deg, rgba(109,94,252,.18), rgba(255,255,255,.03));
  border:1px solid var(--line); box-shadow:var(--shadow); margin-bottom:18px;
}}
.grid {{ display:grid; gap:18px; }}
.grid-2 {{ grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); }}
.grid-3 {{ grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); }}
.grid-4 {{ grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); }}
.card {{
  background:var(--panel); border:1px solid var(--line);
  border-radius:var(--radius); padding:20px; box-shadow:var(--shadow);
  backdrop-filter: blur(14px);
}}
.card-soft {{ background:linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02)); }}
h1, h2, h3 {{ margin:0 0 10px; line-height:1.08; }}
h1 {{ font-size:clamp(34px, 5vw, 60px); }}
h2 {{ font-size:26px; }}
h3 {{ font-size:18px; }}
p {{ margin:0 0 12px; }}
.muted {{ color:var(--muted); }}
.stats {{ font-size:30px; font-weight:800; letter-spacing:-0.02em; }}
.kicker {{
  display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:999px;
  background:rgba(109,94,252,.14); color:#d9d4ff;
  border:1px solid rgba(139,124,255,.22); font-size:13px; margin-bottom:14px;
}}
input, textarea, select, button {{
  width:100%; padding:14px 16px; border-radius:16px;
  border:1px solid rgba(255,255,255,.12); background:rgba(7,16,31,.75);
  color:#fff; font-size:15px; outline:none; margin-bottom:10px;
}}
input:focus, textarea:focus, select:focus {{
  border-color:rgba(139,124,255,.72); box-shadow:0 0 0 4px rgba(109,94,252,.12);
}}
textarea {{ min-height:120px; resize:vertical; }}
button {{
  border:none; cursor:pointer; font-weight:800;
  background:linear-gradient(135deg, var(--primary), var(--primary-2));
  box-shadow:0 14px 32px rgba(109,94,252,.28);
  transition:transform .12s ease, opacity .12s ease;
}}
button:hover {{ transform:translateY(-1px); }}
button.secondary {{ background:rgba(255,255,255,.08); box-shadow:none; }}
button.ghost {{ background:transparent; border:1px solid var(--line); box-shadow:none; }}
button.success {{ background:linear-gradient(135deg, #11b87d, #22d39a); }}
button.danger {{ background:linear-gradient(135deg, #f5536d, #ff7a8e); }}
.btn-row {{ display:flex; gap:10px; flex-wrap:wrap; }}
.btn-row > * {{ width:auto; }}
.hero-actions {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:18px; }}
.hero-actions button {{ width:auto; padding:14px 20px; }}
.pill {{
  display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:999px;
  background:rgba(255,255,255,.06); border:1px solid var(--line); font-size:13px;
}}
pre {{
  margin:0; padding:14px; border-radius:16px; overflow:auto;
  background:rgba(2,6,23,.75); border:1px solid var(--line);
  white-space:pre-wrap; word-break:break-word;
}}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th, td {{
  padding:12px 10px; border-bottom:1px solid rgba(255,255,255,.06);
  text-align:left; vertical-align:top;
}}
thead th {{ color:#c9d5f0; font-weight:700; }}
.small {{ font-size:13px; }}
.hr {{
  height:1px; background:linear-gradient(90deg, transparent, rgba(255,255,255,.14), transparent);
  margin:16px 0;
}}
.price-card {{ position:relative; overflow:hidden; }}
.price-card::after {{
  content:""; position:absolute; inset:auto -40px -40px auto; width:120px; height:120px;
  background:radial-gradient(circle, rgba(109,94,252,.28), transparent 60%);
}}
.price-tag {{ font-size:38px; font-weight:900; margin:8px 0 4px; }}
.price-sub {{ color:#cbd5e1; font-size:14px; margin-bottom:14px; }}
.section-title {{
  display:flex; justify-content:space-between; align-items:center; gap:12px;
  flex-wrap:wrap; margin-bottom:10px;
}}
.copyline {{ display:flex; gap:10px; flex-wrap:wrap; }}
.copyline > *:first-child {{ flex:1 1 320px; }}
.list-card {{
  padding:14px; border-radius:18px; border:1px solid var(--line);
  background:rgba(255,255,255,.03); margin-bottom:12px;
}}
.footer-note {{ text-align:center; color:var(--muted); font-size:13px; margin-top:20px; }}
.message-user {{ background:rgba(109,94,252,.14); border:1px solid rgba(139,124,255,.25); }}
.message-assistant {{ background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.08); }}
@media (max-width: 740px) {{
  .wrap {{ padding:18px 14px 40px; }}
  .topnav {{ padding:14px; }}
  .hero {{ padding:20px; }}
  .card {{ padding:16px; border-radius:20px; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <div class="topnav">
    <div class="brand">
      <div class="brand-badge">✦</div>
      <div>
        <div style="font-size:18px;">Assistify AI</div>
        <div class="muted small">AI klantenservice SaaS</div>
      </div>
    </div>
    <div class="nav-actions">
      <a href="/"><button class="ghost" style="width:auto;">Home</button></a>
      <a href="/signup"><button class="ghost" style="width:auto;">Signup</button></a>
      <a href="/dashboard/login"><button class="ghost" style="width:auto;">Dashboard</button></a>
      <a href="/admin"><button class="ghost" style="width:auto;">Admin</button></a>
    </div>
  </div>
  {body}
  <div class="footer-note">Assistify AI • {APP_VERSION}</div>
</div>
</body>
</html>'''


def render_homepage_html():
    return render_shell(
        "Assistify AI",
        f'''
<div class="hero">
  <div class="kicker">⚡ Automatische support • Leads • Billing • Dashboard</div>
  <h1>AI klantenservice die omzet helpt maken.</h1>
  <p class="muted" style="max-width:760px;font-size:18px;">
    Geef je website een slimme assistent die klantvragen beantwoordt, leads opvangt en je team tijd bespaart.
    Alles in één modern dashboard met signup, billing, teambeheer en chatinzichten.
  </p>
  <div class="hero-actions">
    <button onclick="window.location.href='/signup'">Start met Assistify</button>
    <button class="secondary" onclick="window.location.href='/dashboard/login'">Klantdashboard</button>
    <button class="ghost" onclick="window.location.href='/admin'">Admin</button>
  </div>
</div>

<div class="grid grid-3">
  <div class="card card-soft">
    <h3>🤖 Slimme widget</h3>
    <p class="muted">Laat bezoekers direct antwoord krijgen via een snelle AI chat-widget op je website.</p>
  </div>
  <div class="card card-soft">
    <h3>📥 Leads verzamelen</h3>
    <p class="muted">Sales- en supportaanvragen komen automatisch binnen in je dashboard.</p>
  </div>
  <div class="card card-soft">
    <h3>💬 Chat inzichten</h3>
    <p class="muted">Bekijk sessies en transcripts zodat je ziet wat klanten echt vragen.</p>
  </div>
</div>

<div class="grid grid-3" style="margin-top:18px;">
  <div class="card price-card">
    <div class="pill">Starter</div>
    <div class="price-tag">€{PRICE_STARTER_EUR}</div>
    <div class="price-sub">per maand</div>
    <p class="muted">500 berichten per maand voor kleine bedrijven en starters.</p>
  </div>
  <div class="card price-card">
    <div class="pill">Pro</div>
    <div class="price-tag">€{PRICE_PRO_EUR}</div>
    <div class="price-sub">per maand</div>
    <p class="muted">5.000 berichten per maand voor teams die serieus willen opschalen.</p>
  </div>
  <div class="card price-card">
    <div class="pill">Agency</div>
    <div class="price-tag">€{PRICE_AGENCY_EUR}</div>
    <div class="price-sub">per maand</div>
    <p class="muted">50.000 berichten per maand voor agencies en meerdere klanten.</p>
  </div>
</div>
''',
    )


def render_signup_html():
    return render_shell(
        "Assistify Signup",
        f'''
<div class="hero">
  <div class="kicker">🚀 Nieuwe klant onboarding</div>
  <h1>Start met Assistify AI</h1>
  <p class="muted" style="font-size:18px;">Kies je plan, vul je e-mail in en rond je betaling af via Stripe.</p>
</div>

<div class="card">
  <div class="section-title">
    <h2>Kies je pakket</h2>
    <div class="pill">Agency vervangt Enterprise</div>
  </div>

  <input id="email" placeholder="jij@bedrijf.nl" />

  <div class="grid grid-3" style="margin-top:8px;">
    <div class="card price-card">
      <h3>Starter</h3>
      <div class="price-tag">€{PRICE_STARTER_EUR}</div>
      <div class="price-sub">per maand • 500 berichten</div>
      <p class="muted">Voor starters en kleine bedrijven.</p>
      <button onclick="startSignup('starter')">Start Starter</button>
    </div>

    <div class="card price-card">
      <h3>Pro</h3>
      <div class="price-tag">€{PRICE_PRO_EUR}</div>
      <div class="price-sub">per maand • 5.000 berichten</div>
      <p class="muted">Voor groeiende support- en sales teams.</p>
      <button onclick="startSignup('pro')">Start Pro</button>
    </div>

    <div class="card price-card">
      <h3>Agency</h3>
      <div class="price-tag">€{PRICE_AGENCY_EUR}</div>
      <div class="price-sub">per maand • 50.000 berichten</div>
      <p class="muted">Voor agencies en meerdere klanten.</p>
      <button onclick="startSignup('agency')">Start Agency</button>
    </div>
  </div>

  <p id="status" class="muted" style="margin-top:10px;"></p>
</div>

<script>
async function startSignup(plan) {{
  status.textContent = "Bezig met checkout aanmaken...";
  try {{
    const res = await fetch("/signup/create-checkout", {{
      method: "POST",
      headers: {{"Content-Type":"application/json"}},
      body: JSON.stringify({{email: email.value.trim(), plan_name: plan}})
    }});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Er ging iets mis.");
    window.location.href = data.url;
  }} catch (e) {{
    status.textContent = e.message;
  }}
}}
</script>
''',
    )


def render_signup_success_html():
    return render_shell(
        "Betaling gelukt",
        '''
<div class="card">
  <div class="kicker">✅ Betaling ontvangen</div>
  <h2>We maken je setup-link klaar</h2>
  <p class="muted">Een ogenblik, we halen je onboarding-link op.</p>
  <p id="status">Bezig...</p>
</div>

<script>
(async function() {{
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("session_id");
  if (!sessionId) {{
    status.textContent = "Geen session_id gevonden.";
    return;
  }}

  try {{
    const res = await fetch("/signup/finalize?session_id=" + encodeURIComponent(sessionId));
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Er ging iets mis.");
    window.location.href = data.redirect_url;
  }} catch (e) {{
    status.textContent = e.message;
  }}
}})();
</script>
''',
    )


def render_signup_cancel_html():
    return render_shell(
        "Betaling geannuleerd",
        '''
<div class="card">
  <div class="kicker">↩ Betaling geannuleerd</div>
  <h2>Je checkout is gestopt</h2>
  <p class="muted">Je kunt het meteen opnieuw proberen.</p>
  <div class="btn-row">
    <button onclick="window.location.href='/signup'">Terug naar signup</button>
  </div>
</div>
''',
    )def render_onboarding_html(token: str, tenant: dict):
    host = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/")
    color = tenant.get("widget_color") or DEFAULT_WIDGET_COLOR

    embed_code = f'''<script src="{host}/widget.js" data-api-base="{host}" data-tenant-key="{tenant["api_key"]}" data-title="{tenant["name"]}" data-color="{color}" ></script>'''

    return render_shell(
        "Assistify Onboarding",
        f'''
<div class="hero">
  <div class="kicker">🎉 Nieuwe omgeving klaar</div>
  <h1>Welkom bij Assistify</h1>
  <p class="muted">Stel hieronder je eerste gegevens in en maak je dashboard-login aan.</p>
</div>

<div class="grid grid-2">
  <div class="card">
    <h2>Eerste setup</h2>
    <input id="full_name" placeholder="Jouw naam" />
    <input id="password" type="password" placeholder="Nieuw wachtwoord (minimaal 8 tekens)" />
    <input id="name" value="{tenant['name']}" placeholder="Bedrijfsnaam" />
    <input id="slug" value="{tenant['slug']}" placeholder="slug" />
    <input id="support_email" value="{tenant['support_email']}" placeholder="support@email.nl" />
    <input id="website_url" value="{tenant['website_url']}" placeholder="https://jouwdomein.nl" />
    <input id="widget_color" value="{tenant.get('widget_color', DEFAULT_WIDGET_COLOR)}" placeholder="#6d5efc" />
    <textarea id="company_description">{tenant['company_description']}</textarea>
    <button onclick="saveSetup()">Opslaan</button>
    <p id="status" class="muted"></p>
  </div>

  <div class="card">
    <h2>Integratie</h2>
    <div class="muted">Tenant slug</div>
    <pre>{tenant['slug']}</pre>
    <div class="hr"></div>
    <div class="muted">API key</div>
    <pre id="api_key">{tenant['api_key']}</pre>
    <div class="hr"></div>
    <div class="muted">Embed code</div>
    <pre id="embed">{embed_code}</pre>
  </div>
</div>

<script>
async function saveSetup() {{
  status.textContent = "Bezig...";
  try {{
    const res = await fetch("/signup/complete/{token}", {{
      method: "POST",
      headers: {{"Content-Type":"application/json"}},
      body: JSON.stringify({{
        full_name: full_name.value.trim(),
        password: password.value.trim(),
        name: name.value.trim(),
        slug: slug.value.trim(),
        support_email: support_email.value.trim(),
        website_url: website_url.value.trim(),
        widget_color: widget_color.value.trim(),
        company_description: company_description.value.trim()
      }})
    }});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Er ging iets mis.");
    api_key.textContent = data.tenant.api_key || "";
    embed.textContent = data.embed_code || "";
    status.innerHTML = 'Opgeslagen. Login via <a href="/dashboard/login">/dashboard/login</a> met je e-mail + tenant slug.';
  }} catch(e) {{
    status.textContent = e.message;
  }}
}}
</script>
''',
    )


def render_dashboard_login_html():
    return render_shell(
        "Dashboard Login",
        '''
<div class="hero">
  <div class="kicker">🔐 Klantomgeving</div>
  <h1>Klantdashboard login</h1>
  <p class="muted">Log in met je e-mail, wachtwoord en tenant slug.</p>
</div>

<div class="card" style="max-width:720px;margin:0 auto;">
  <input id="tenant_slug" placeholder="tenant-slug" />
  <input id="email" placeholder="jij@bedrijf.nl" />
  <input id="password" type="password" placeholder="Wachtwoord" />
  <div class="btn-row">
    <button onclick="login()">Inloggen</button>
    <button class="secondary" onclick="window.location.href='/reset-password/request'">Wachtwoord vergeten</button>
  </div>
  <p id="status" class="muted"></p>
</div>

<script>
async function login() {{
  status.textContent = "Bezig...";
  try {{
    const res = await fetch("/dashboard/login", {{
      method: "POST",
      headers: {{"Content-Type":"application/json"}},
      credentials: "include",
      body: JSON.stringify({{
        tenant_slug: tenant_slug.value.trim(),
        email: email.value.trim(),
        password: password.value.trim()
      }})
    }});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Er ging iets mis.");
    window.location.href = "/dashboard";
  }} catch(e) {{
    status.textContent = e.message;
  }}
}}
</script>
''',
    )


def render_dashboard_html():
    return render_shell(
        "Assistify Dashboard",
        '''
<div class="hero">
  <div class="section-title">
    <div>
      <div class="kicker">📈 Klantdashboard</div>
      <h1>Beheer je Assistify omgeving</h1>
      <p class="muted">Instellingen, API key, leads, teamleden, billing en chats in één overzicht.</p>
    </div>
    <div class="btn-row">
      <button class="secondary" onclick="openPortal()">Billing portal</button>
      <button class="ghost" onclick="window.location.href='/reset-password/request'">Reset wachtwoord</button>
      <button class="danger" onclick="logout()">Uitloggen</button>
    </div>
  </div>
</div>

<div class="grid grid-4">
  <div class="card"><div class="muted">Plan</div><div class="stats" id="statPlan">-</div></div>
  <div class="card"><div class="muted">Status</div><div class="stats" id="statStatus">-</div></div>
  <div class="card"><div class="muted">Berichten deze maand</div><div class="stats" id="statMessages">-</div></div>
  <div class="card"><div class="muted">Resterend</div><div class="stats" id="statRemaining">-</div></div>
</div>

<div class="grid grid-2" style="margin-top:18px;">
  <div class="card">
    <div class="section-title">
      <h2>Bedrijfsinstellingen</h2>
      <div class="pill">Live widget styling</div>
    </div>
    <input id="name" placeholder="Bedrijfsnaam" />
    <input id="support_email" placeholder="support@email.nl" />
    <input id="website_url" placeholder="https://jouwdomein.nl" />
    <input id="widget_color" placeholder="#6d5efc" />
    <textarea id="company_description" placeholder="Bedrijfsomschrijving"></textarea>
    <textarea id="faq_context" placeholder="FAQ context"></textarea>
    <button onclick="saveSettings()">Opslaan</button>
    <p id="saveStatus" class="muted"></p>
  </div>

  <div class="card">
    <div class="section-title">
      <h2>Integratie</h2>
      <div class="pill" id="tenantSlugPill">slug</div>
    </div>
    <div class="muted">API key</div>
    <div class="copyline" style="margin:8px 0 12px;">
      <pre id="apiKeyBox"></pre>
      <button class="secondary" onclick="copyApiKey()" style="width:auto;">Kopieer</button>
      <button class="secondary" onclick="rotateApiKey()" style="width:auto;">Vernieuw key</button>
    </div>
    <div class="muted">Embed code</div>
    <pre id="embedBox"></pre>
    <p id="apiStatus" class="muted" style="margin-top:10px;"></p>
  </div>
</div>

<div class="grid grid-2" style="margin-top:18px;">
  <div class="card">
    <div class="section-title">
      <h2>Teamleden</h2>
      <div class="pill">Owner kan invites maken</div>
    </div>
    <table>
      <thead><tr><th>Naam</th><th>E-mail</th><th>Owner</th><th>Actief</th></tr></thead>
      <tbody id="teamTable"></tbody>
    </table>
    <div class="hr"></div>
    <h3>Nieuwe invite</h3>
    <input id="invite_full_name" placeholder="Naam" />
    <input id="invite_email" placeholder="email@bedrijf.nl" />
    <button onclick="sendInvite()">Invite versturen</button>
    <pre id="inviteResult"></pre>
  </div>

  <div class="card">
    <div class="section-title">
      <h2>Leads</h2>
      <div class="btn-row">
        <button class="secondary" onclick="loadLeads()">Verversen</button>
        <button class="secondary" onclick="exportLeads()">CSV export</button>
      </div>
    </div>
    <input id="leadSearch" placeholder="Zoek lead..." onkeydown="if(event.key==='Enter') loadLeads();" />
    <div id="leadsBox"></div>
  </div>
</div>

<div class="grid grid-2" style="margin-top:18px;">
  <div class="card">
    <div class="section-title">
      <h2>Chat sessies</h2>
      <div class="pill">Live transcript viewer</div>
    </div>
    <button class="secondary" onclick="loadSessions()">Sessies verversen</button>
    <div id="sessionsBox" style="margin-top:12px;"></div>
  </div>

  <div class="card">
    <div class="section-title">
      <h2>Transcript</h2>
      <div class="pill" id="activeSessionPill">geen sessie</div>
    </div>
    <div id="transcriptBox"><p class="muted">Selecteer een sessie om het gesprek te bekijken.</p></div>
  </div>
</div>

<div class="card" style="margin-top:18px;">
  <div class="section-title">
    <h2>Audit logs</h2>
    <div class="pill">Activiteit in je omgeving</div>
  </div>
  <table>
    <thead><tr><th>Tijd</th><th>Actie</th><th>Target</th><th>Meta</th></tr></thead>
    <tbody id="auditTable"></tbody>
  </table>
</div>

<script>
let tenant = null;

function escapeHtml(value) {{
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}}

function safeJsonPretty(value) {{
  try {{
    return JSON.stringify(JSON.parse(value || "{{}}"), null, 2);
  }} catch {{
    return "{{}}";
  }}
}}

async function api(path, options={{}}) {{
  const headers = options.headers || {{}};
  if (!headers["Content-Type"] && !(options.body instanceof FormData)) {{
    headers["Content-Type"] = "application/json";
  }}
  const res = await fetch(path, {{...options, headers, credentials:"include"}});
  const data = await res.json().catch(() => ({{}}));
  if (!res.ok) throw new Error(data.error || "Er ging iets mis.");
  return data;
}}

function formatDate(ts) {{
  return ts ? new Date(ts * 1000).toLocaleString() : "-";
}}

async function loadDashboard() {{
  const me = await api("/dashboard/me");
  const statsData = await api("/dashboard/stats");
  const teamData = await api("/dashboard/team");
  const auditData = await api("/dashboard/audit-logs");

  tenant = me.tenant;
  name.value = tenant.name || "";
  support_email.value = tenant.support_email || "";
  website_url.value = tenant.website_url || "";
  widget_color.value = tenant.widget_color || "";
  company_description.value = tenant.company_description || "";
  faq_context.value = tenant.faq_context || "";

  statPlan.textContent = tenant.plan_name || "-";
  statStatus.textContent = tenant.subscription_status || "-";
  statMessages.textContent = statsData.stats.message_count_current_month ?? "-";
  statRemaining.textContent = statsData.stats.monthly_message_remaining ?? "-";

  tenantSlugPill.textContent = tenant.slug || "slug";
  apiKeyBox.textContent = me.tenant.api_key || "";
  embedBox.textContent = me.embed_code || "";

  teamTable.innerHTML = (teamData.users || []).map(u => `
    <tr>
      <td>${{escapeHtml(u.full_name || "-")}}</td>
      <td>${{escapeHtml(u.email)}}</td>
      <td>${{u.is_owner ? "Ja" : "Nee"}}</td>
      <td>${{u.is_active ? "Ja" : "Nee"}}</td>
    </tr>
  `).join("");

  auditTable.innerHTML = (auditData.logs || []).map(x => `
    <tr>
      <td>${{escapeHtml(formatDate(x.created_at))}}</td>
      <td>${{escapeHtml(x.action)}}</td>
      <td>${{escapeHtml((x.target_type || "") + ":" + (x.target_id || "-"))}}</td>
      <td><pre>${{escapeHtml(safeJsonPretty(x.meta_json))}}</pre></td>
    </tr>
  `).join("");

  await loadLeads();
  await loadSessions();
}}

async function loadLeads() {{
  try {{
    const q = encodeURIComponent((leadSearch.value || "").trim());
    const data = await api("/dashboard/leads?q=" + q);
    leadsBox.innerHTML = (data.leads || []).length ? data.leads.map(l => `
      <div class="list-card">
        <strong>${{escapeHtml(l.name)}}</strong>
        <span class="muted">(${{escapeHtml(l.email)}})</span><br>
        <span class="muted">${{escapeHtml(l.phone || "-")}}</span><br>
        <span class="muted">${{escapeHtml(formatDate(l.created_at))}} • ${{escapeHtml(l.source)}}</span>
        <div style="margin-top:8px;">${{escapeHtml(l.message)}}</div>
      </div>
    `).join("") : "<p class='muted'>Nog geen leads gevonden.</p>";
  }} catch (e) {{
    leadsBox.innerHTML = "<p class='muted'>" + escapeHtml(e.message) + "</p>";
  }}
}}

async function loadSessions() {{
  try {{
    const data = await api("/dashboard/chat/sessions");
    sessionsBox.innerHTML = (data.sessions || []).length ? data.sessions.map(s => `
      <div class="list-card">
        <strong>${{escapeHtml(s.session_id)}}</strong><br>
        <span class="muted">${{escapeHtml(String(s.total_messages))}} berichten • Laatste: ${{escapeHtml(formatDate(s.last_message_at))}}</span>
        <div style="margin-top:10px;">
          <button class="secondary" style="width:auto;" onclick="loadTranscript('${{encodeURIComponent(s.session_id)}}')">Open transcript</button>
        </div>
      </div>
    `).join("") : "<p class='muted'>Nog geen chatsessies gevonden.</p>";
  }} catch (e) {{
    sessionsBox.innerHTML = "<p class='muted'>" + escapeHtml(e.message) + "</p>";
  }}
}}

async function loadTranscript(encodedSessionId) {{
  const sessionId = decodeURIComponent(encodedSessionId);
  activeSessionPill.textContent = sessionId;
  transcriptBox.innerHTML = "<p class='muted'>Transcript laden...</p>";

  try {{
    const data = await api("/dashboard/chat/session/" + encodeURIComponent(sessionId));
    transcriptBox.innerHTML = (data.messages || []).length ? data.messages.map(m => `
      <div class="list-card ${{m.role === 'user' ? 'message-user' : 'message-assistant'}}">
        <strong>${{escapeHtml(m.role)}}</strong>
        <div class="muted" style="margin-top:4px;">${{escapeHtml(formatDate(m.created_at))}}</div>
        <div style="margin-top:8px; white-space:pre-wrap;">${{escapeHtml(m.content)}}</div>
      </div>
    `).join("") : "<p class='muted'>Geen berichten gevonden.</p>";
  }} catch (e) {{
    transcriptBox.innerHTML = "<p class='muted'>" + escapeHtml(e.message) + "</p>";
  }}
}}

async function saveSettings() {{
  saveStatus.textContent = "Bezig...";
  try {{
    const data = await api("/dashboard/settings", {{
      method:"POST",
      body: JSON.stringify({{
        name: name.value.trim(),
        support_email: support_email.value.trim(),
        website_url: website_url.value.trim(),
        widget_color: widget_color.value.trim(),
        company_description: company_description.value.trim(),
        faq_context: faq_context.value.trim()
      }})
    }});
    apiKeyBox.textContent = data.tenant.api_key || "";
    embedBox.textContent = data.embed_code || "";
    saveStatus.textContent = "Opgeslagen.";
    await loadDashboard();
  }} catch(e) {{
    saveStatus.textContent = e.message;
  }}
}}

async function sendInvite() {{
  try {{
    const data = await api("/dashboard/team/invite", {{
      method:"POST",
      body: JSON.stringify({{
        email: invite_email.value.trim(),
        full_name: invite_full_name.value.trim()
      }})
    }});
    inviteResult.textContent = JSON.stringify(data, null, 2);
    await loadDashboard();
  }} catch(e) {{
    inviteResult.textContent = e.message;
  }}
}}

async function openPortal() {{
  try {{
    const data = await api("/dashboard/billing-portal", {{
      method:"POST",
      body: JSON.stringify({{}})
    }});
    if (data.url) window.open(data.url, "_blank");
  }} catch(e) {{
    alert(e.message);
  }}
}}

async function rotateApiKey() {{
  apiStatus.textContent = "API key vernieuwen...";
  try {{
    const data = await api("/dashboard/rotate-api-key", {{
      method:"POST",
      body: JSON.stringify({{}})
    }});
    apiKeyBox.textContent = data.tenant.api_key || "";
    embedBox.textContent = data.embed_code || "";
    apiStatus.textContent = "Nieuwe API key opgeslagen.";
  }} catch(e) {{
    apiStatus.textContent = e.message;
  }}
}}

function copyApiKey() {{
  navigator.clipboard.writeText(apiKeyBox.textContent || "");
  apiStatus.textContent = "API key gekopieerd.";
}}

function exportLeads() {{
  const q = encodeURIComponent((leadSearch.value || "").trim());
  window.location.href = "/dashboard/leads/export.csv?q=" + q;
}}

async function logout() {{
  await api("/dashboard/logout", {{
    method:"POST",
    body: JSON.stringify({{}})
  }});
  window.location.href = "/dashboard/login";
}}

(async()=>{{
  try {{
    await loadDashboard();
  }} catch(e) {{
    window.location.href = "/dashboard/login";
  }}
}})();
</script>
''',
    )


def render_admin_html():
    return render_shell(
        "Assistify Admin",
        '''
<div id="loginView" class="card" style="max-width:720px;margin:0 auto;">
  <div class="kicker">🛡 Admin omgeving</div>
  <h2>Admin login</h2>
  <input id="u" placeholder="admin" />
  <input id="p" type="password" placeholder="wachtwoord" />
  <button onclick="login()">Inloggen</button>
  <p id="status" class="muted"></p>
</div>

<div id="appView" style="display:none;">
  <div class="hero">
    <div class="section-title">
      <div>
        <div class="kicker">📊 Overzicht</div>
        <h1>Assistify Admin Dashboard</h1>
        <p class="muted">Bekijk tenants, gebruik en audit logs in een nettere adminomgeving.</p>
      </div>
    </div>
  </div>

  <div id="overview" class="grid grid-4"></div>

  <div class="grid grid-2" style="margin-top:18px;">
    <div class="card">
      <h2>Tenants</h2>
      <table>
        <thead><tr><th>Naam</th><th>Slug</th><th>Plan</th><th>Status</th></tr></thead>
        <tbody id="tenants"></tbody>
      </table>
    </div>

    <div class="card">
      <h2>Audit logs</h2>
      <table>
        <thead><tr><th>Tijd</th><th>Actor</th><th>Actie</th><th>Target</th></tr></thead>
        <tbody id="auditLogs"></tbody>
      </table>
    </div>
  </div>
</div>

<script>
async function api(path, options={{}}) {{
  const headers = options.headers || {{}};
  if (!headers["Content-Type"] && !(options.body instanceof FormData)) {{
    headers["Content-Type"] = "application/json";
  }}
  const res = await fetch(path, {{...options, headers, credentials:"include"}});
  const data = await res.json().catch(() => ({{}}));
  if (!res.ok) throw new Error(data.error || "Er ging iets mis.");
  return data;
}}

function formatDate(ts) {{
  return ts ? new Date(ts * 1000).toLocaleString() : "-";
}}

async function login() {{
  try {{
    await api("/admin/login", {{
      method:"POST",
      body:JSON.stringify({{username:u.value.trim(), password:p.value.trim()}})
    }});
    load();
  }} catch(e) {{
    status.textContent = e.message;
  }}
}}

async function load() {{
  loginView.style.display = "none";
  appView.style.display = "block";

  const s = await api("/admin/stats/overview");
  overview.innerHTML = `
    <div class="card"><div class="muted">Tenants</div><div class="stats">${{s.stats.tenant_count}}</div></div>
    <div class="card"><div class="muted">Leads</div><div class="stats">${{s.stats.lead_count_total}}</div></div>
    <div class="card"><div class="muted">Berichten</div><div class="stats">${{s.stats.message_count_total}}</div></div>
    <div class="card"><div class="muted">Sessies</div><div class="stats">${{s.stats.session_count_total}}</div></div>
  `;

  const t = await api("/admin/tenants");
  tenants.innerHTML = (t.tenants || []).map(x => `
    <tr>
      <td>${{x.name}}</td>
      <td>${{x.slug}}</td>
      <td>${{x.plan_name}}</td>
      <td>${{x.subscription_status}}</td>
    </tr>
  `).join("");

  const logs = await api("/admin/audit-logs");
  auditLogs.innerHTML = (logs.logs || []).map(x => `
    <tr>
      <td>${{formatDate(x.created_at)}}</td>
      <td>${{x.actor_type}}:${{x.actor_id || "-"}}</td>
      <td>${{x.action}}</td>
      <td>${{x.target_type}}:${{x.target_id || "-"}}</td>
    </tr>
  `).join("");
}}

(async()=>{{
  try {{
    await api("/admin/me");
    load();
  }} catch(e) {{}}
}})();
</script>
''',
    )


def render_invite_accept_html(token: str, invite_row: dict):
    return render_shell(
        "Team Invite",
        f'''
<div class="card" style="max-width:760px;margin:0 auto;">
  <div class="kicker">👥 Team uitnodiging</div>
  <h2>Je bent uitgenodigd</h2>
  <p class="muted">Maak hieronder je account aan.</p>
  <input id="full_name" value="{invite_row.get('full_name','')}" placeholder="Naam" />
  <input id="email" value="{invite_row.get('email','')}" placeholder="E-mail" />
  <input id="password" type="password" placeholder="Wachtwoord (minimaal 8 tekens)" />
  <button onclick="acceptInvite()">Account maken</button>
  <p id="status" class="muted"></p>
</div>

<script>
async function acceptInvite() {{
  status.textContent = "Bezig...";
  try {{
    const res = await fetch("/invite/{token}", {{
      method: "POST",
      headers: {{"Content-Type":"application/json"}},
      body: JSON.stringify({{
        full_name: full_name.value.trim(),
        email: email.value.trim(),
        password: password.value.trim()
      }})
    }});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Er ging iets mis.");
    status.innerHTML = 'Account aangemaakt. Login via <a href="/dashboard/login">/dashboard/login</a> met tenant slug.';
  }} catch(e) {{
    status.textContent = e.message;
  }}
}}
</script>
''',
    )


def render_reset_request_html():
    return render_shell(
        "Wachtwoord reset aanvragen",
        '''
<div class="card" style="max-width:760px;margin:0 auto;">
  <div class="kicker">🔑 Wachtwoord reset</div>
  <h2>Vraag een reset aan</h2>
  <input id="email" placeholder="jij@bedrijf.nl" />
  <button onclick="requestReset()">Reset aanvragen</button>
  <pre id="result"></pre>
</div>

<script>
async function requestReset() {{
  try {{
    const res = await fetch("/reset-password/request", {{
      method:"POST",
      headers: {{"Content-Type":"application/json"}},
      body: JSON.stringify({{email: email.value.trim()}})
    }});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Er ging iets mis.");
    result.textContent = JSON.stringify(data, null, 2);
  }} catch(e) {{
    result.textContent = e.message;
  }}
}}
</script>
''',
    )


def render_reset_password_html(token: str):
    return render_shell(
        "Nieuw wachtwoord",
        f'''
<div class="card" style="max-width:760px;margin:0 auto;">
  <div class="kicker">🔒 Nieuw wachtwoord</div>
  <h2>Stel een nieuw wachtwoord in</h2>
  <input id="password" type="password" placeholder="Nieuw wachtwoord" />
  <button onclick="saveNewPassword()">Opslaan</button>
  <p id="status" class="muted"></p>
</div>

<script>
async function saveNewPassword() {{
  status.textContent = "Bezig...";
  try {{
    const res = await fetch("/reset-password/{token}", {{
      method:"POST",
      headers: {{"Content-Type":"application/json"}},
      body: JSON.stringify({{password: password.value.trim()}})
    }});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Er ging iets mis.");
    status.innerHTML = 'Wachtwoord opgeslagen. Login via <a href="/dashboard/login">/dashboard/login</a>';
  }} catch(e) {{
    status.textContent = e.message;
  }}
}}
</script>
''',
    )# =========================
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
# PUBLIC ROUTES
# =========================
@app.route("/", methods=["GET"])
def home():
    return Response(render_homepage_html(), mimetype="text/html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify(
        {
            "ok": True,
            "version": APP_VERSION,
            "openai_configured": bool(OPENAI_API_KEY),
            "model": MODEL_NAME,
            "database": DB_PATH,
            "stripe_configured": bool(stripe and STRIPE_SECRET_KEY),
            "stripe_webhook_configured": bool(STRIPE_WEBHOOK_SECRET),
            "smtp_enabled": SMTP_ENABLED,
            "startup_done": _startup_done,
        }
    )


@app.route("/version", methods=["GET"])
def version():
    return jsonify({"ok": True, "version": APP_VERSION, "model": MODEL_NAME})


@app.route("/admin", methods=["GET"])
def admin_dashboard():
    return Response(render_admin_html(), mimetype="text/html")


@app.route("/signup", methods=["GET"])
def signup_page():
    return Response(render_signup_html(), mimetype="text/html")


@app.route("/signup/success", methods=["GET"])
def signup_success():
    return Response(render_signup_success_html(), mimetype="text/html")


@app.route("/signup/cancel", methods=["GET"])
def signup_cancel():
    return Response(render_signup_cancel_html(), mimetype="text/html")


@app.route("/dashboard/login", methods=["GET"])
def dashboard_login_page():
    return Response(render_dashboard_login_html(), mimetype="text/html")


@app.route("/dashboard", methods=["GET"])
def dashboard_page():
    return Response(render_dashboard_html(), mimetype="text/html")


@app.route("/invite/<token>", methods=["GET"])
def invite_accept_page(token):
    row = get_invite_token_row(token)
    if not row:
        return Response("Invite niet gevonden.", status=404)
    if int(row.get("is_used", 0)) == 1:
        return Response("Invite is al gebruikt.", status=400)
    if token_is_expired(row.get("created_at", 0)):
        return Response("Invite is verlopen.", status=400)
    return Response(render_invite_accept_html(token, row), mimetype="text/html")


@app.route("/reset-password/request", methods=["GET"])
def reset_request_page():
    return Response(render_reset_request_html(), mimetype="text/html")


@app.route("/reset-password/<token>", methods=["GET"])
def reset_password_page(token):
    row = get_password_reset_token_row(token)
    if not row:
        return Response("Reset token niet gevonden.", status=404)
    if int(row.get("is_used", 0)) == 1:
        return Response("Reset token is al gebruikt.", status=400)
    if token_is_expired(row.get("created_at", 0)):
        return Response("Reset token is verlopen.", status=400)
    return Response(render_reset_password_html(token), mimetype="text/html")


# =========================
# STRIPE WEBHOOK
# =========================
@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    if not stripe or not STRIPE_SECRET_KEY:
        return jsonify({"ok": False, "error": "Stripe is niet geconfigureerd."}), 503

    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = json.loads(payload.decode("utf-8"))
    except Exception as e:
        return jsonify({"ok": False, "error": "Webhook validatie mislukt.", "details": str(e)}), 400

    event_type = event.get("type", "")
    data_object = ((event.get("data") or {}).get("object") or {})

    try:
        if event_type == "checkout.session.completed":
            metadata = data_object.get("metadata") or {}

            if metadata.get("source") == "public_signup":
                customer_id = str(data_object.get("customer") or "").strip()
                subscription_id = str(data_object.get("subscription") or "").strip()
                plan_name = str(metadata.get("plan_name") or "starter").strip().lower()

                tenant = get_tenant_by_stripe_customer_id(customer_id) if customer_id else None
                if tenant:
                    sync_tenant_subscription(
                        tenant["id"],
                        plan_name=plan_name,
                        subscription_status="active",
                        stripe_subscription_id=subscription_id,
                    )
                    create_audit_log(
                        tenant["id"],
                        "stripe",
                        customer_id,
                        "stripe_checkout_completed",
                        "tenant",
                        tenant["id"],
                        {"plan_name": plan_name, "subscription_id": subscription_id},
                        get_client_ip(),
                    )

        elif event_type in (
            "customer.subscription.updated",
            "customer.subscription.created",
            "customer.subscription.deleted",
        ):
            subscription_id = str(data_object.get("id") or "").strip()
            customer_id = str(data_object.get("customer") or "").strip()
            status = str(data_object.get("status") or "").strip().lower()

            tenant = get_tenant_by_stripe_subscription_id(subscription_id)
            if not tenant and customer_id:
                tenant = get_tenant_by_stripe_customer_id(customer_id)

            if tenant:
                plan_name = tenant["plan_name"]
                items = (((data_object.get("items") or {}).get("data")) or [])
                price_ids = [
                    str(((item.get("price") or {}).get("id")) or "").strip()
                    for item in items
                ]

                if STRIPE_PRICE_AGENCY_MONTHLY and STRIPE_PRICE_AGENCY_MONTHLY in price_ids:
                    plan_name = "agency"
                elif STRIPE_PRICE_PRO_MONTHLY and STRIPE_PRICE_PRO_MONTHLY in price_ids:
                    plan_name = "pro"
                elif STRIPE_PRICE_STARTER_MONTHLY and STRIPE_PRICE_STARTER_MONTHLY in price_ids:
                    plan_name = "starter"

                sync_tenant_subscription(
                    tenant["id"],
                    plan_name=plan_name,
                    subscription_status=status,
                    stripe_subscription_id=subscription_id,
                )
                create_audit_log(
                    tenant["id"],
                    "stripe",
                    customer_id,
                    "stripe_subscription_synced",
                    "tenant",
                    tenant["id"],
                    {"status": status, "subscription_id": subscription_id, "plan_name": plan_name},
                    get_client_ip(),
                )

        return jsonify({"ok": True})

    except Exception as e:
        return jsonify({"ok": False, "error": "Webhook verwerking mislukt.", "details": str(e)}), 500


# =========================
# SIGNUP FLOW
# =========================
@app.route("/signup/create-checkout", methods=["POST"])
def signup_create_checkout():
    data = json_body()
    email = clamp_text(data.get("email") or "", 200)
    plan_name = clamp_text(data.get("plan_name") or "starter", 50).lower()

    if plan_name not in ("starter", "pro", "agency"):
        return jsonify({"ok": False, "error": "Ongeldig plan."}), 400

    try:
        session_obj = create_public_signup_checkout(email, plan_name)
        return jsonify({"ok": True, "url": session_obj.url, "session_id": session_obj.id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/signup/finalize", methods=["GET"])
def signup_finalize():
    if not stripe or not STRIPE_SECRET_KEY:
        return jsonify({"ok": False, "error": "Stripe is niet geconfigureerd."}), 503

    session_id = (request.args.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"ok": False, "error": "session_id ontbreekt."}), 400

    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        if not checkout_session:
            return jsonify({"ok": False, "error": "Checkout sessie niet gevonden."}), 404

        metadata = checkout_session.get("metadata") or {}
        if metadata.get("source") != "public_signup":
            return jsonify({"ok": False, "error": "Dit is geen public signup checkout."}), 400

        customer_id = str(checkout_session.get("customer", "") or "").strip()
        subscription_id = str(checkout_session.get("subscription", "") or "").strip()
        email = str(
            checkout_session.get("customer_email")
            or ((checkout_session.get("customer_details") or {}).get("email"))
            or metadata.get("signup_email")
            or ""
        ).strip()
        plan_name = str(metadata.get("plan_name") or "starter").strip().lower()

        existing = get_tenant_by_stripe_customer_id(customer_id) if customer_id else None
        if existing:
            sync_tenant_subscription(
                existing["id"],
                plan_name=plan_name,
                subscription_status="active",
                stripe_subscription_id=subscription_id,
            )
            token = create_onboarding_token(existing["id"], email)
            base_url = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/")
            return jsonify({"ok": True, "redirect_url": f"{base_url}/signup/complete/{token}"})

        tenant, token = create_selfserve_tenant_from_checkout(
            email, plan_name, customer_id, subscription_id
        )
        base_url = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/")
        return jsonify({"ok": True, "tenant_id": tenant["id"], "redirect_url": f"{base_url}/signup/complete/{token}"})

    except Exception as e:
        return jsonify({"ok": False, "error": "Signup finaliseren mislukt.", "details": str(e)}), 500


@app.route("/signup/complete/<token>", methods=["GET"])
def signup_complete_view(token):
    row = get_onboarding_token_row(token)
    if not row:
        return Response("Onboarding token niet gevonden.", status=404)
    if int(row.get("is_used", 0)) == 1:
        return Response("Onboarding token is al gebruikt.", status=400)
    if token_is_expired(row.get("created_at", 0)):
        return Response("Onboarding token is verlopen.", status=400)

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
    if token_is_expired(row.get("created_at", 0)):
        return jsonify({"ok": False, "error": "Onboarding token is verlopen."}), 400

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
    widget_color = normalize_hex_color(data.get("widget_color") or tenant.get("widget_color") or DEFAULT_WIDGET_COLOR)
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
                website_url = ?, widget_color = ?, company_description = ?
            WHERE id = ?
            """,
            (name, slug, support_email, support_email, website_url, widget_color, company_description, tenant["id"]),
        )
        conn.commit()

    existing_user = get_customer_user_by_email_and_tenant(support_email, tenant["id"])
    if not existing_user:
        user, error = create_customer_user(
            tenant["id"],
            support_email,
            password,
            full_name=full_name,
            is_owner=True,
        )
        if error:
            return jsonify({"ok": False, "error": error}), 400

        create_audit_log(
            tenant["id"],
            "customer_user",
            user["id"],
            "owner_created_from_onboarding",
            "customer_user",
            user["id"],
            {"email": user["email"]},
            get_client_ip(),
        )

    mark_onboarding_token_used(token)
    tenant = get_tenant_by_id(tenant["id"])

    create_audit_log(
        tenant["id"],
        "system",
        "",
        "onboarding_completed",
        "tenant",
        tenant["id"],
        {"name": tenant["name"], "slug": tenant["slug"], "support_email": tenant["support_email"]},
        get_client_ip(),
    )

    host = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/")
    embed_code = f'''<script src="{host}/widget.js" data-api-base="{host}" data-tenant-key="{tenant["api_key"]}" data-title="{tenant["name"]}" data-color="{tenant.get("widget_color") or DEFAULT_WIDGET_COLOR}" ></script>'''

    return jsonify(
        {
            "ok": True,
            "tenant": {
                "id": tenant["id"],
                "name": tenant["name"],
                "slug": tenant["slug"],
                "api_key": tenant["api_key"],
                "widget_color": tenant.get("widget_color") or DEFAULT_WIDGET_COLOR,
            },
            "embed_code": embed_code,
        }
    )


# =========================
# INVITE FLOW
# =========================
@app.route("/invite/<token>", methods=["POST"])
def invite_accept_submit(token):
    row = get_invite_token_row(token)
    if not row:
        return jsonify({"ok": False, "error": "Invite niet gevonden."}), 404
    if int(row.get("is_used", 0)) == 1:
        return jsonify({"ok": False, "error": "Invite is al gebruikt."}), 400
    if token_is_expired(row.get("created_at", 0)):
        return jsonify({"ok": False, "error": "Invite is verlopen."}), 400

    data = json_body()
    email = clamp_text(data.get("email") or row["email"], 200).lower()
    full_name = clamp_text(data.get("full_name") or row.get("full_name", ""), 200)
    password = (data.get("password") or "").strip()

    if email != row["email"].lower():
        return jsonify({"ok": False, "error": "E-mailadres klopt niet met de invite."}), 400

    user, error = create_customer_user(row["tenant_id"], email, password, full_name=full_name, is_owner=False)
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


# =========================
# RESET PASSWORD
# =========================
@app.route("/reset-password/request", methods=["POST"])
def reset_password_request_submit():
    data = json_body()
    email = clamp_text(data.get("email") or "", 200)
    user = get_customer_user_by_email_global(email)

    if not user:
        return jsonify({"ok": False, "error": "Gebruiker niet gevonden."}), 404

    token = create_password_reset_token(user["tenant_id"], user["id"], user["email"])
    base_url = PUBLIC_APP_URL.strip() or request.host_url.rstrip("/")
    reset_url = f"{base_url}/reset-password/{token}"

    email_sent = False
    email_error = None

    try:
        tenant = get_tenant_by_id(user["tenant_id"])
        tenant_name = tenant["name"] if tenant else "Assistify"
        email_sent, email_error = send_password_reset_email(user["email"], reset_url, tenant_name)
    except Exception as e:
        email_sent = False
        email_error = str(e)

    create_audit_log(
        user["tenant_id"],
        "customer_user",
        user["id"],
        "password_reset_requested",
        "customer_user",
        user["id"],
        {"email": user["email"], "email_sent": email_sent, "email_error": email_error},
        get_client_ip(),
    )

    return jsonify(
        {
            "ok": True,
            "reset_url": reset_url,
            "email_sent": email_sent,
            "email_error": email_error,
        }
    )


@app.route("/reset-password/<token>", methods=["POST"])
def reset_password_submit(token):
    row = get_password_reset_token_row(token)
    if not row:
        return jsonify({"ok": False, "error": "Reset token niet gevonden."}), 404
    if int(row.get("is_used", 0)) == 1:
        return jsonify({"ok": False, "error": "Reset token is al gebruikt."}), 400
    if token_is_expired(row.get("created_at", 0)):
        return jsonify({"ok": False, "error": "Reset token is verlopen."}), 400

    data = json_body()
    password = (data.get("password") or "").strip()

    ok, error = update_customer_password(row["user_id"], password)
    if not ok:
        return jsonify({"ok": False, "error": error}), 400

    mark_password_reset_token_used(token)

    create_audit_log(
        row["tenant_id"],
        "customer_user",
        row["user_id"],
        "password_reset_completed",
        "customer_user",
        row["user_id"],
        {"email": row["email"]},
        get_client_ip(),
    )

    return jsonify({"ok": True})
