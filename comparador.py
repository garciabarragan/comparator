#!/usr/bin/env python3
"""
Comparador de precios de finde (ida + vuelta) entre dos ciudades.

Cada consulta sale "limpia": cliente HTTP nuevo, sin cookies, user-agent e
idioma aleatorios, proxy opcional y orden/espera aleatorios entre consultas,
para que tus propias búsquedas no inflen el precio.

Uso:
  python comparador.py                          # BCN <-> SVQ, findes hasta fin de año
  python comparador.py --sabado --lunes         # permite ida en sábado y vuelta en lunes
  python comparador.py --umbral 60              # avisa si algún finde baja de 60 €
  python comparador.py --capturar vueling       # modo captura (ver README)
"""
import argparse
import asyncio
import json
import os
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

import httpx

DB_PATH = os.getenv("COMPARADOR_DB", "precios.sqlite")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
]
LANGS = ["es-ES,es;q=0.9", "ca-ES,ca;q=0.9,es;q=0.8", "es-ES,es;q=0.9,en;q=0.7"]
VIEWPORTS = [(1366, 768), (1440, 900), (1536, 864), (1920, 1080), (390, 844)]

CAPTURE_SITES = {
    "vueling": "https://www.vueling.com/es",
    "iryo": "https://iryo.eu/es",
    "renfe": "https://www.renfe.com/es/es",
    "ouigo": "https://www.ouigo.com/es",
    "alsa": "https://www.alsa.es",
}


@dataclass
class Fare:
    provider: str
    mode: str  # vuelo / tren / bus
    origin: str
    dest: str
    day: date
    price: float
    dep_time: str | None = None


# ---------- aislamiento ----------

def fresh_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": random.choice(LANGS),
        "Accept": "application/json, text/plain, */*",
    }


def proxy_url() -> str | None:
    # Proxy residencial rotativo, p.ej. http://user:pass@host:port
    return os.getenv("PROXY_URL") or None


async def jitter(a: float = 2.5, b: float = 7.0) -> None:
    await asyncio.sleep(random.uniform(a, b))


# ---------- proveedores ----------

class Ryanair:
    name, mode = "Ryanair", "vuelo"
    URL = "https://www.ryanair.com/api/farfnd/v4/oneWayFares/{o}/{d}/cheapestPerDay"

    async def fares(self, origin: str, dest: str, month: date) -> list[Fare]:
        # Cliente nuevo por consulta: ninguna cookie viaja de una búsqueda a otra.
        async with httpx.AsyncClient(headers=fresh_headers(), proxy=proxy_url(), timeout=25) as c:
            r = await c.get(
                self.URL.format(o=origin, d=dest),
                params={"outboundMonthOfDate": month.strftime("%Y-%m-01"), "currency": "EUR"},
            )
            r.raise_for_status()
            data = r.json()
        out = []
        for f in data.get("outbound", {}).get("fares", []):
            if f.get("unavailable") or not f.get("price"):
                continue
            dep = f.get("departureDate") or ""
            out.append(Fare(self.name, self.mode, origin, dest,
                            date.fromisoformat(f["day"]),
                            float(f["price"]["value"]),
                            dep[11:16] or None))
        return out


PROVIDERS = [Ryanair()]


# ---------- persistencia ----------

