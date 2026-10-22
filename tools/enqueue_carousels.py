#!/usr/bin/env python3
"""
Ставит готовые карусели дня в очередь автопостинга Threads.

Копирует PNG-карточки в публичный репозиторий картинок и дописывает строки в posts.csv.
Git-операции (commit/push) выполняет вызывающий: сначала репозиторий картинок, потом posts.csv.

Запуск:
  python3 enqueue_carousels.py --date 2026-10-19 --out out --captions captions.json \
      --posts threads-autopost/posts.csv --images-repo threads-images

  out/           — папки 01-slug, 02-slug, … с PNG (результат render.py), по порядку публикации
  captions.json  — массив по порядку публикации: [{"caption": "...", "hashtags": "#a #b"}, ...]
  --times        — время слотов через запятую (по умолчанию 8 слотов дня: 5 новостей + 3 заявки)
  --instagram    — слоты, которые также публикуются в Instagram (по умолчанию все 8 слотов);
                   пустая строка — ничего в Instagram
  --stories      — слоты, для которых делается сторис в Instagram (по умолчанию заявки: 12:00,16:00,21:00)
  --articles     — articles.json: массив той же длины, по порядку публикации; для новости
                   {"title": "...", "text": "абзацы через пустую строку"}, для заявки null.
                   Статьи сохраняются в articles/ДАТА/ рядом с posts.csv и уходят в Telegram → Дзен
Карточки сохраняются в JPEG (качество 92): Instagram принимает только JPEG, Threads — тоже.
"""
import argparse
import csv
import json
import pathlib
import shutil
import sys

DEFAULT_TIMES = "09:00,11:00,12:00,14:00,16:00,18:00,19:00,21:00"
FIELDS = ["datetime", "text", "image_urls", "status", "post_id", "error",
          "instagram", "ig_status", "ig_post_id", "ig_error",
          "ig_story", "ig_story_status", "ig_story_id",
          "tg_article", "tg_status", "tg_post_id", "tg_error"]
TG_MAX = 2900   # до 3000 знаков Дзен делает один пост; длиннее — может разбить на несколько
STORY_W, STORY_H = 1080, 1920
STORY_BG = (20, 32, 43)        # тёмный фон карточек
STORY_ACC = (217, 89, 38)      # фирменный оранжевый
STORY_TEXT = ("Telegram @tarukin_deals", "Аренда и продажа торговых помещений Уфы")


def _font(size, bold=True):
    from PIL import ImageFont
    import glob
    pats = (["/usr/share/fonts/**/Inter-Bold.*", "/usr/share/fonts/**/DejaVuSans-Bold.ttf"] if bold
            else ["/usr/share/fonts/**/Inter-Medium.*", "/usr/share/fonts/**/DejaVuSans.ttf"])
    for pat in pats:
        for f in glob.glob(pat, recursive=True):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                pass
    return ImageFont.load_default(size)


