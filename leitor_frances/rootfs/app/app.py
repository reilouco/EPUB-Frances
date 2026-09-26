import os
import re
import json
import uuid
import sqlite3
from pathlib import Path
from functools import lru_cache
from datetime import datetime, timezone
from contextlib import contextmanager

import bleach
import argostranslate.translate
from bs4 import BeautifulSoup
from ebooklib import epub, ITEM_DOCUMENT, ITEM_COVER, ITEM_IMAGE

from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Form
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path("/app")
DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/data"))
BOOKS_DIR = DATA_DIR / "books"
COVERS_DIR = DATA_DIR / "covers"
DB_PATH = DATA_DIR / "leitor_frances.db"
DIFFICULTIES = {"facil", "medio", "dificil"}


def load_options():
    try:
        return json.loads((DATA_DIR / "options.json").read_text())
    except Exception:
        return {}


OPTIONS = load_options()


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


MAX_UPLOAD_MB = _int(OPTIONS.get("max_upload_mb", os.getenv("MAX_UPLOAD_MB")), 50)
SHOW_CONTEXT_DEFAULT = str(
    OPTIONS.get("mostrar_contexto_por_padrao", os.getenv("SHOW_CONTEXT_DEFAULT", "false"))
).lower() == "true"

BOOKS_DIR.mkdir(parents=True, exist_ok=True)
COVERS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Leitor Francês Contextual")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

