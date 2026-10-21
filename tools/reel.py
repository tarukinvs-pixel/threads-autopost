#!/usr/bin/env python3
"""Рилс 1080x1920 из одной карусели carousels.json.
Запуск (из папки, где лежит render.py задания):
  python3 /home/claude/threads-autopost/tools/reel.py carousels.json N reels/NN-slug.mp4
N — номер карусели дня с 1. Стиль и блоки берутся из render.py текущей папки,
сверху — вертикальная раскладка 9:16, анимации и тихая звуковая подложка. Нужен ffmpeg.

Хук — первые 2,4 секунды рилса: крупная фраза на оранжевом фоне, слова появляются по одному.
Его текст берётся из поля "hook" карусели в carousels.json, например
  {"slug": "...", "hook": "Рынок пекарен *−13%*. А «Хлебник» открыл *79* новых", ...}
Слова между *звёздочками* выделяются. До 10 слов, только факты из карусели.
Если поля нет, хук собирается автоматически из обложки."""
import json, re, sys, pathlib, subprocess, tempfile, shutil
sys.path[:0] = [str(pathlib.Path.cwd()), str(pathlib.Path(__file__).parent)]
from render import CSS, BLOCKS, E, LABEL, BRAND, TG, PLANE
from playwright.sync_api import sync_playwright

W, H, FPS = 1080, 1920, 30
DUR = {"hook": 2.4, "cover": 3.2, "data": 3.6, "final": 2.6, "tg": 2.8}

REEL_CSS = """
body{width:1080px;height:1920px}
.s{width:1080px;height:1920px;padding:150px 80px 420px;position:relative}
.prog{position:absolute;left:80px;right:80px;top:90px;display:flex;gap:10px}
.prog i{flex:1;height:8px;border-radius:4px;background:rgba(127,127,127,.28);overflow:hidden;position:relative}
.prog i.done:after,.prog i.now:after{content:"";position:absolute;inset:0;background:var(--acc)}
.prog i.now:after{transform-origin:left;animation:fill var(--d) linear both}
@keyframes fill{from{transform:scaleX(0)}to{transform:scaleX(1)}}
.data h1{font-size:96px;margin-top:70px}
.cover h1{font-size:120px}.cover .num{font-size:270px}
.final h1{font-size:110px}
.foot{font-size:28px}
.a{animation:up .55s cubic-bezier(.2,.8,.2,1) both}
@keyframes up{from{opacity:0;transform:translateY(60px)}to{opacity:1;transform:none}}
.d1{animation-delay:.15s}.d2{animation-delay:.35s}.d3{animation-delay:.55s}.d4{animation-delay:.75s}
.rule{animation:grow .5s .1s cubic-bezier(.2,.8,.2,1) both;transform-origin:left}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
.bar,.track{transform-origin:left;animation:grow .8s cubic-bezier(.2,.8,.2,1) both}
.row:nth-child(2) .bar,.row:nth-child(2) .track{animation-delay:.15s}
.row:nth-child(3) .bar,.row:nth-child(3) .track{animation-delay:.3s}
.row:nth-child(4) .bar,.row:nth-child(4) .track{animation-delay:.45s}
.grid i.on{animation:pop .3s both}
@keyframes pop{from{transform:scale(0)}to{transform:scale(1)}}
.li{animation:up .5s cubic-bezier(.2,.8,.2,1) both}
.li:nth-child(2){animation-delay:.2s}.li:nth-child(3){animation-delay:.4s}.li:nth-child(4){animation-delay:.6s}.li:nth-child(5){animation-delay:.8s}
.card{animation:up .55s cubic-bezier(.2,.8,.2,1) both}.card+.card{animation-delay:.35s}
.cta{animation:up .5s .5s cubic-bezier(.2,.8,.2,1) both}
.hero .num{font-size:240px}
.hook{background:var(--acc);color:#fff;justify-content:center}
.hook .top{color:#fff}.hook .tag:before{background:#fff}
.hook .mid{justify-content:center;gap:56px}
.hk{font-family:"Inter Display","Inter",sans-serif;font-weight:800;font-size:124px;line-height:1.04;letter-spacing:-.02em}
.hk span{display:inline-block;animation:pw .28s cubic-bezier(.2,.9,.3,1.3) both;margin-right:.22em}
.hk span.hi{color:var(--dark)}
@keyframes pw{from{opacity:.0;transform:translateY(40px) scale(.85)}to{opacity:1;transform:none}}
.hk span:first-child{animation-name:pw0}
@keyframes pw0{from{opacity:1;transform:scale(1.15)}to{opacity:1;transform:none}}
.more{font-size:40px;font-weight:700;opacity:.9;animation:up .4s 1.2s both}
"""

def auto_hook(c):
    cover = next((s for s in c["slides"] if s["kind"] == "cover"), None)
    if not cover:
        return ""
    num, title = cover.get("num", ""), cover.get("title", "")
    net = re.search(r"«[^»]+»", title)
    if "рассмотрит" in title and net:
        return f"Помещение *{num}*? Его рассмотрит {net.group(0)}"
    return f"*{num}* — что это значит для собственников помещений"