def make_story(cover_png, out_jpg):
    """Сторис 1080x1920: обложка карусели 4:5 + надпись про Telegram."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (STORY_W, STORY_H), STORY_BG)
    cover = Image.open(cover_png).convert("RGB").resize((1080, 1350))
    top = 150
    img.paste(cover, (0, top))
    d = ImageDraw.Draw(img)
    # Убираем «1 / N» и «Листайте →» с обложки: в сторис листать нечего.
    # Заливаем цветом фона обложки (у заявок обложка оранжевая)
    bg = cover.getpixel((20, 20))
    d.rectangle([760, top + 50, 1010, top + 130], fill=bg)
    d.rectangle([700, top + 1260, 1010, top + 1340], fill=bg)
    y = top + 1350 + 70
    d.rectangle([80, y, 220, y + 14], fill=STORY_ACC)
    d.text((80, y + 44), STORY_TEXT[0], font=_font(64), fill=(255, 255, 255))
    d.text((80, y + 132), STORY_TEXT[1], font=_font(38, bold=False), fill=(185, 192, 199))
    img.save(out_jpg, "JPEG", quality=92, optimize=True)
DEFAULT_IG = "09:00,11:00,12:00,14:00,16:00,18:00,19:00,21:00"
DEFAULT_STORIES = "12:00,16:00,21:00"
MAX_LEN = 500
MAX_IMAGES = 20


def read_posts(path):
    if not path.exists():
        return [], ","
    content = path.read_text(encoding="utf-8-sig")
    first = content.split("\n", 1)[0]
    delim = ";" if first.count(";") > first.count(",") else ","
    rows = list(csv.DictReader(content.splitlines(keepends=True), delimiter=delim))
    return [{k: (r.get(k) or "") for k in FIELDS} for r in rows], delim


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--captions", required=True)
    ap.add_argument("--posts", required=True)
    ap.add_argument("--images-repo", required=True)
    ap.add_argument("--times", default=DEFAULT_TIMES)
    ap.add_argument("--instagram", default=DEFAULT_IG)
    ap.add_argument("--stories", default=DEFAULT_STORIES)
    ap.add_argument("--keep-days", type=int, default=3, help="сколько дней хранить картинки")
    ap.add_argument("--articles", default="", help="articles.json со статьями для Telegram/Дзена")
    a = ap.parse_args()

    times = [t.strip() for t in a.times.split(",") if t.strip()]
    ig_slots = {t.strip() for t in a.instagram.split(",") if t.strip()}
    story_slots = {t.strip() for t in a.stories.split(",") if t.strip()} & ig_slots
    from PIL import Image
    dirs = sorted(d for d in pathlib.Path(a.out).iterdir() if d.is_dir())
    caps = json.loads(pathlib.Path(a.captions).read_text(encoding="utf-8"))
    if not (len(dirs) == len(caps) == len(times)):
        sys.exit(f"Не совпадает число каруселей ({len(dirs)}), подписей ({len(caps)}) и слотов ({len(times)})")

    arts = json.loads(pathlib.Path(a.articles).read_text(encoding="utf-8")) if a.articles else [None] * len(times)
    if len(arts) != len(times):
        sys.exit(f"В articles.json {len(arts)} элементов, а слотов {len(times)} (для заявок ставьте null)")
    posts_path = pathlib.Path(a.posts)
    art_root = posts_path.parent / "articles"
    rows, delim = read_posts(posts_path)
    taken = {r["datetime"].strip() for r in rows}

    img_root = pathlib.Path(a.images_repo)
    added = []
    for d, cap, t, art in zip(dirs, caps, times, arts):
        dt = f"{a.date} {t}"
        if dt in taken:
            print(f"! {dt} уже есть в posts.csv — пропускаю {d.name}")
            continue
        pngs = sorted(list(d.glob("*.png")) + list(d.glob("*.jpg")))
        if not pngs:
            sys.exit(f"В {d} нет PNG")
        if len(pngs) > MAX_IMAGES:
            sys.exit(f"В {d} больше {MAX_IMAGES} карточек")
        dest = img_root / a.date / d.name
        dest.mkdir(parents=True, exist_ok=True)
        rel = []
        for p in pngs:
            jpg = dest / (p.stem + ".jpg")
            Image.open(p).convert("RGB").save(jpg, "JPEG", quality=92, optimize=True)
            rel.append(f"{a.date}/{d.name}/{jpg.name}")
        story_rel = ""
        if t in story_slots:
            story = dest / f"story-{d.name}.jpg"
            make_story(pngs[0], story)
            story_rel = f"{a.date}/{d.name}/{story.name}"
        caption = cap["caption"].strip() if isinstance(cap, dict) else str(cap).strip()
        tags = cap.get("hashtags", "").strip() if isinstance(cap, dict) else ""
        text = f"{caption}\n\n{tags}" if tags else caption
        if len(text) > MAX_LEN:
            sys.exit(f"{dt}: текст {len(text)} символов, лимит Threads {MAX_LEN}")
        art_rel = ""
        if art:
            title = " ".join(art["title"].split())
            body = art["text"].strip()
            full = f"{title}\n\n{body}\n"
            if len(title) > 140:
                sys.exit(f"{dt}: заголовок статьи {len(title)} символов, в Дзене максимум 140")
            if len(full) > TG_MAX:
                sys.exit(f"{dt}: статья {len(full)} символов, максимум {TG_MAX}")
            (art_root / a.date).mkdir(parents=True, exist_ok=True)
            art_file = art_root / a.date / f"{d.name}.txt"
            art_file.write_text(full, encoding="utf-8")
            art_rel = f"articles/{a.date}/{art_file.name}"
        rows.append({"datetime": dt, "text": text, "image_urls": "|".join(rel),
                     "status": "", "post_id": "", "error": "",
                     "instagram": "yes" if t in ig_slots else "",
                     "ig_status": "", "ig_post_id": "", "ig_error": "",
                     "ig_story": story_rel, "ig_story_status": "", "ig_story_id": "",
                     "tg_article": art_rel, "tg_status": "", "tg_post_id": "", "tg_error": ""})
        ig = (" | + Instagram" if t in ig_slots else "") + (" + сторис" if t in story_slots else "") + (f" | + статья {len(full)} симв." if art else "")
        if t in ig_slots and len(rel) > 10:
            sys.exit(f"{dt}: для Instagram максимум 10 карточек, а здесь {len(rel)}")
        added.append(f"{dt} | {d.name} | {len(rel)} карточек | {len(text)} симв.{ig}")

    # Threads копирует картинку в момент публикации, поэтому старые папки можно удалять:
    # репозиторий картинок не разрастается.
    from datetime import date, timedelta
    cutoff = (date.fromisoformat(a.date) - timedelta(days=a.keep_days)).isoformat()
    for old in img_root.iterdir():
        if old.is_dir() and len(old.name) == 10 and old.name[4] == "-" and old.name < cutoff:
            shutil.rmtree(old)
            print(f"Удалена старая папка картинок: {old.name}")
    art_cut = (date.fromisoformat(a.date) - timedelta(days=7)).isoformat()
    if art_root.exists():
        for old in art_root.iterdir():
            if old.is_dir() and old.name < art_cut:
                shutil.rmtree(old)

    with open(posts_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, delimiter=delim)
        w.writeheader()
        w.writerows(rows)
    print(f"Добавлено в очередь: {len(added)}")
    print(*added, sep="\n")


if __name__ == "__main__":
    main()