@app.middleware("http")
async def no_cache_static(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


ALLOWED_TAGS = [
    "p", "div", "span", "em", "i", "strong", "b", "br",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "ul", "ol", "li", "hr", "sup", "sub"
]
ALLOWED_ATTRIBUTES = {"*": ["class"]}


@contextmanager
def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def now():
    return datetime.now(timezone.utc).isoformat()


# ---------- Normalização de expressões (igual ao front-end) ----------
def norm_word(word: str) -> str:
    word = word.lower().replace("’", "'")
    return re.sub(r"^[\W_]+|[\W_]+$", "", word)


def phrase_key(text: str) -> str:
    return " ".join(w for w in (norm_word(t) for t in text.split()) if w)


def init_database():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS books (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, author TEXT,
            language TEXT, cover_path TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id TEXT NOT NULL, chapter_index INTEGER NOT NULL,
            title TEXT NOT NULL, content_html TEXT NOT NULL, plain_text TEXT NOT NULL,
            UNIQUE(book_id, chapter_index)
        );
        CREATE TABLE IF NOT EXISTS reading_progress (
            user_id TEXT NOT NULL, book_id TEXT NOT NULL,
            chapter_index INTEGER NOT NULL DEFAULT 0,
            scroll_percent REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, book_id)
        );
        CREATE TABLE IF NOT EXISTS phrases (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, book_id TEXT,
            phrase_fr TEXT NOT NULL, translation_pt TEXT NOT NULL,
            context_fr TEXT, context_pt TEXT, chapter_index INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS phrase_marks (
            user_id TEXT NOT NULL, phrase_key TEXT NOT NULL,
            phrase_fr TEXT NOT NULL, difficulty TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, phrase_key)
        );
        CREATE INDEX IF NOT EXISTS idx_phrases_user_book ON phrases(user_id, book_id);
        """)

        # Migração: chave normalizada nos cartões existentes
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(phrases)")}
        if "phrase_key" not in cols:
            conn.execute("ALTER TABLE phrases ADD COLUMN phrase_key TEXT")
        for row in conn.execute(
            "SELECT id, phrase_fr FROM phrases WHERE phrase_key IS NULL"
        ).fetchall():
            conn.execute("UPDATE phrases SET phrase_key = ? WHERE id = ?",
                         (phrase_key(row["phrase_fr"]), row["id"]))
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_phrases_user_key ON phrases(user_id, phrase_key)"
        )


@app.on_event("startup")
def startup():
    init_database()


def current_user(request: Request):
    user_id = (
        request.headers.get("X-Remote-User-Id")
        or request.headers.get("X-Remote-User-Name")
        or "usuario-local"
    )
    name = (
        request.headers.get("X-Remote-User-Display-Name")
        or request.headers.get("X-Remote-User-Name")
        or "Usuário local"
    )
    with db() as conn:
        conn.execute("""
            INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name = excluded.name
        """, (user_id, name, now()))
    return {"id": user_id, "name": name}


def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "img", "svg", "iframe", "object"]):
        tag.decompose()
    body = soup.body if soup.body else soup
    return bleach.clean(str(body), tags=ALLOWED_TAGS,
                        attributes=ALLOWED_ATTRIBUTES, strip=True)


def chapter_title(html: str, fallback: str) -> str:
    heading = BeautifulSoup(html, "html.parser").find(["h1", "h2", "h3", "title"])
    if heading:
        text = heading.get_text(" ", strip=True)
        if text:
            return text[:160]
    return fallback


def get_book(book_id: str):
    with db() as conn:
        book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        raise HTTPException(status_code=404, detail="Livro não encontrado.")
    return dict(book)


# ---------- Capas ----------
def image_ext(data: bytes):
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return None


def store_cover(book_id: str, data: bytes):
    ext = image_ext(data)
    if not ext:
        return None
    for old in COVERS_DIR.glob(f"{book_id}.*"):
        old.unlink(missing_ok=True)
    name = f"{book_id}{ext}"
    (COVERS_DIR / name).write_bytes(data)
    return name


def extract_epub_cover(loaded_book, book_id):
    try:
        items = list(loaded_book.get_items_of_type(ITEM_COVER))
        if not items:
            items = [i for i in loaded_book.get_items_of_type(ITEM_IMAGE)
                     if "cover" in (i.get_name() or "").lower()]
        return store_cover(book_id, items[0].get_content()) if items else None
    except Exception:
        return None


def cover_url(book: dict):
    name = book.get("cover_path")
    if name and (COVERS_DIR / name).is_file():
        mtime = int((COVERS_DIR / name).stat().st_mtime)
        return f"./api/books/{book['id']}/cover?v={mtime}"
    return None


# ---------- Tradução ----------
@lru_cache(maxsize=1)
def _translator():
    langs = argostranslate.translate.get_installed_languages()
    fr = next((l for l in langs if l.code == "fr"), None)
    pt = next((l for l in langs if l.code == "pt"), None)
    if not fr or not pt:
        raise RuntimeError("Modelo francês-português não está instalado.")
    return fr.get_translation(pt)


@lru_cache(maxsize=4096)
def _translate_cached(text: str) -> str:
    return _translator().translate(text)


def translate_local(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    try:
        return _translate_cached(text)
    except Exception as error:
        raise HTTPException(status_code=503,
                            detail=f"Tradutor local indisponível: {error}")


# ---------- Rotas ----------
@app.get("/")
def index():
    return FileResponse(APP_DIR / "static" / "index.html")


@app.get("/api/me")
def me(request: Request):
    return {"user": current_user(request), "show_context_default": SHOW_CONTEXT_DEFAULT}


@app.get("/api/books")
def list_books(request: Request):
    user = current_user(request)
    with db() as conn:
        rows = conn.execute("""
            SELECT b.*,
                (SELECT COUNT(*) FROM chapters c WHERE c.book_id = b.id) AS total_chapters,
                COALESCE(r.chapter_index, 0) AS current_chapter,
                COALESCE(r.scroll_percent, 0) AS scroll_percent,
                r.updated_at AS last_read
            FROM books b
            LEFT JOIN reading_progress r ON r.book_id = b.id AND r.user_id = ?
            ORDER BY COALESCE(r.updated_at, b.created_at) DESC
        """, (user["id"],)).fetchall()

    books = []
    for row in rows:
        item = dict(row)
        total = max(item["total_chapters"], 1)
        progress = ((item["current_chapter"] + item["scroll_percent"] / 100) / total) * 100
        item["progress_percent"] = round(min(progress, 100), 1)
        item["cover_url"] = cover_url(item)
        books.append(item)
    return books


@app.post("/api/books/upload")
async def upload_book(request: Request, file: UploadFile = File(...)):
    current_user(request)
    if not file.filename.lower().endswith(".epub"):
        raise HTTPException(status_code=400, detail="Envie somente arquivos EPUB.")

    content = await file.read()
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400,
                            detail=f"O EPUB excede o limite de {MAX_UPLOAD_MB} MB.")

    book_id = str(uuid.uuid4())
    epub_path = BOOKS_DIR / f"{book_id}.epub"
    epub_path.write_bytes(content)

    try:
        loaded = epub.read_epub(str(epub_path))
        m_title = loaded.get_metadata("DC", "title")
        m_author = loaded.get_metadata("DC", "creator")
        m_lang = loaded.get_metadata("DC", "language")
        title = m_title[0][0] if m_title else Path(file.filename).stem
        author = m_author[0][0] if m_author else "Autor desconhecido"
        language = m_lang[0][0] if m_lang else "fr"

        chapters, idx = [], 0
        for item in loaded.get_items():
            if item.get_type() != ITEM_DOCUMENT:
                continue
            cleaned = clean_html(item.get_content().decode("utf-8", errors="ignore"))
            plain = BeautifulSoup(cleaned, "html.parser").get_text(" ", strip=True)
            if len(plain) < 80:
                continue
            chapters.append((idx, chapter_title(cleaned, f"Capítulo {idx + 1}"), cleaned, plain))
            idx += 1

        if not chapters:
            raise ValueError("Não encontrei capítulos legíveis nesse EPUB.")

        cover = extract_epub_cover(loaded, book_id)

        with db() as conn:
            conn.execute("""
                INSERT INTO books (id, title, author, language, cover_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (book_id, title, author, language, cover, now()))
            conn.executemany("""
                INSERT INTO chapters (book_id, chapter_index, title, content_html, plain_text)
                VALUES (?, ?, ?, ?, ?)
            """, [(book_id, *c) for c in chapters])

        return {"id": book_id, "title": title, "author": author, "chapters": len(chapters)}

    except Exception as error:
        epub_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400,
                            detail=f"Não foi possível processar o EPUB: {error}")


