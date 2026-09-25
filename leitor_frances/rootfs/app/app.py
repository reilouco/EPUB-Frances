import os
import re
import json
import uuid
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager

import bleach
import argostranslate.translate
from bs4 import BeautifulSoup
from ebooklib import epub, ITEM_DOCUMENT

from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Form
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


APP_DIR = Path("/app")
DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/data"))
BOOKS_DIR = DATA_DIR / "books"
DB_PATH = DATA_DIR / "leitor_frances.db"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
SHOW_CONTEXT_DEFAULT = os.getenv("SHOW_CONTEXT_DEFAULT", "false").lower() == "true"

BOOKS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Leitor Francês Contextual")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

ALLOWED_TAGS = [
    "p", "div", "span", "em", "i", "strong", "b", "br",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "ul", "ol", "li", "hr", "sup", "sub"
]

ALLOWED_ATTRIBUTES = {
    "*": ["class"]
}


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


def init_database():
    with db() as conn:
        conn.executescript("""
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS books (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT,
            language TEXT,
            cover_path TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id TEXT NOT NULL,
            chapter_index INTEGER NOT NULL,
            title TEXT NOT NULL,
            content_html TEXT NOT NULL,
            plain_text TEXT NOT NULL,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            UNIQUE(book_id, chapter_index)
        );

        CREATE TABLE IF NOT EXISTS reading_progress (
            user_id TEXT NOT NULL,
            book_id TEXT NOT NULL,
            chapter_index INTEGER NOT NULL DEFAULT 0,
            scroll_percent REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, book_id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(book_id) REFERENCES books(id)
        );

        CREATE TABLE IF NOT EXISTS phrases (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            book_id TEXT,
            phrase_fr TEXT NOT NULL,
            translation_pt TEXT NOT NULL,
            context_fr TEXT,
            context_pt TEXT,
            chapter_index INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(book_id) REFERENCES books(id)
        );

        CREATE INDEX IF NOT EXISTS idx_phrases_user_book
        ON phrases(user_id, book_id);
        """)


@app.on_event("startup")
def startup():
    init_database()


