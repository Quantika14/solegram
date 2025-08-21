#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import time
import argparse
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
from collections import Counter, defaultdict

import requests

API_HOST = "instagram120.p.rapidapi.com"
USERINFO_URL   = f"https://{API_HOST}/api/instagram/userInfo"
POSTS_URL      = f"https://{API_HOST}/api/instagram/posts"
USERPOSTS_URL  = f"https://{API_HOST}/api/instagram/userPosts"  # fallback
LINKS_URL      = f"https://{API_HOST}/api/instagram/links"

# ---------------------------
# Barra de progreso en consola
# ---------------------------
class ProgressBar:
    def __init__(self, total: int = 100, length: int = 40):
        self.total = total
        self.length = length
        self.last_msg = ""

    def update(self, percent: float, msg: str = ""):
        filled = int(self.length * percent // 100)
        bar = "█" * filled + "░" * (self.length - filled)
        sys.stdout.write(f"\r[{bar}] {percent:6.2f}%  {msg[:60].ljust(60)}")
        sys.stdout.flush()
        self.last_msg = msg
        if percent >= 100:
            print()

# ---------------------------
# Utilidades
# ---------------------------
MENTION_RE = re.compile(r"@[\w.]+")

def build_headers(api_key: str, form: bool = False) -> dict:
    return {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": API_HOST,
        "Content-Type": "application/x-www-form-urlencoded" if form else "application/json",
    }

def to_dt(ts: Any) -> Optional[datetime]:
    """Convierte epoch (s/ms) o ISO8601 a datetime UTC."""
    if ts is None:
        return None
    try:
        t = float(ts)
        if t > 10_000_000_000:
            t /= 1000.0
        return datetime.fromtimestamp(t, tz=timezone.utc)
    except Exception:
        pass
    if isinstance(ts, str):
        s = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None
    return None

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def scale_linear(x: float, x0: float, x1: float) -> float:
    if x1 == x0: return 50.0
    if x <= x0: return 0.0
    if x >= x1: return 100.0
    return 100.0 * (x - x0) / (x1 - x0)

def _post_try_json_then_form(url: str, payload: dict, api_key: str, timeout: int = 30) -> requests.Response:
    """Intenta JSON y, si no es 200, reintenta como FORM. Devuelve el último response."""
    r = requests.post(url, json=payload, headers=build_headers(api_key, form=False), timeout=timeout)
    if r.status_code == 200:
        return r
    # Reintenta en FORM para 4xx/5xx típicos del proveedor
    r2 = requests.post(url, data=payload, headers=build_headers(api_key, form=True), timeout=timeout)
    return r2

# ---------------------------
# Normalizadores
# ---------------------------
def normalize_userinfo_response(raw: dict) -> dict:
    # data directo
    data = raw.get("data")
    if isinstance(data, dict):
        user_id = (data.get("id") or data.get("pk") or (data.get("user") or {}).get("id"))
        return {
            "username": data.get("username"),
            "full_name": data.get("full_name") or data.get("name"),
            "followers": int((data.get("edge_followed_by") or {}).get("count") or data.get("follower_count") or data.get("followers") or 0),
            "following": int((data.get("edge_follow") or {}).get("count") or data.get("following_count") or data.get("following") or 0),
            "bio": data.get("biography") or data.get("bio"),
            "user_id": str(user_id) if user_id is not None else None,
        }
    # result[0].user (tu JSON)
    result = raw.get("result")
    if isinstance(result, list) and result and isinstance(result[0], dict) and "user" in result[0]:
        u = result[0].get("user") or {}
        user_id = (u.get("id") or u.get("pk") or u.get("pk_id") or u.get("instagram_pk"))
        return {
            "username": u.get("username"),
            "full_name": u.get("full_name"),
            "followers": int(u.get("follower_count") or 0),
            "following": int(u.get("following_count") or 0),
            "bio": u.get("biography"),
            "user_id": str(user_id) if user_id is not None else None,
        }
    # fallback vacío
    return {"username": None, "full_name": None, "followers": 0, "following": 0, "bio": None, "user_id": None}

def extract_shortcode_url(item: dict, username: str) -> Optional[str]:
    """
    Intenta obtener la URL pública del post a partir de distintos campos:
    - code | shortcode
    - link (algunos proveedores lo traen ya)
    - id con patrón que contenga 'C...'
    """
    # link directo
    link = item.get("link") or item.get("permalink") or item.get("url")
    if isinstance(link, str) and link.startswith("http"):
        return link

    code = item.get("code") or item.get("shortcode")
    if code and isinstance(code, str):
        return f"https://www.instagram.com/p/{code}/"

    # A veces el 'id' lleva el code embebido, prueba a detectar 'Cxxxx' secuencia
    _id = str(item.get("id") or item.get("pk") or "")
    m = re.search(r"(C[A-Za-z0-9_-]{5,})", _id)
    if m:
        return f"https://www.instagram.com/p/{m.group(1)}/"

    # Último recurso (no ideal): construir por username y timestamp no es fiable → None
    return None

def normalize_links_response(raw: dict) -> dict:
    """
    /links devuelve diferentes formatos. Unificamos a:
      { likesCount, commentsCount, timestamp, caption }
    Buscamos en varias claves (data / result / atajos).
    """
    src = raw.get("data", raw.get("result", raw))
    # Likes
    likes = (
        src.get("likesCount") or src.get("likeCount") or src.get("like_count") or
        src.get("edge_media_preview_like", {}).get("count") or 0
    )
    # Comments
    comments = (
        src.get("commentsCount") or src.get("commentCount") or src.get("comment_count") or
        src.get("edge_media_to_comment", {}).get("count") or 0
    )
    # Timestamp
    ts = src.get("timestamp") or src.get("taken_at") or src.get("created_time") or src.get("timeStamp")
    # Caption
    caption = (
        src.get("caption") or
        (src.get("edge_media_to_caption", {}).get("edges", [{}])[0].get("node", {}).get("text") if src.get("edge_media_to_caption") else None) or
        ""
    )
    return {
        "likesCount": int(likes or 0),
        "commentsCount": int(comments or 0),
        "timestamp": ts,
        "caption": caption or ""
    }

# ---------------------------
# Llamadas a API
# ---------------------------
def get_user_info(username: str, api_key: str, pbar: ProgressBar) -> dict:
    pbar.update(5, f"Solicitando userInfo de @{username}")
    r = _post_try_json_then_form(USERINFO_URL, {"username": username}, api_key)
    if r.status_code in (401, 403):
        r.raise_for_status()
    if r.status_code != 200:
        raise RuntimeError(f"userInfo HTTP {r.status_code}: {r.text[:200]}")
    pbar.update(12, "userInfo recibido")
    return r.json()

def _fetch_posts_from(url: str, key: str, value: str, api_key: str, pbar: ProgressBar, max_posts: int) -> List[dict]:
    """
    Descarga posts desde un endpoint concreto (/posts o /userPosts) con
    la clave 'username' o 'userId', sin romper en 500.
    """
    # 1) Primera página sin maxId (evita 422)
    pbar.update(18, f"Descargando listado de posts ({url.split('/')[-1]})")
    r = _post_try_json_then_form(url, {key: value}, api_key)
    if r.status_code == 500 and "link not found" in (r.text or "").lower():
        # este endpoint falla para esta clave → retornar vacío para que el caller pruebe otro
        return []
    if r.status_code != 200:
        return []

    data = r.json()
    container = data.get("data", data)
    items = container.get("items", [])
    collected = list(items)

    # 2) Paginación
    def next_token(d: dict) -> Optional[str]:
        for k in ("nextMaxId", "next_max_id", "maxId", "max_id"):
            if k in d and d[k]:
                return d[k]
        return None

    nxt = next_token(container) or next_token(data)
    base_prog = 22
    while nxt and len(collected) < max_posts:
        pbar.update(min(65, base_prog), f"Paginando ({len(collected)}) …")
        # prueba con maxId y con max_id
        pag_ok = False
        for mf in ("maxId", "max_id"):
            r2 = _post_try_json_then_form(url, {key: value, mf: nxt}, api_key)
            if r2.status_code != 200:
                continue
            d2 = r2.json()
            c2 = d2.get("data", d2)
            items2 = c2.get("items", [])
            if isinstance(items2, list) and items2:
                collected.extend(items2)
                nxt = next_token(c2) or next_token(d2)
                pag_ok = True
                break
        if not pag_ok:
            break
        base_prog = min(65, base_prog + 3)

    return collected[:max_posts]

def get_posts(username: str, api_key: str, max_posts: int, pbar: ProgressBar, user_id: Optional[str] = None) -> List[dict]:
    """
    Intenta primero /posts con userId (si lo tenemos) y luego username.
    Si falla (500 link not found, 4xx,…), hace fallback a /userPosts.
    """
    # A) /posts
    for key, val in (("userId", user_id), ("username", username)):
        if not val:
            continue
        posts = _fetch_posts_from(POSTS_URL, key, val, api_key, pbar, max_posts)
        if posts:
            return posts

    # B) fallback /userPosts
    pbar.update(20, "Fallback a /userPosts …")
    for key, val in (("userId", user_id), ("username", username)):
        if not val:
            continue
        posts = _fetch_posts_from(USERPOSTS_URL, key, val, api_key, pbar, max_posts)
        if posts:
            return posts

    # Si no hay items, devolvemos lista vacía (seguiremos con highlights si luego los hay)
    return []

def get_post_details(post_url: str, api_key: str, pbar: ProgressBar, index: int, total: int) -> Optional[dict]:
    payload = {"url": post_url}
    r = _post_try_json_then_form(LINKS_URL, payload, api_key)
    if r.status_code != 200:
        pbar.update(70 + int((index/ max(1,total)) * 25), f"Falló /links {index}/{total}")
        return None
    data = r.json()
    pbar.update(70 + int((index/ max(1,total)) * 25), f"OK /links {index}/{total}")
    return normalize_links_response(data)

# ---------------------------
# Scoring & análisis
# ---------------------------
def score_interaction(posts: List[dict]) -> float:
    n = len(posts)
    if n == 0: return 0.0
    sum_c, sum_ratio = 0, 0.0
    for p in posts:
        c = int(p.get("commentsCount", 0) or 0)
        l = int(p.get("likesCount", 0) or 0)
        sum_c += c
        sum_ratio += c / max(1, l)
    s_c = scale_linear(sum_c / n, 0, 30)
    s_r = scale_linear(sum_ratio / n, 0.0, 0.10)
    return 0.6 * s_c + 0.4 * s_r

def posts_per_week(dts: List[datetime]) -> float:
    if not dts: return 0.0
    first, last = min(dts), max(dts)
    days = (last - first).days + 1
    if days <= 0: days = 1
    return len(dts) / (days / 7.0)

def score_regularidad(posts: List[dict]) -> float:
    dts = [to_dt(p.get("timestamp")) for p in posts if p.get("timestamp")]
    if not dts: return 0.0
    p_w = posts_per_week(dts)
    if 1.0 <= p_w <= 3.0: return 70.0 + 30.0 * (1.0 - abs(p_w - 2.0))
    return clamp(70.0 - 25.0 * abs(p_w - 2.0), 0.0, 70.0)

def score_menciones(posts: List[dict]) -> float:
    n = len(posts)
    if n == 0: return 0.0
    total = 0
    for p in posts:
        total += len(MENTION_RE.findall(p.get("caption") or ""))
    return scale_linear(total / n, 0.0, 2.0)

def score_reciprocidad(followers: int, following: int) -> float:
    following = max(1, following)
    ffr = followers / following
    # ideal ffr≈1 → penaliza asimetría con log
    import math
    return 100.0 * math.exp(-abs(math.log(ffr)))

def categorize_irs(irs: float) -> str:
    if irs < 34: return "BAJO"
    if irs < 67: return "MEDIO"
    return "ALTO"

def seasonality_and_hours(posts: List[dict], followers: int) -> dict:
    followers = max(1, int(followers or 0))
    from collections import defaultdict, Counter
    dow, hod = Counter(), Counter()
    eng_dow, eng_hod = defaultdict(list), defaultdict(list)
    for p in posts:
        dt = to_dt(p.get("timestamp"))
        if not dt: continue
        e = (int(p.get("likesCount", 0) or 0) + int(p.get("commentsCount", 0) or 0)) / followers
        dow[dt.weekday()] += 1; hod[dt.hour] += 1
        eng_dow[dt.weekday()].append(e); eng_hod[dt.hour].append(e)
    avg_dow = {k: (sum(v)/len(v)) for k, v in eng_dow.items()}
    avg_hod = {k: (sum(v)/len(v)) for k, v in eng_hod.items()}
    top_days = sorted(avg_dow.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top_hours = sorted(avg_hod.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "count_by_dow": dict(dow), "count_by_hour": dict(hod),
        "eng_by_dow": avg_dow, "eng_by_hour": avg_hod,
        "top_days": top_days, "top_hours": top_hours,
    }

# ---------------------------
# Informe
# ---------------------------
def print_report(username: str, profile: dict, posts: List[dict], max_posts_requested: int, pbar: ProgressBar):
    pbar.update(96, "Generando informe …")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    followers = int(profile.get("followers", 0))
    following = int(profile.get("following", 0))
    full_name = profile.get("full_name") or "-"
    bio = profile.get("bio") or ""

    if posts:
        s1 = score_interaction(posts)
        s2 = score_regularidad(posts)
        s3 = score_reciprocidad(followers, following)
        s4 = score_menciones(posts)
        connected = 0.45*s1 + 0.20*s2 + 0.20*s3 + 0.15*s4
        irs = clamp(100.0 - connected, 0.0, 100.0)
        cat = categorize_irs(irs)
    else:
        s1 = s2 = s3 = s4 = connected = irs = None
        cat = None

    n = max(1, len(posts))
    total_likes = sum(int(p.get("likesCount", 0) or 0) for p in posts)
    total_cmts  = sum(int(p.get("commentsCount", 0) or 0) for p in posts)
    ratio = (total_cmts / max(1, total_likes)) if posts else 0.0
    mentions = sum(len(MENTION_RE.findall(p.get("caption") or "")) for p in posts)

    timing = seasonality_and_hours(posts, followers) if posts else None

    print("\n" + "="*96)
    print(f"INFORME HEURÍSTICO DE RIESGO DE SOLEDAD — @{username}")
    print("="*96)
    print(f"Fecha: {now}")
    print(f"Parámetros: username=@{username}, max_posts={max_posts_requested}")
    print("-"*96)
    print("PERFIL")
    print(f"  • Nombre completo: {full_name}")
    print(f"  • Seguidores:      {followers:,}".replace(",", "."))
    print(f"  • Seguidos:        {following:,}".replace(",", "."))
    if bio:
        print(f"  • Bio:             {bio[:200]}{'...' if len(bio)>200 else ''}")
    print("-"*96)
    print("DATOS AGREGADOS")
    print(f"  • Publicaciones analizadas: {len(posts)}")
    if posts:
        print(f"  • Prom. comentarios/post:   {(total_cmts/n):.2f}")
        print(f"  • Prom. likes/post:         {(total_likes/n):.2f}")
        print(f"  • Ratio comentarios/likes:  {ratio:.4f}")
    print(f"  • Menciones @ por caption:  {(mentions/n):.2f}")
    print("-"*96)

    if posts:
        print("SUB-ÍNDICES (0–100; mayor=mejor conectividad)")
        print(f"  • S1 Interacción ............ {s1:5.1f}")
        print(f"  • S2 Regularidad ............ {s2:5.1f}")
        print(f"  • S3 Reciprocidad ........... {s3:5.1f}")
        print(f"  • S4 Menciones .............. {s4:5.1f}")
        print("-"*96)
        print(f"ConnectednessScore: {connected:5.1f}/100")
        print(f"IRS (Riesgo de Soledad): {irs:5.1f}/100 → {cat}")
        if timing:
            dow_names = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
            print("-"*96)
            print("ESTACIONALIDAD (por día de semana)")
            for d in range(7):
                c = timing["count_by_dow"].get(d, 0)
                e = timing["eng_by_dow"].get(d, 0.0)
                print(f"  · {dow_names[d]}: posts={c:3d} | engagement_medio={e:.4f}")
            tops = ", ".join([f"{dow_names[d]}({v:.4f})" for d, v in (timing['top_days'] or [])]) or "-"
            print(f"  → Mejores días: {tops}")
            print("-"*96)
            print("ANÁLISIS HORARIO (0–23h)")
            for h in range(24):
                c = timing["count_by_hour"].get(h, 0)
                e = timing["eng_by_hour"].get(h, 0.0)
                print(f"  · {h:02d}h: posts={c:3d} | engagement_medio={e:.4f}")
            tops_h = ", ".join([f"{h:02d}h({v:.4f})" for h, v in (timing['top_hours'] or [])]) or "-"
            print(f"  → Mejores horas: {tops_h}")
    else:
        print("⚠️  No se pudieron obtener posts con métricas. Revisa permisos/plan del API.")
    print("\nMUESTRA (url | likes | comments | fecha UTC | caption)")
    for p in posts[:10]:
        dt = to_dt(p.get("timestamp")); dt_s = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "-"
        cap = (p.get("caption") or "").replace("\n", " ")
        if len(cap) > 90: cap = cap[:90] + "..."
        print(f"  - {p.get('shortcode_url','-')} | ❤ {p.get('likesCount',0)} | 💬 {p.get('commentsCount',0)} | {dt_s} | {cap}")
    if len(posts) > 10: print(f"  … y {len(posts)-10} más.")
    print("="*96)
    pbar.update(100, "Finalizado")

# ---------------------------
# Main
# ---------------------------
def main():
    parser = argparse.ArgumentParser(description="Analizador de soledad digital en Instagram (RapidAPI instagram120)")
    parser.add_argument("--username", required=True, help="Usuario de Instagram")
    parser.add_argument("--max-posts", type=int, default=12, help="Máximo de posts a analizar")
    parser.add_argument("--api-key", default=os.getenv("RAPIDAPI_KEY"), help="Tu RapidAPI key")
    args = parser.parse_args()

    if not args.api_key:
        print("[ERROR] Debes especificar --api-key o exportar RAPIDAPI_KEY")
        sys.exit(1)

    username = args.username.lstrip("@")
    pbar = ProgressBar()

    # 1) userInfo
    info_raw = get_user_info(username, args.api_key, pbar)
    profile = normalize_userinfo_response(info_raw)

    # 2) posts (listado ligero) con fallback
    posts_list = get_posts(username, args.api_key, args.max_posts, pbar, user_id=profile.get("user_id"))

    # 3) para cada post del listado, obtener URL y consultar /links
    detalles: List[dict] = []
    total = len(posts_list)
    if total == 0:
        pbar.update(70, "Sin items en feed (o error del proveedor)")
    for idx, item in enumerate(posts_list, 1):
        url = extract_shortcode_url(item, username)
        if not url:
            pbar.update(70 + int((idx/max(1,total))*25), f"Sin URL detectable {idx}/{total}")
            continue
        detail = get_post_details(url, args.api_key, pbar, idx, total)
        if detail:
            detail["shortcode_url"] = url
            detalles.append(detail)

    # 4) informe
    print_report(username, profile, detalles, args.max_posts, pbar)

if __name__ == "__main__":
    main()