@app.post("/api/books/{book_id}/cover")
async def upload_cover(book_id: str, request: Request, file: UploadFile = File(...)):
    current_user(request)
    get_book(book_id)
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="A capa excede 10 MB.")
    name = store_cover(book_id, data)
    if not name:
        raise HTTPException(status_code=400, detail="Envie uma imagem JPG (ou PNG).")
    with db() as conn:
        conn.execute("UPDATE books SET cover_path = ? WHERE id = ?", (name, book_id))
    return {"ok": True, "cover_url": cover_url({"id": book_id, "cover_path": name})}


@app.get("/api/books/{book_id}/cover")
def get_cover(book_id: str):
    book = get_book(book_id)
    name = book.get("cover_path")
    path = COVERS_DIR / (name or "")
    if not name or not path.is_file():
        raise HTTPException(status_code=404, detail="Sem capa.")
    media = "image/png" if name.endswith(".png") else "image/jpeg"
    return FileResponse(path, media_type=media,
                        headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/api/books/{book_id}")
def book_details(book_id: str, request: Request):
    user = current_user(request)
    book = get_book(book_id)
    with db() as conn:
        chapters = conn.execute("""
            SELECT chapter_index, title FROM chapters
            WHERE book_id = ? ORDER BY chapter_index
        """, (book_id,)).fetchall()
        progress = conn.execute("""
            SELECT chapter_index, scroll_percent FROM reading_progress
            WHERE user_id = ? AND book_id = ?
        """, (user["id"], book_id)).fetchone()
    return {
        "book": book,
        "chapters": [dict(c) for c in chapters],
        "progress": dict(progress) if progress else {"chapter_index": 0, "scroll_percent": 0},
    }


@app.get("/api/books/{book_id}/chapters/{chapter_index}")
def get_chapter(book_id: str, chapter_index: int, request: Request):
    current_user(request)
    with db() as conn:
        chapter = conn.execute("""
            SELECT chapter_index, title, content_html, plain_text FROM chapters
            WHERE book_id = ? AND chapter_index = ?
        """, (book_id, chapter_index)).fetchone()
    if not chapter:
        raise HTTPException(status_code=404, detail="Capítulo não encontrado.")
    return dict(chapter)


@app.post("/api/progress")
def save_progress(request: Request, book_id: str = Form(...),
                  chapter_index: int = Form(...), scroll_percent: float = Form(...)):
    user = current_user(request)
    scroll_percent = max(0, min(100, scroll_percent))
    with db() as conn:
        conn.execute("""
            INSERT INTO reading_progress (user_id, book_id, chapter_index, scroll_percent, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, book_id) DO UPDATE SET
                chapter_index = excluded.chapter_index,
                scroll_percent = excluded.scroll_percent,
                updated_at = excluded.updated_at
        """, (user["id"], book_id, chapter_index, scroll_percent, now()))
    return {"ok": True}


@app.post("/api/translate")
def translate(request: Request, phrase_fr: str = Form(...),
              context_fr: str = Form(""), with_context: bool = Form(False)):
    current_user(request)
    phrase_fr = re.sub(r"\s+", " ", phrase_fr).strip()
    context_fr = re.sub(r"\s+", " ", context_fr).strip()
    if not phrase_fr:
        raise HTTPException(status_code=400, detail="Expressão vazia.")
    if len(phrase_fr) > 400:
        raise HTTPException(status_code=400, detail="A expressão está longa demais.")

    context_pt = ""
    if with_context and context_fr and context_fr != phrase_fr:
        context_pt = translate_local(context_fr)

    return {"phrase_fr": phrase_fr, "translation_pt": translate_local(phrase_fr),
            "context_fr": context_fr, "context_pt": context_pt}


# ---------- Marcações (dificuldade + cartões) por usuário ----------
@app.get("/api/marks")
def get_marks(request: Request):
    user = current_user(request)
    with db() as conn:
        diffs = conn.execute(
            "SELECT phrase_key, difficulty FROM phrase_marks WHERE user_id = ?",
            (user["id"],)).fetchall()
        saved = conn.execute(
            "SELECT DISTINCT phrase_key FROM phrases WHERE user_id = ? AND phrase_key <> ''",
            (user["id"],)).fetchall()
    return {"difficulty": {r["phrase_key"]: r["difficulty"] for r in diffs},
            "saved": [r["phrase_key"] for r in saved]}


@app.post("/api/marks")
def set_mark(request: Request, phrase_fr: str = Form(...), difficulty: str = Form("")):
    user = current_user(request)
    key = phrase_key(phrase_fr)
    if not key:
        raise HTTPException(status_code=400, detail="Expressão vazia.")
    with db() as conn:
        if difficulty in DIFFICULTIES:
            conn.execute("""
                INSERT INTO phrase_marks (user_id, phrase_key, phrase_fr, difficulty, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, phrase_key) DO UPDATE SET
                    difficulty = excluded.difficulty, updated_at = excluded.updated_at
            """, (user["id"], key, phrase_fr.strip(), difficulty, now()))
        else:
            conn.execute("DELETE FROM phrase_marks WHERE user_id = ? AND phrase_key = ?",
                         (user["id"], key))
    return {"ok": True, "key": key, "difficulty": difficulty if difficulty in DIFFICULTIES else None}


@app.get("/api/phrases")
def list_phrases(request: Request):
    user = current_user(request)
    with db() as conn:
        rows = conn.execute("""
            SELECT p.*, m.difficulty, b.title AS book_title
            FROM phrases p
            LEFT JOIN phrase_marks m ON m.user_id = p.user_id AND m.phrase_key = p.phrase_key
            LEFT JOIN books b ON b.id = p.book_id
            WHERE p.user_id = ?
            ORDER BY p.created_at DESC
        """, (user["id"],)).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/phrases")
def save_phrase(request: Request, book_id: str = Form(...), chapter_index: int = Form(...),
                phrase_fr: str = Form(...), translation_pt: str = Form(...),
                context_fr: str = Form(""), context_pt: str = Form("")):
    user = current_user(request)
    key = phrase_key(phrase_fr)
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM phrases WHERE user_id = ? AND phrase_key = ?",
            (user["id"], key)).fetchone()
        if existing:
            conn.execute("""
                UPDATE phrases SET translation_pt = ?, context_fr = ?, context_pt = ?
                WHERE id = ?
            """, (translation_pt.strip(), context_fr.strip(), context_pt.strip(), existing["id"]))
            return {"id": existing["id"], "ok": True}

        phrase_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO phrases (id, user_id, book_id, phrase_fr, phrase_key, translation_pt,
                                 context_fr, context_pt, chapter_index, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (phrase_id, user["id"], book_id, phrase_fr.strip(), key, translation_pt.strip(),
              context_fr.strip(), context_pt.strip(), chapter_index, now()))
    return {"id": phrase_id, "ok": True}