def current_user(request: Request):
    """
    O Supervisor do Home Assistant envia estes cabeçalhos pelo Ingress.
    Isso separa automaticamente o histórico de cada usuário do HA.
    """
    user_id = (
        request.headers.get("X-Remote-User-ID")
        or request.headers.get("X-Remote-User")
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
            INSERT INTO users (id, name, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name = excluded.name
        """, (user_id, name, now()))

    return {"id": user_id, "name": name}


def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "img", "svg", "iframe", "object"]):
        tag.decompose()

    body = soup.body if soup.body else soup

    cleaned = bleach.clean(
        str(body),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True
    )

    return cleaned


def chapter_title(html: str, fallback: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find(["h1", "h2", "h3", "title"])

    if heading:
        text = heading.get_text(" ", strip=True)
        if text:
            return text[:160]

    return fallback


def get_book(book_id: str):
    with db() as conn:
        book = conn.execute("""
            SELECT * FROM books WHERE id = ?
        """, (book_id,)).fetchone()

    if not book:
        raise HTTPException(status_code=404, detail="Livro não encontrado.")

    return dict(book)


def translate_local(text: str) -> str:
    text = text.strip()

    if not text:
        return ""

    try:
        installed_languages = argostranslate.translate.get_installed_languages()

        french = next(
            (language for language in installed_languages if language.code == "fr"),
            None
        )

        portuguese = next(
            (language for language in installed_languages if language.code == "pt"),
            None
        )

        if not french or not portuguese:
            raise RuntimeError("Modelo francês-português não está instalado.")

        translation = french.get_translation(portuguese)
        return translation.translate(text)

    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=f"Tradutor local indisponível: {str(error)}"
        )


@app.get("/")
def index():
    return FileResponse(APP_DIR / "static" / "index.html")


@app.get("/api/me")
def me(request: Request):
    user = current_user(request)

    return {
        "user": user,
        "show_context_default": SHOW_CONTEXT_DEFAULT
    }


@app.get("/api/books")
def list_books(request: Request):
    user = current_user(request)

    with db() as conn:
        rows = conn.execute("""
            SELECT
                b.*,
                COUNT(c.id) AS total_chapters,
                COALESCE(r.chapter_index, 0) AS current_chapter,
                COALESCE(r.scroll_percent, 0) AS scroll_percent
            FROM books b
            LEFT JOIN chapters c ON c.book_id = b.id
            LEFT JOIN reading_progress r
                ON r.book_id = b.id AND r.user_id = ?
            GROUP BY b.id
            ORDER BY b.created_at DESC
        """, (user["id"],)).fetchall()

    books = []

    for row in rows:
        item = dict(row)
        total = max(item["total_chapters"], 1)

        progress = (
            (item["current_chapter"] + item["scroll_percent"] / 100)
            / total
        ) * 100

        item["progress_percent"] = round(min(progress, 100), 1)
        books.append(item)

    return books


@app.post("/api/books/upload")
async def upload_book(
    request: Request,
    file: UploadFile = File(...)
):
    current_user(request)

    if not file.filename.lower().endswith(".epub"):
        raise HTTPException(
            status_code=400,
            detail="Envie somente arquivos EPUB."
        )

    content = await file.read()

    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"O EPUB excede o limite de {MAX_UPLOAD_MB} MB."
        )

    book_id = str(uuid.uuid4())
    epub_path = BOOKS_DIR / f"{book_id}.epub"

    with open(epub_path, "wb") as target:
        target.write(content)

    try:
        loaded_book = epub.read_epub(str(epub_path))

        metadata_title = loaded_book.get_metadata("DC", "title")
        metadata_author = loaded_book.get_metadata("DC", "creator")
        metadata_language = loaded_book.get_metadata("DC", "language")

        title = (
            metadata_title[0][0]
            if metadata_title
            else Path(file.filename).stem
        )

        author = metadata_author[0][0] if metadata_author else "Autor desconhecido"
        language = metadata_language[0][0] if metadata_language else "fr"

        chapters = []
        chapter_index = 0

        for item in loaded_book.get_items():
            if item.get_type() != ITEM_DOCUMENT:
                continue

            raw_html = item.get_content().decode("utf-8", errors="ignore")
            cleaned_html = clean_html(raw_html)

            soup = BeautifulSoup(cleaned_html, "html.parser")
            plain_text = soup.get_text(" ", strip=True)

            if len(plain_text) < 80:
                continue

            chapters.append({
                "index": chapter_index,
                "title": chapter_title(
                    cleaned_html,
                    f"Capítulo {chapter_index + 1}"
                ),
                "html": cleaned_html,
                "text": plain_text
            })

            chapter_index += 1

        if not chapters:
            raise ValueError("Não encontrei capítulos legíveis nesse EPUB.")

        with db() as conn:
            conn.execute("""
                INSERT INTO books (id, title, author, language, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (book_id, title, author, language, now()))

            for chapter in chapters:
                conn.execute("""
                    INSERT INTO chapters (
                        book_id,
                        chapter_index,
                        title,
                        content_html,
                        plain_text
                    )
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    book_id,
                    chapter["index"],
                    chapter["title"],
                    chapter["html"],
                    chapter["text"]
                ))

        return {
            "id": book_id,
            "title": title,
            "author": author,
            "chapters": len(chapters)
        }

    except Exception as error:
        epub_path.unlink(missing_ok=True)

        raise HTTPException(
            status_code=400,
            detail=f"Não foi possível processar o EPUB: {str(error)}"
        )


@app.get("/api/books/{book_id}")
def book_details(book_id: str, request: Request):
    user = current_user(request)
    book = get_book(book_id)

    with db() as conn:
        chapters = conn.execute("""
            SELECT chapter_index, title
            FROM chapters
            WHERE book_id = ?
            ORDER BY chapter_index
        """, (book_id,)).fetchall()

        progress = conn.execute("""
            SELECT chapter_index, scroll_percent
            FROM reading_progress
            WHERE user_id = ? AND book_id = ?
        """, (user["id"], book_id)).fetchone()

    return {
        "book": book,
        "chapters": [dict(chapter) for chapter in chapters],
        "progress": dict(progress) if progress else {
            "chapter_index": 0,
            "scroll_percent": 0
        }
    }


@app.get("/api/books/{book_id}/chapters/{chapter_index}")
def get_chapter(book_id: str, chapter_index: int, request: Request):
    current_user(request)

    with db() as conn:
        chapter = conn.execute("""
            SELECT chapter_index, title, content_html, plain_text
            FROM chapters
            WHERE book_id = ? AND chapter_index = ?
        """, (book_id, chapter_index)).fetchone()

    if not chapter:
        raise HTTPException(status_code=404, detail="Capítulo não encontrado.")

    return dict(chapter)


@app.post("/api/progress")
async def save_progress(
    request: Request,
    book_id: str = Form(...),
    chapter_index: int = Form(...),
    scroll_percent: float = Form(...)
):
    user = current_user(request)

    scroll_percent = max(0, min(100, scroll_percent))

    with db() as conn:
        conn.execute("""
            INSERT INTO reading_progress (
                user_id, book_id, chapter_index, scroll_percent, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, book_id)
            DO UPDATE SET
                chapter_index = excluded.chapter_index,
                scroll_percent = excluded.scroll_percent,
                updated_at = excluded.updated_at
        """, (
            user["id"],
            book_id,
            chapter_index,
            scroll_percent,
            now()
        ))

    return {"ok": True}


@app.post("/api/translate")
async def translate(
    request: Request,
    phrase_fr: str = Form(...),
    context_fr: str = Form(""),
    with_context: bool = Form(False)
):
    current_user(request)

    phrase_fr = re.sub(r"\s+", " ", phrase_fr).strip()
    context_fr = re.sub(r"\s+", " ", context_fr).strip()

    if not phrase_fr:
        raise HTTPException(status_code=400, detail="Expressão vazia.")

    if len(phrase_fr) > 400:
        raise HTTPException(
            status_code=400,
            detail="A expressão está longa demais."
        )

    phrase_pt = translate_local(phrase_fr)

    context_pt = ""
    if with_context and context_fr and context_fr != phrase_fr:
        context_pt = translate_local(context_fr)

    return {
        "phrase_fr": phrase_fr,
        "translation_pt": phrase_pt,
        "context_fr": context_fr,
        "context_pt": context_pt
    }


@app.get("/api/phrases")
def list_phrases(request: Request, book_id: str | None = None):
    user = current_user(request)

    query = """
        SELECT *
        FROM phrases
        WHERE user_id = ?
    """

    params = [user["id"]]

    if book_id:
        query += " AND book_id = ?"
        params.append(book_id)

    query += " ORDER BY created_at DESC"

    with db() as conn:
        phrases = conn.execute(query, params).fetchall()

    return [dict(phrase) for phrase in phrases]


@app.post("/api/phrases")
async def save_phrase(
    request: Request,
    book_id: str = Form(...),
    chapter_index: int = Form(...),
    phrase_fr: str = Form(...),
    translation_pt: str = Form(...),
    context_fr: str = Form(""),
    context_pt: str = Form("")
):
    user = current_user(request)

    phrase_id = str(uuid.uuid4())

    with db() as conn:
        conn.execute("""
            INSERT INTO phrases (
                id,
                user_id,
                book_id,
                phrase_fr,
                translation_pt,
                context_fr,
                context_pt,
                chapter_index,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            phrase_id,
            user["id"],
            book_id,
            phrase_fr.strip(),
            translation_pt.strip(),
            context_fr.strip(),
            context_pt.strip(),
            chapter_index,
            now()
        ))

    return {"id": phrase_id, "ok": True}


@app.delete("/api/phrases/{phrase_id}")
def delete_phrase(phrase_id: str, request: Request):
    user = current_user(request)

    with db() as conn:
        result = conn.execute("""
            DELETE FROM phrases
            WHERE id = ? AND user_id = ?
        """, (phrase_id, user["id"]))

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Expressão não encontrada.")

    return {"ok": True}


@app.delete("/api/books/{book_id}")
def delete_book(book_id: str, request: Request):
    current_user(request)

    epub_path = BOOKS_DIR / f"{book_id}.epub"

    with db() as conn:
        result = conn.execute("""
            DELETE FROM books WHERE id = ?
        """, (book_id,))

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Livro não encontrado.")

    epub_path.unlink(missing_ok=True)

    return {"ok": True}