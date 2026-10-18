#!/usr/bin/env python3
"""
Автопубликация постов в Threads по расписанию из posts.csv.

Возможности:
  - текстовые посты, посты с картинкой, карусели (до 20 картинок через "|");
  - длинный текст (> 500 символов) или текст с разделителем "---"
    автоматически публикуется цепочкой (тред из нескольких постов);
  - публикует только посты, время которых наступило, и помечает их в CSV;
  - продление долгоживущего токена: python threads_poster.py refresh

Переменные окружения:
  THREADS_ACCESS_TOKEN  — долгоживущий токен Threads API (обязательно)
  THREADS_USER_ID       — ID профиля (необязательно, определится сам)
  POSTS_FILE            — путь к CSV (по умолчанию posts.csv)
  TZ_NAME               — часовой пояс расписания (по умолчанию Asia/Yekaterinburg)
  IMAGES_BASE_URL       — откуда брать картинки, если в CSV указано только имя файла
                          (по умолчанию публичный репозиторий <владелец>/threads-images)

Запуск:
  python threads_poster.py            # опубликовать всё, что пора
  python threads_poster.py --dry-run  # показать, что было бы опубликовано
  python threads_poster.py refresh    # продлить токен ещё на 60 дней
"""

import csv
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

API = "https://graph.threads.net/v1.0"
MAX_LEN = 500
TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
POSTS_FILE = os.environ.get("POSTS_FILE", "posts.csv")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Asia/Yekaterinburg"))
FIELDS = ["datetime", "text", "image_urls", "status", "post_id", "error"]
_owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
IMAGES_BASE_URL = os.environ.get("IMAGES_BASE_URL") or (
    f"https://raw.githubusercontent.com/{_owner}/threads-images/main/" if _owner else "")


def image_url(name):
    """Полная ссылка остаётся как есть; просто имя файла превращается в ссылку на threads-images."""
    name = name.strip()
    if name.lower().startswith(("http://", "https://")) or not IMAGES_BASE_URL:
        return name
    return IMAGES_BASE_URL.rstrip("/") + "/" + requests.utils.quote(name.lstrip("/"))


# ---------- API ----------

def api(method, path, **params):
    params["access_token"] = TOKEN
    r = requests.request(method, f"{API}/{path}", data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=60)
    data = r.json() if r.content else {}
    if r.status_code >= 400 or "error" in data:
        msg = data.get("error", {}).get("message", r.text)
        raise RuntimeError(f"Threads API {r.status_code}: {msg}")
    return data


def get_user_id():
    uid = os.environ.get("THREADS_USER_ID", "").strip()
    return uid or api("GET", "me", fields="id,username")["id"]


def wait_ready(container_id, timeout=300):
    """Ждём, пока Threads обработает контейнер (важно для картинок)."""
    start = time.time()
    while time.time() - start < timeout:
        st = api("GET", container_id, fields="status,error_message")
        status = st.get("status")
        if status in ("FINISHED", "PUBLISHED"):
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Контейнер {container_id}: {status} {st.get('error_message', '')}")
        time.sleep(5)
    raise RuntimeError(f"Контейнер {container_id} не готов за {timeout} c")


def create_container(uid, text, images, reply_to=None):
    extra = {"reply_to_id": reply_to} if reply_to else {}
    if not images:
        return api("POST", f"{uid}/threads", media_type="TEXT", text=text, **extra)["id"]
    if len(images) == 1:
        return api("POST", f"{uid}/threads", media_type="IMAGE",
                   image_url=images[0], text=text, **extra)["id"]
    children = []
    for url in images[:20]:
        cid = api("POST", f"{uid}/threads", media_type="IMAGE",
                  image_url=url, is_carousel_item="true")["id"]
        wait_ready(cid)
        children.append(cid)
    return api("POST", f"{uid}/threads", media_type="CAROUSEL",
               children=",".join(children), text=text, **extra)["id"]


def publish(uid, container_id):
    wait_ready(container_id)
    return api("POST", f"{uid}/threads_publish", creation_id=container_id)["id"]


# ---------- Текст ----------