@app.post("/api/phrases/unsave")
def unsave_phrase(request: Request, phrase_fr: str = Form(...)):
    user = current_user(request)
    with db() as conn:
        conn.execute("DELETE FROM phrases WHERE user_id = ? AND phrase_key = ?",
                     (user["id"], phrase_key(phrase_fr)))
    return {"ok": True}


@app.delete("/api/phrases/{phrase_id}")
def delete_phrase(phrase_id: str, request: Request):
    user = current_user(request)
    with db() as conn:
        result = conn.execute("DELETE FROM phrases WHERE id = ? AND user_id = ?",
                              (phrase_id, user["id"]))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Expressão não encontrada.")
    return {"ok": True}


@app.delete("/api/books/{book_id}")
def delete_book(book_id: str, request: Request):
    current_user(request)
    with db() as conn:
        conn.execute("DELETE FROM chapters WHERE book_id = ?", (book_id,))
        conn.execute("DELETE FROM reading_progress WHERE book_id = ?", (book_id,))
        conn.execute("UPDATE phrases SET book_id = NULL WHERE book_id = ?", (book_id,))
        result = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Livro não encontrado.")
    (BOOKS_DIR / f"{book_id}.epub").unlink(missing_ok=True)
    for f in COVERS_DIR.glob(f"{book_id}.*"):
        f.unlink(missing_ok=True)
    return {"ok": True}