def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS fares(
        ts TEXT, provider TEXT, mode TEXT, origin TEXT, dest TEXT,
        day TEXT, price REAL, dep_time TEXT)""")
    return con


def save(con: sqlite3.Connection, fares: list[Fare]) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    con.executemany("INSERT INTO fares VALUES (?,?,?,?,?,?,?,?)",
                    [(ts, f.provider, f.mode, f.origin, f.dest, f.day.isoformat(), f.price, f.dep_time)
                     for f in fares])
    con.commit()


def previous_min(con: sqlite3.Connection, origin: str, day: date) -> float | None:
    row = con.execute(
        "SELECT MIN(price) FROM fares WHERE origin=? AND day=? AND ts < (SELECT MAX(ts) FROM fares)",
        (origin, day.isoformat())).fetchone()
    return row[0] if row else None


# ---------- lógica de findes ----------

def months_between(start: date, end: date) -> list[date]:
    m, out = date(start.year, start.month, 1), []
    while m <= end:
        out.append(m)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


def weekend_combos(fares, origin, dest, start, end, sabado=False, lunes=False):
    best: dict[tuple[str, date], Fare] = {}
    for f in fares:  # el más barato por dirección y día, venga de donde venga (avión/tren/bus)
        k = (f.origin, f.day)
        if k not in best or f.price < best[k].price:
            best[k] = f
    out_offsets = [0] + ([1] if sabado else [])   # viernes (+ sábado)
    back_offsets = [2] + ([3] if lunes else [])   # domingo (+ lunes)
    combos = []
    fri = start + timedelta(days=(4 - start.weekday()) % 7)
    while fri <= end:
        for oo in out_offsets:
            ida = best.get((origin, fri + timedelta(days=oo)))
            if not ida:
                continue
            for bo in back_offsets:
                vuelta = best.get((dest, fri + timedelta(days=bo)))
                if vuelta:
                    combos.append((round(ida.price + vuelta.price, 2), fri, ida, vuelta))
        fri += timedelta(days=7)
    return sorted(combos, key=lambda c: c[0])


def fmt_leg(f: Fare) -> str:
    return f"{f.day:%a %d/%m} {f.dep_time or '--:--'} {f.price:>6.2f}€ {f.provider}"


# ---------- avisos ----------

async def telegram(msg: str) -> None:
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    async with httpx.AsyncClient(timeout=15) as c:
        await c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                     json={"chat_id": chat, "text": msg})


# ---------- modo captura (para añadir Vueling, Iryo, Renfe, Ouigo, Alsa) ----------

def playwright_proxy() -> dict | None:
    url = proxy_url()
    if not url:
        return None
    u = urlparse(url)
    p = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        p.update(username=u.username, password=u.password or "")
    return p


async def capture(site: str) -> None:
    from playwright.async_api import async_playwright

    os.makedirs("capturas", exist_ok=True)
    path = f"capturas/{site}.jsonl"
    fh = open(path, "a", encoding="utf-8")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, proxy=playwright_proxy())
        w, h = random.choice(VIEWPORTS)
        # Contexto nuevo = perfil vacío: sin cookies, sin localStorage, sin historial.
        ctx = await browser.new_context(user_agent=random.choice(USER_AGENTS),
                                        locale="es-ES", viewport={"width": w, "height": h})
        page = await ctx.new_page()

        async def on_response(resp):
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            try:
                body = await resp.json()
            except Exception:
                return
            txt = json.dumps(body).lower()
            if any(k in txt for k in ("price", "precio", "fare", "amount", "importe")):
                fh.write(json.dumps({"url": resp.url, "body": body}, ensure_ascii=False) + "\n")
                fh.flush()
                print("  capturado:", resp.url[:110])

        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        await page.goto(CAPTURE_SITES[site])
        await asyncio.get_running_loop().run_in_executor(
            None, input, "\nHaz la búsqueda en el navegador y pulsa Enter aquí cuando veas los precios... ")
        await browser.close()
    fh.close()
    print(f"\nGuardado en {path}. Pásamelo y convierto ese tráfico en un proveedor más.")


# ---------- main ----------

async def run(a) -> None:
    start = date.fromisoformat(a.desde) if a.desde else date.today() + timedelta(days=1)
    end = date.fromisoformat(a.hasta)
    tasks = [(p, o, d, m) for p in PROVIDERS for m in months_between(start, end)
             for o, d in ((a.origen, a.destino), (a.destino, a.origen))]
    random.shuffle(tasks)  # sin patrón fijo de consultas

    fares: list[Fare] = []
    for i, (p, o, d, m) in enumerate(tasks):
        try:
            got = await p.fares(o, d, m)
            fares += [f for f in got if start <= f.day <= end]
            print(f"[{p.name}] {o}→{d} {m:%Y-%m}: {len(got)} días con precio", file=sys.stderr)
        except Exception as e:
            print(f"[{p.name}] {o}→{d} {m:%Y-%m}: ERROR {e}", file=sys.stderr)
        if i < len(tasks) - 1:
            await jitter()

    if not fares:
        print("No se ha obtenido ningún precio.")
        return

    con = db()
    save(con, fares)
    combos = weekend_combos(fares, a.origen, a.destino, start, end, a.sabado, a.lunes)

    print(f"\nFindes más baratos {a.origen} ⇄ {a.destino} ({start} → {end})\n")
    print(f"{'TOTAL':>8}  {'IDA':<34} {'VUELTA':<34}")
    for total, fri, ida, vuelta in combos[: a.top]:
        drop = ""
        prev = [previous_min(con, ida.origin, ida.day), previous_min(con, vuelta.origin, vuelta.day)]
        if all(prev) and sum(prev) > total:
            drop = f"  ↓ antes {sum(prev):.2f}€"
        print(f"{total:>7.2f}€  {fmt_leg(ida):<34} {fmt_leg(vuelta):<34}{drop}")

    if a.umbral:
        chollos = [c for c in combos if c[0] <= a.umbral]
        if chollos:
            lines = [f"{t:.0f}€ · ida {fmt_leg(i)} · vuelta {fmt_leg(v)}" for t, _, i, v in chollos[:5]]
            msg = f"✈️ {a.origen}⇄{a.destino} por debajo de {a.umbral:.0f}€:\n" + "\n".join(lines)
            print("\n" + msg)
            await telegram(msg)


def main() -> None:
    ap = argparse.ArgumentParser(description="Comparador de findes sin contaminar la búsqueda")
    ap.add_argument("--origen", default="BCN")
    ap.add_argument("--destino", default="SVQ")
    ap.add_argument("--desde", help="YYYY-MM-DD (por defecto mañana)")
    ap.add_argument("--hasta", default=f"{date.today().year}-12-31")
    ap.add_argument("--sabado", action="store_true", help="permitir ida en sábado")
    ap.add_argument("--lunes", action="store_true", help="permitir vuelta en lunes")
    ap.add_argument("--umbral", type=float, help="avisar si el total baja de X €")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--capturar", choices=sorted(CAPTURE_SITES), help="modo captura de tráfico")
    a = ap.parse_args()
    asyncio.run(capture(a.capturar) if a.capturar else run(a))


if __name__ == "__main__":
    main()
