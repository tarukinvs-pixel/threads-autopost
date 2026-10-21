#!/usr/bin/env python3
"""
Ставит рилсы дня в очередь автопостинга (Instagram Reels).

Запуск:
  python3 enqueue_reels.py --date 2026-10-21 --reels reels --captions captions.json \
      --map "1:13:00,5:20:00" --posts posts.csv --images-repo ../threads-images

  reels/        — файлы NN-slug.mp4 (результат reel.py), NN — номер карусели дня
  --map         — какую карусель в какое время выпустить рилсом: "номер:ЧЧ:ММ" через запятую
  captions.json — те же подписи, что у каруселей; рилс берёт подпись своей карусели
Видео копируется в threads-images/ДАТА/reels/. В Threads рилсы не дублируются (status=skip),
потому что та же тема уже вышла там каруселью; в Instagram они уходят как Reels.
"""
import argparse, json, pathlib, shutil, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from enqueue_carousels import FIELDS, read_posts
import csv

IG_CAPTION_MAX = 2200


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--reels", required=True)
    ap.add_argument("--captions", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--posts", required=True)
    ap.add_argument("--images-repo", required=True)
    a = ap.parse_args()

    caps = json.loads(pathlib.Path(a.captions).read_text(encoding="utf-8"))
    vids = {int(p.name[:2]): p for p in pathlib.Path(a.reels).glob("*.mp4") if p.name[:2].isdigit()}
    plan = []
    for item in a.map.split(","):
        num, t = item.strip().split(":", 1)
        plan.append((int(num), t.strip()))
    posts_path = pathlib.Path(a.posts)
    rows, delim = read_posts(posts_path)
    taken = {r["datetime"].strip() for r in rows}
    dest = pathlib.Path(a.images_repo) / a.date / "reels"
    dest.mkdir(parents=True, exist_ok=True)
    for num, t in plan:
        dt = f"{a.date} {t}"
        if dt in taken:
            sys.exit(f"{dt} уже занято в posts.csv — выберите другое время рилса")
        if num not in vids:
            sys.exit(f"Нет видео для карусели {num} в {a.reels}")
        if not 1 <= num <= len(caps):
            sys.exit(f"Нет подписи для карусели {num}")
        cap = caps[num - 1]
        text = f"{cap['caption'].strip()}\n\n{cap.get('hashtags', '').strip()}".strip()
        if len(text) > IG_CAPTION_MAX:
            sys.exit(f"{dt}: подпись {len(text)} символов, лимит Instagram {IG_CAPTION_MAX}")
        src = vids[num]
        shutil.copy2(src, dest / src.name)
        rows.append({k: "" for k in FIELDS} | {
            "datetime": dt, "text": text, "image_urls": f"{a.date}/reels/{src.name}",
            "status": "skip", "error": "рилс: только Instagram", "instagram": "yes"})
        print(f"{dt} | рилс {src.name} | {src.stat().st_size // 1024} КБ | {len(text)} симв. | Instagram Reels")
    with posts_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, delimiter=delim)
        w.writeheader()
        w.writerows(rows)
    print(f"Добавлено рилсов: {len(plan)}")


if __name__ == "__main__":
    main()
