#!/usr/bin/env python3
"""
Автопубликация постов в Threads по расписанию из posts.csv.

Возможности:
  - текстовые посты, посты с картинкой, карусели (до 20 картинок через "|");
  - длинный текст (> 500 символов) или текст с разделителем "---"
    автоматически публикуется цепочкой (тред из нескольких постов);
  - публикует только посты, время которых наступило, и помечает их в CSV;
  - продление долгоживущего токена: python threads_poster.py refresh
  - Instagram: строки с instagram=yes дополнительно публикуются в Instagram
    (нужен INSTAGRAM_ACCESS_TOKEN; картинки только JPEG, в карусели до 10 штук)

Переменные окружения:
  THREADS_ACCESS_TOKEN  — долгоживущий токен Threads API
  INSTAGRAM_ACCESS_TOKEN — токен Instagram API (вход через Instagram); без него Instagram пропускается
  THREADS_USER_ID       — ID профиля (необязательно, определится сам)
  POSTS_FILE            — путь к CSV (по умолчанию posts.csv)
  TZ_NAME               — часовой пояс расписания (по умолчанию Asia/Yekaterinburg)
  IMAGES_BASE_URL       — откуда брать картинки, если в CSV указано только имя файла
                          (по умолчанию публичный репозиторий <владелец>/threads-images)

Запуск:
  python threads_poster.py            # опубликовать всё, что пора
  python threads_poster.py --dry-run  # показать, что было бы опубликовано
  python threads_poster.py refresh            # продлить токен Threads ещё на 60 дней
  python threads_poster.py refresh instagram  # продлить токен Instagram
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
FIELDS = ["datetime", "text", "image_urls", "status", "post_id", "error",
          "instagram", "ig_status", "ig_post_id", "ig_error"]
IG_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
IG_API = "https://graph.instagram.com/" + os.environ.get("INSTAGRAM_API_VERSION", "v25.0")
IG_MAX_ITEMS = 10
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
    time.sleep(3)
    while time.time() - start < timeout:
        try:
            st = api("GET", container_id, fields="status,error_message")
        except RuntimeError as e:
            # Свежий контейнер (особенно картинка) может ещё не быть виден API — ждём и повторяем.
            if "does not exist" in str(e) and time.time() - start < 90:
                print(f"  контейнер {container_id} ещё не виден, жду…")
                time.sleep(5)
                continue
            raise
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
    for n, url in enumerate(images[:20], 1):
        try:
            cid = api("POST", f"{uid}/threads", media_type="IMAGE",
                      image_url=url, is_carousel_item="true")["id"]
            wait_ready(cid)
        except RuntimeError as e:
            raise RuntimeError(f"картинка {n} ({url}): {e}") from None
        print(f"  картинка {n}/{len(images[:20])} загружена")
        children.append(cid)
    return api("POST", f"{uid}/threads", media_type="CAROUSEL",
               children=",".join(children), text=text, **extra)["id"]


def publish(uid, container_id):
    wait_ready(container_id)
    return api("POST", f"{uid}/threads_publish", creation_id=container_id)["id"]


# ---------- Instagram ----------

def ig_api(method, path, **params):
    params["access_token"] = IG_TOKEN
    r = requests.request(method, f"{IG_API}/{path}", data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=60)
    data = r.json() if r.content else {}
    if r.status_code >= 400 or "error" in data:
        msg = data.get("error", {}).get("message", r.text)
        raise RuntimeError(f"Instagram API {r.status_code}: {msg}")
    return data


def ig_user_id():
    data = ig_api("GET", "me", fields="user_id,username")
    return str(data.get("user_id") or data["id"])


def ig_wait(container_id, timeout=300):
    """Ждём, пока Instagram скачает и обработает картинку (status_code = FINISHED)."""
    start = time.time()
    time.sleep(5)
    while time.time() - start < timeout:
        try:
            st = ig_api("GET", container_id, fields="status_code")
        except RuntimeError as e:
            if "does not exist" in str(e) and time.time() - start < 90:
                time.sleep(5)
                continue
            raise
        code = st.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"контейнер Instagram {container_id}: {code}")
        time.sleep(10)
    raise RuntimeError(f"контейнер Instagram {container_id} не готов за {timeout} c")


def ig_post(uid, caption, images):
    """Публикует картинку или карусель в Instagram. Возвращает ID публикации."""
    if not images:
        raise RuntimeError("для Instagram нужна хотя бы одна картинка")
    if len(images) > IG_MAX_ITEMS:
        raise RuntimeError(f"в карусели Instagram максимум {IG_MAX_ITEMS} картинок, а здесь {len(images)}")
    bad = [u for u in images if not u.lower().split("?")[0].endswith((".jpg", ".jpeg"))]
    if bad:
        raise RuntimeError(f"Instagram принимает только JPEG: {bad[0]}")
    if len(images) == 1:
        cid = ig_api("POST", f"{uid}/media", image_url=images[0], caption=caption)["id"]
    else:
        children = []
        for n, url in enumerate(images, 1):
            try:
                child = ig_api("POST", f"{uid}/media", image_url=url, is_carousel_item="true")["id"]
                ig_wait(child)
            except RuntimeError as e:
                raise RuntimeError(f"картинка {n} ({url}): {e}") from None
            print(f"  Instagram: картинка {n}/{len(images)} загружена")
            children.append(child)
        cid = ig_api("POST", f"{uid}/media", media_type="CAROUSEL",
                     children=",".join(children), caption=caption)["id"]
    ig_wait(cid)
    return ig_api("POST", f"{uid}/media_publish", creation_id=cid)["id"]


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

def _due(row, now):
    try:
        return bool(row["datetime"].strip()) and parse_dt(row["datetime"]) <= now
    except ValueError as e:
        print(f"✗ {e}")
        return False


def _open(status):
    return status.strip().lower() not in ("published", "skip")


def run(dry_run=False):
    rows = load_posts()
    now = datetime.now(TZ)
    due = [r for r in rows if _due(r, now)]
    th_due = [r for r in due if TOKEN and _open(r["status"])]
    ig_due = [r for r in due if r["instagram"].strip().lower() in ("yes", "да", "1", "true")
              and _open(r["ig_status"])]
    # Instagram не догоняет старые посты (например, накопившиеся до подключения токена):
    # пост, опоздавший больше чем на IG_MAX_DELAY_H часов, помечается skip.
    max_delay = float(os.environ.get("IG_MAX_DELAY_H", "6"))
    fresh = []
    for r in ig_due:
        if (now - parse_dt(r["datetime"])).total_seconds() > max_delay * 3600:
            r["ig_status"], r["ig_error"] = "skip", f"опоздал больше чем на {max_delay:g} ч"
        else:
            fresh.append(r)
    if len(fresh) != len(ig_due) and not dry_run:
        save_posts(rows)
    ig_due = fresh
    if not IG_TOKEN:
        if ig_due:
            print(f"Instagram: нет INSTAGRAM_ACCESS_TOKEN, пропускаю {len(ig_due)} пост(ов)")
        ig_due = []
    if not th_due and not ig_due:
        print("Нет постов к публикации.")
        return
    if dry_run:
        for r in th_due:
            print(f"[dry-run] Threads {r['datetime']} | частей: {len(split_text(r['text']))} | "
                  f"картинок: {len([u for u in r['image_urls'].split('|') if u.strip()])} | {r['text'][:60]!r}")
        for r in ig_due:
            print(f"[dry-run] Instagram {r['datetime']} | {r['image_urls'][:80]}")
        return

    uid = get_user_id() if th_due else None
    for row in th_due:
        images = [image_url(u) for u in row["image_urls"].split("|") if u.strip()]
        try:
            row["post_id"] = post_chain(uid, row["text"], images)
            row["status"], row["error"] = "published", ""
            print(f"✓ Threads {row['datetime']} опубликовано: {row['post_id']}")
        except Exception as e:
            row["status"], row["error"] = "error", str(e)[:300]
            print(f"✗ Threads {row['datetime']} ошибка: {e}")
        save_posts(rows)  # сохраняем после каждого поста, чтобы не задвоить
        time.sleep(5)

    if ig_due:
        try:
            iuid = ig_user_id()
        except Exception as e:
            print(f"✗ Instagram: не удалось получить аккаунт: {e}")
            for row in ig_due:
                row["ig_status"], row["ig_error"] = "error", str(e)[:300]
            save_posts(rows)
            return
        for row in ig_due:
            images = [image_url(u) for u in row["image_urls"].split("|") if u.strip()]
            try:
                row["ig_post_id"] = ig_post(iuid, row["text"], images)
                row["ig_status"], row["ig_error"] = "published", ""
                print(f"✓ Instagram {row['datetime']} опубликовано: {row['ig_post_id']}")
            except Exception as e:
                row["ig_status"], row["ig_error"] = "error", str(e)[:300]
                print(f"✗ Instagram {row['datetime']} ошибка: {e}")
            save_posts(rows)
            time.sleep(5)


def refresh_token(instagram=False):
    if instagram:
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": IG_TOKEN}, timeout=30)
    else:
        r = requests.get("https://graph.threads.net/refresh_access_token",
                         params={"grant_type": "th_refresh_token", "access_token": TOKEN}, timeout=30)
    data = r.json()
    if "access_token" not in data:
        sys.exit(f"Не удалось продлить токен: {data}")
    days = int(data.get("expires_in", 0)) // 86400
    print(f"Новый токен (действует ~{days} дн.):\n{data['access_token']}")


if __name__ == "__main__":
    if not TOKEN and not IG_TOKEN:
        sys.exit("Задайте переменную окружения THREADS_ACCESS_TOKEN и/или INSTAGRAM_ACCESS_TOKEN")
    if len(sys.argv) > 1 and sys.argv[1] == "refresh":
        refresh_token(instagram="instagram" in sys.argv)
    else:
        run(dry_run="--dry-run" in sys.argv)