def split_text(text):
    """Делит текст на части ≤ 500 символов: сначала по '---', потом по абзацам/предложениям."""
    parts = [p.strip() for p in re.split(r"\n\s*---\s*\n", text) if p.strip()]
    result = []
    for part in parts:
        while len(part) > MAX_LEN:
            cut = max(part.rfind("\n", 0, MAX_LEN), part.rfind(". ", 0, MAX_LEN) + 1,
                      part.rfind(" ", 0, MAX_LEN))
            cut = cut if cut > MAX_LEN // 2 else MAX_LEN
            result.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            result.append(part)
    return result


def post_chain(uid, text, images):
    """Первый пост (с картинками), остальные — ответами в цепочке. Возвращает ID первого."""
    parts = split_text(text) or [""]
    first_id = prev_id = None
    for i, part in enumerate(parts):
        cid = create_container(uid, part, images if i == 0 else [], reply_to=prev_id)
        prev_id = publish(uid, cid)
        first_id = first_id or prev_id
        if i < len(parts) - 1:
            time.sleep(3)
    return first_id


# ---------- CSV ----------

DELIM = ","


def load_posts():
    """Читает CSV в UTF-8 (с BOM или без), разделитель — запятая или точка с запятой (Excel)."""
    global DELIM
    with open(POSTS_FILE, newline="", encoding="utf-8-sig") as f:
        content = f.read()
    first_line = content.split("\n", 1)[0]
    DELIM = ";" if first_line.count(";") > first_line.count(",") else ","
    rows = list(csv.DictReader(content.splitlines(keepends=True), delimiter=DELIM))
    for row in rows:
        for k in FIELDS:
            row[k] = (row.get(k) or "")
    return rows


def save_posts(rows):
    tmp = POSTS_FILE + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore", delimiter=DELIM)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, POSTS_FILE)


DT_FORMATS = ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S")


def parse_dt(s):
    """Понимает 2026-10-20 10:00 и 20.10.2026 10:00 (так дату сохраняет Excel)."""
    for fmt in DT_FORMATS:
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=TZ)
        except ValueError:
            pass
    raise ValueError(f"Неверный формат даты: '{s}'. Нужно 2026-10-20 10:00")


# ---------- Команды ----------

def run(dry_run=False):
    rows = load_posts()
    now = datetime.now(TZ)
    due = []
    for r in rows:
        if r["status"].strip().lower() in ("published", "skip") or not r["datetime"].strip():
            continue
        try:
            if parse_dt(r["datetime"]) <= now:
                due.append(r)
        except ValueError as e:
            print(f"✗ {e}")
    if not due:
        print("Нет постов к публикации.")
        return
    uid = None if dry_run else get_user_id()
    for row in due:
        images = [image_url(u) for u in row["image_urls"].split("|") if u.strip()]
        preview = row["text"][:60].replace("\n", " ")
        if dry_run:
            print(f"[dry-run] {row['datetime']} | частей: {len(split_text(row['text']))} "
                  f"| картинок: {len(images)} | {preview}…")
            continue
        try:
            row["post_id"] = post_chain(uid, row["text"], images)
            row["status"], row["error"] = "published", ""
            print(f"✓ {row['datetime']} опубликовано: {row['post_id']}")
        except Exception as e:
            row["status"], row["error"] = "error", str(e)[:300]
            print(f"✗ {row['datetime']} ошибка: {e}")
        save_posts(rows)  # сохраняем после каждого поста, чтобы не задвоить
        time.sleep(5)


def refresh_token():
    r = requests.get("https://graph.threads.net/refresh_access_token",
                     params={"grant_type": "th_refresh_token", "access_token": TOKEN}, timeout=30)
    data = r.json()
    if "access_token" not in data:
        sys.exit(f"Не удалось продлить токен: {data}")
    days = int(data.get("expires_in", 0)) // 86400
    print(f"Новый токен (действует ~{days} дн.):\n{data['access_token']}")


if __name__ == "__main__":
    if not TOKEN:
        sys.exit("Задайте переменную окружения THREADS_ACCESS_TOKEN")
    if len(sys.argv) > 1 and sys.argv[1] == "refresh":
        refresh_token()
    else:
        run(dry_run="--dry-run" in sys.argv)