def hook_html(text):
    out, hi = [], False
    for tok in text.split():
        start = tok.startswith("*")
        word = tok.replace("*", "")
        mark = hi or start
        if tok.count("*") % 2:
            hi = not hi
        if word:
            out.append((word, mark))
    return "".join(f'<span class="{"hi" if h else ""}" style="animation-delay:{i*0.09:.2f}s">{E(w)}</span>'
                   for i, (w, h) in enumerate(out))

def prog(i, n, d):
    return '<div class="prog">' + "".join(
        f'<i class="{"done" if k < i else "now" if k == i else ""}" style="--d:{d}s"></i>' for k in range(n)) + '</div>'

def scene(c, s, i, n, d):
    top = f'<div class="top"><div class="tag">{E(LABEL)}</div></div>'
    p = prog(i, n, d)
    if s["kind"] == "hook":
        body = f'<div class="mid"><div class="hk">{hook_html(s["text"])}</div><div class="more">Смотрите до конца ↓</div></div>'
        return f'<div class="s hook">{p}{top}{body}</div>'
    if s["kind"] == "cover":
        body = (f'<div class="mid"><div class="num a">{E(s["num"])}</div><div class="rule"></div>'
                f'<h1 class="a d2">{E(s["title"])}</h1><div class="sub a d3">{E(s.get("sub",""))}</div></div>')
        return f'<div class="s dark cover">{p}{top}{body}<div class="foot"><span>{E(BRAND)}</span></div></div>'
    if s["kind"] == "final":
        body = f'<div class="mid"><div class="rule"></div><h1 class="a">{E(s["title"])}</h1><div class="cta">{E(s["cta"])}</div></div>'
        return f'<div class="s dark final">{p}{top}{body}<div class="foot"><span>{E(BRAND)}</span></div></div>'
    if s["kind"] == "tg":
        body = (f'<div class="mid"><div class="plane a">{PLANE}</div><h1 class="a d1">{E(TG["title"])}</h1>'
                f'<div class="tgname a d2">{E(TG["name"])}</div><div class="sub a d3">{E(TG["text"])}</div>'
                f'<div class="cta">{E(TG["cta"])}</div></div>')
        return f'<div class="s dark tg">{p}{top}{body}<div class="foot"><span>{E(BRAND)}</span></div></div>'
    blk = BLOCKS[s["block"]["type"]](s["block"])
    txt = f'<div class="txt a d3">{E(s["text"])}</div>' if s.get("text") else ""
    return (f'<div class="s data">{p}{top}<h1 class="a">{E(s["title"])}</h1><div class="mid"><div class="a d1">{blk}</div>{txt}</div>'
            f'<div class="foot"><span>{E(c["geo"])} · Источник: {E(c["source"])}</span></div></div>')

def main(src, idx, out):
    c = json.loads(pathlib.Path(src).read_text(encoding="utf-8"))[idx - 1]
    slides = [s for s in c["slides"] if s["kind"] != "tg"] + [{"kind": "tg"}]
    hook = (c.get("hook") or "").strip() or auto_hook(c)
    if hook:
        slides = [{"kind": "hook", "text": hook}] + slides
    n = len(slides)
    tmp = pathlib.Path(tempfile.mkdtemp())
    k = 0
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": W, "height": H})
        for i, s in enumerate(slides):
            d = DUR[s["kind"]]
            pg.set_content(f'<!doctype html><meta charset="utf-8"><style>{CSS}{REEL_CSS}</style><body>{scene(c, s, i, n, d)}</body>')
            pg.evaluate("document.fonts.ready")
            pg.evaluate("document.getAnimations().forEach(a=>{a.pause();a.currentTime=1e5})")
            pg.evaluate("""()=>{for(const n of document.querySelectorAll('.num')){let f=parseFloat(getComputedStyle(n).fontSize);
                while(n.scrollWidth>n.clientWidth&&f>60){f-=4;n.style.fontSize=f+'px'}}}""")
            for f in range(int(d * FPS)):
                t = f * 1000 / FPS
                pg.evaluate(f"document.getAnimations().forEach(a=>a.currentTime={t})")
                pg.screenshot(path=str(tmp / f"{k:05d}.jpg"), type="jpeg", quality=92)
                k += 1
        br.close()
    total = k / FPS
    # тихая фоновая подложка: мягкий аккорд с плавным входом и выходом
    # в начале — короткий низкий удар под хук, дальше подложка
    audio = (f"aevalsrc='0.5*sin(2*PI*55*t)*exp(-7*t)+0.05*sin(2*PI*220*t)+0.04*sin(2*PI*277.2*t)+0.035*sin(2*PI*329.6*t)"
             f"+0.02*sin(2*PI*440*t)*(0.5+0.5*sin(2*PI*0.25*t))':s=44100:d={total:.2f}")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(tmp / "%05d.jpg"),
                    "-f", "lavfi", "-i", audio,
                    "-af", f"afade=t=out:st={total-1.5:.2f}:d=1.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-crf", "20",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", out], check=True)
    shutil.rmtree(tmp)
    print(f"{out}: {total:.1f} c, {n} сцен, хук: {hook}")

if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), sys.argv[3])
