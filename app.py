import os
import sqlite3
from datetime import datetime
from urllib.parse import quote_plus
from flask import Flask, render_template, request, redirect, url_for, flash

from slugify import slugify
import markdown as md
import bleach

try:
    from dotenv import load_dotenv
    load_dotenv()
    print("Loaded ADMIN_PASSWORD from env:", os.environ.get("ADMIN_PASSWORD"))
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
except Exception:
    pass

def check_admin(pw: str) -> bool:
    return pw == ADMIN_PASSWORD

def render_markdown(md_text: str) -> str:
    html = md.markdown(md_text, extensions=["extra", "tables", "fenced_code"])
    allowed_tags = bleach.sanitizer.ALLOWED_TAGS | {"p","img","h1","h2","h3","h4","pre","code","table","thead","tbody","tr","th","td","hr","br"}
    allowed_attrs = {"a":["href","title","rel","target"], "img":["src","alt","title"], "code":["class"]}
    return bleach.clean(html, tags=allowed_tags, attributes=allowed_attrs)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "devkey")

SCHEDULER_URL = os.environ.get("SCHEDULER_URL", "").strip().rstrip("/")
if not SCHEDULER_URL:
    SCHEDULER_URL = "https://calendly.com/jared-jaredmclinden/home-evaluation"

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "instance", "leads.sqlite3")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS home_eval_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            address TEXT NOT NULL,
            property_type TEXT,
            timeframe TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            summary TEXT,
            content_md TEXT NOT NULL,
            content_html TEXT NOT NULL,
            cover_url TEXT,
            published INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
init_db()

@app.route("/home-evaluation", methods=["GET", "POST"])
def home_evaluation():
    if request.method == "POST":
        data = {
            "full_name": request.form.get("full_name","").strip(),
            "email": request.form.get("email","").strip(),
            "phone": request.form.get("phone","").strip(),
            "address": request.form.get("address","").strip(),
            "property_type": request.form.get("property_type","").strip(),
            "timeframe": request.form.get("timeframe","").strip(),
            "notes": request.form.get("notes","").strip(),
        }

        if not data["full_name"] or not data["email"] or not data["address"]:
            flash("Name, email, and property address are required.")
            return render_template("home_evaluation.html", form=data)

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO home_eval_leads
                (full_name,email,phone,address,property_type,timeframe,notes,created_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                data["full_name"], data["email"], data["phone"], data["address"],
                data["property_type"], data["timeframe"], data["notes"],
                datetime.utcnow().isoformat()
            ))

        return redirect(url_for("schedule", name=data["full_name"], email=data["email"]))

    return render_template("home_evaluation.html", form={})

@app.route("/schedule")
def schedule():
    name  = (request.args.get("name") or "").strip()
    email = (request.args.get("email") or "").strip()

    params = {
        "hide_gdpr_banner": "1",
        "hide_event_type_details": "1",
        "background_color": "F7F7F7",
        "text_color": "111827",
        "primary_color": "2563EB",
    }
    if name:  params["name"]  = name
    if email: params["email"] = email

    query = "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())
    sep = "&" if "?" in SCHEDULER_URL else "?"
    calendly_embed_url = f"{SCHEDULER_URL}{sep}{query}"

    print("DEBUG SCHEDULER_URL =", repr(SCHEDULER_URL))
    print("DEBUG EMBED URL     =", calendly_embed_url)

    return render_template("schedule.html", calendly_embed_url=calendly_embed_url)

from flask import abort

@app.route("/blog")
def blog_index():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        posts = conn.execute("""
            SELECT id, title, slug, summary, cover_url, created_at
            FROM posts
            WHERE published = 1
            ORDER BY datetime(created_at) DESC
        """).fetchall()
    return render_template("blog_index.html", posts=posts)

@app.route("/blog/<slug>")
def blog_post(slug):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        post = conn.execute("""
            SELECT id, title, slug, summary, content_md, content_html, cover_url, created_at
            FROM posts
            WHERE slug = ? AND published = 1
            LIMIT 1
        """, (slug,)).fetchone()

    if not post:
        abort(404)

    html = (post["content_html"] or "").strip()
    if not html and post["content_md"]:
        html = render_markdown(post["content_md"])

        edit_url = url_for("admin_blog_edit", post_id=post["id"])
        return render_template("blog_post.html", post=post, content_html=html, edit_url=edit_url)

@app.route("/")
def home():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        latest = conn.execute("SELECT title, slug, summary, cover_url, created_at FROM posts WHERE published=1 ORDER BY created_at DESC LIMIT 3").fetchall()
    return render_template("index.html", latest_posts=latest)

@app.route("/admin/blog/new", methods=["GET","POST"])
def admin_blog_new():
    if request.method == "POST":
        if not check_admin(request.form.get("password","")):
            flash("Invalid admin password.")
            return render_template("admin_post_edit.html", form=request.form, mode="new")

        title = request.form.get("title","").strip()
        summary = request.form.get("summary","").strip()
        content_md = request.form.get("content_md","").strip()
        cover_url = request.form.get("cover_url","").strip()
        published = 1 if request.form.get("published") == "on" else 0
        if not title or not content_md:
            flash("Title and content are required.")
            return render_template("admin_post_edit.html", form=request.form, mode="new")

        slug = slugify(title)
        content_html = render_markdown(content_md)
        now = datetime.utcnow().isoformat()

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""INSERT INTO posts
                (title,slug,summary,content_md,content_html,cover_url,published,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (title,slug,summary,content_md,content_html,cover_url,published,now,now))
        return redirect(url_for("blog_post", slug=slug))

    return render_template("admin_post_edit.html", form={}, mode="new")

@app.route("/admin/blog/<int:post_id>/edit", methods=["GET","POST"])
def admin_blog_edit(post_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        return render_template("404.html"), 404

    if request.method == "POST":
        if not check_admin(request.form.get("password","")):
            flash("Invalid admin password.")
            return render_template("admin_post_edit.html", form=request.form, mode="edit", post=row)

        title = request.form.get("title","").strip()
        summary = request.form.get("summary","").strip()
        content_md = request.form.get("content_md","").strip()
        cover_url = request.form.get("cover_url","").strip()
        published = 1 if request.form.get("published") == "on" else 0
        slug = slugify(title)
        content_html = render_markdown(content_md)
        now = datetime.utcnow().isoformat()

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""UPDATE posts
                SET title=?, slug=?, summary=?, content_md=?, content_html=?, cover_url=?, published=?, updated_at=?
                WHERE id=?""",
                (title,slug,summary,content_md,content_html,cover_url,published,now,post_id))
        return redirect(url_for("blog_post", slug=slug))

    return render_template("admin_post_edit.html", form=row, mode="edit", post=row)

if __name__ == "__main__":
    app.run(debug=True)