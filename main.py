#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py
------------------------------------------------------------
Obtiene 'userinfo' y 'posts' desde RapidAPI (instagram120),
normaliza distintos formatos de respuesta (posts reales y/o highlights),
calcula el Índice Heurístico de Riesgo de Soledad (IRS) cuando hay
engagement/fechas, y presenta un INFORME detallado.

Requisitos: requests (Python 3.8+). Sin dependencias extra.
"""

import os
import sys
import math
import re
import time
import argparse
from datetime import datetime, timezone
from collections import Counter, defaultdict

import requests

# =========================
# CONFIGURACIÓN API / CREDENCIALES
# =========================
API_HOST = "instagram120.p.rapidapi.com"
API_KEY = os.getenv("RAPIDAPI_KEY") or "YOUR_RAPIDAPI_KEY_HERE"

HEADERS = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": API_HOST,
    "Content-Type": "application/json",
}

# =========================
# PROGRESS BAR (sin dependencias)
# =========================
class ProgressBar:
    """
    Barra de progreso simple en consola:
      - update(pct, msg) actualiza porcentaje y mensaje (en la misma línea).
      - done(msg) cierra al 100% con mensaje final.
    """
    def __init__(self, width: int = 32):
        self.width = max(10, width)
        self.last_len = 0

    def _render(self, pct: float, msg: str):
        pct = max(0.0, min(100.0, pct))
        filled = int(self.width * pct / 100.0)
        bar = "█" * filled + "░" * (self.width - filled)
        line = f"\r[{bar}] {pct:6.2f}%  {msg}"
        # limpiar resto de caracteres si la línea anterior era más larga
        extra = max(0, self.last_len - len(line))
        sys.stdout.write(line + " " * extra)
        sys.stdout.flush()
        self.last_len = len(line)

    def update(self, pct: float, msg: str = ""):
        self._render(pct, msg)

    def done(self, msg: str = "Completado"):
        self._render(100.0, msg)
        sys.stdout.write("\n")
        sys.stdout.flush()


# =========================
# UTILIDADES
# =========================
MENTION_RE = re.compile(r"@[\w.]+")

def to_dt(ts):
    """Convierte timestamps epoch (s/ms) o ISO8601 a datetime en UTC. Si falla, None."""
    if ts is None:
        return None
    try:
        t = float(ts)
        if t > 10_000_000_000:  # ms
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

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def scale_linear(x, x0, x1):
    if x1 == x0:
        return 50.0
    if x <= x0:
        return 0.0
    if x >= x1:
        return 100.0
    return 100.0 * (x - x0) / (x1 - x0)

# =========================
# NORMALIZADORES (aceptan ambos formatos)
# =========================
def normalize_userinfo_response(raw: dict) -> dict:
    """
    Devuelve:
      {
        'username': str|None, 'full_name': str|None,
        'followers': int, 'following': int, 'bio': str|None,
        'highlights': [ {'id','title','previewUrl'}, ... ]  # si viene 'result'
      }
    """
    # Caso típico con 'data' (si el proveedor lo devuelve)
    data = raw.get("data")
    if isinstance(data, dict):
        return {
            "username": data.get("username"),
            "full_name": data.get("full_name") or data.get("name"),
            "followers": int(
                (data.get("edge_followed_by") or {}).get("count")
                or data.get("followers") or data.get("follower_count") or 0
            ),
            "following": int(
                (data.get("edge_follow") or {}).get("count")
                or data.get("following") or data.get("following_count") or 0
            ),
            "bio": data.get("biography") or data.get("bio"),
            "highlights": [],
        }

    # Caso 'result' con highlights (como ejemplos aportados)
    result = raw.get("result")
    if isinstance(result, list):
        highlights = []
        for r in result:
            cover = (((r.get("cover_media") or {}).get("cropped_image_version") or {}).get("url"))
            highlights.append({
                "id": r.get("id"),
                "title": r.get("title"),
                "previewUrl": cover,
            })
        return {
            "username": (result[0].get("user") or {}).get("username") if result else None,
            "full_name": None,
            "followers": 0,
            "following": 0,
            "bio": None,
            "highlights": highlights,
        }

    # Desconocido
    return {"username": None, "full_name": None, "followers": 0, "following": 0, "bio": None, "highlights": []}

def normalize_posts_response(raw: dict) -> list:
    """
    Devuelve lista de posts con:
      id, caption, likeCount, commentCount, timeStamp, previewUrl, username
    Soporta:
      - raw['data']['items']  → posts “reales” con engagement/fecha
      - raw['result']         → highlights (sin engagement/fecha, se rellenan 0/None)
    """
    # Posts “reales”
    container = raw.get("data", raw)
    items = container.get("items")
    if isinstance(items, list):
        out = []
        for m in items:
            img = None
            iv2 = m.get("image_versions2", {})
            if isinstance(iv2.get("candidates"), list) and iv2["candidates"]:
                img = iv2["candidates"][0].get("url")
            out.append({
                "id": m.get("id") or m.get("pk") or m.get("code"),
                "caption": m.get("caption") or "",
                "likeCount": int(m.get("likeCount", m.get("like_count", 0)) or 0),
                "commentCount": int(m.get("commentCount", m.get("comment_count", 0)) or 0),
                "timeStamp": m.get("timeStamp") or m.get("timestamp") or m.get("taken_at") or m.get("created_time"),
                "previewUrl": img,
                "username": (m.get("user") or {}).get("username"),
            })
        return out

    # Highlights
    result = raw.get("result")
    if isinstance(result, list):
        out = []
        for r in result:
            cover = (((r.get("cover_media") or {}).get("cropped_image_version") or {}).get("url"))
            user = r.get("user") or {}
            out.append({
                "id": r.get("id"),
                "caption": r.get("title") or "",
                "likeCount": 0,
                "commentCount": 0,
                "timeStamp": None,
                "previewUrl": cover,
                "username": user.get("username"),
            })
        return out

    return []

# =========================
# CLIENTE RAPIDAPI (2 PETICIONES + barra progreso)
# =========================
def get_user_info(username: str, pbar: ProgressBar) -> dict:
    """POST /api/instagram/userInfo"""
    url = f"https://{API_HOST}/api/instagram/userInfo"
    payload = {"username": username}
    pbar.update(5, f"Solicitando userInfo de @{username} ...")
    r = requests.post(url, json=payload, headers=HEADERS, timeout=30)
    pbar.update(12, "userInfo recibido, normalizando ...")
    r.raise_for_status()
    return r.json()

def get_user_posts(username: str, max_posts: int, pbar: ProgressBar) -> list:
    """
    POST /api/instagram/posts con paginación 'maxId'.
    Va mostrando progreso en función de posts acumulados.
    """
    url = f"https://{API_HOST}/api/instagram/posts"
    posts_raw = []
    max_id = ""
    collected = 0
    pbar.update(18, f"Descargando posts de @{username} (objetivo {max_posts}) ...")

    # Nota: si el proveedor devuelve 'result' (highlights), no habrá paginación real.
    # Aun así, mantenemos el bucle por compatibilidad.
    while collected < max_posts:
        payload = {"username": username, "maxId": max_id}
        r = requests.post(url, json=payload, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"posts HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()

        # Unificamos extrayendo el “paquete” (data o result)
        container = data.get("data", data)
        items = container.get("items")
        if isinstance(items, list):
            posts_raw.extend(items)
        elif isinstance(data.get("result"), list):
            # highlights: añadimos todo y salimos (no hay engagement/fecha)
            posts_raw.extend(data["result"])
        else:
            break

        collected = len(posts_raw)
        base_progress = 18  # base después de iniciar descarga
        # Progreso dinámico hasta 70%
        dyn = min(52, int((collected / max_posts) * 52))
        pbar.update(base_progress + dyn, f"Posts recogidos: {min(collected, max_posts)}/{max_posts}")

        # siguiente página
        next_max = (
            container.get("nextMaxId")
            or container.get("next_max_id")
            or container.get("maxId")
            or data.get("nextMaxId")
            or data.get("next_max_id")
        )
        if not next_max:
            break
        max_id = next_max

        # Pausa mínima “amable”
        time.sleep(0.15)

    return posts_raw[:max_posts]

# =========================
# CÁLCULO DE SUB-ÍNDICES E IRS
# =========================
def score_interaction(posts):
    n = len(posts)
    if n == 0:
        return 0.0
    sum_c, sum_ratio = 0, 0.0
    for p in posts:
        c = int(p.get("commentCount", 0) or 0)
        l = int(p.get("likeCount", 0) or 0)
        sum_c += c
        sum_ratio += c / max(1, l)
    avg_c = sum_c / n
    avg_ratio = sum_ratio / n
    s_c = scale_linear(avg_c, 0, 30)
    s_r = scale_linear(avg_ratio, 0.0, 0.10)
    return 0.6 * s_c + 0.4 * s_r

def posts_per_week(dts):
    if not dts:
        return 0.0
    first, last = min(dts), max(dts)
    days = (last - first).days + 1
    if days <= 0:
        days = 1
    return len(dts) / (days / 7.0)

def score_regularidad(posts):
    dts = [to_dt(p.get("timeStamp")) for p in posts if p.get("timeStamp")]
    if not dts:
        return 0.0
    p_w = posts_per_week(dts)
    if 1.0 <= p_w <= 3.0:
        return 70.0 + 30.0 * (1.0 - abs(p_w - 2.0))
    return clamp(70.0 - 25.0 * abs(p_w - 2.0), 0.0, 70.0)

def score_reciprocidad(followers, following):
    following = max(1, following)
    ffr = followers / following
    return 100.0 * math.exp(-abs(math.log(ffr)))

def score_menciones(posts):
    n = len(posts)
    if n == 0:
        return 0.0
    total = 0
    for p in posts:
        c = p.get("caption") or ""
        total += len(MENTION_RE.findall(c))
    avg = total / n
    return scale_linear(avg, 0.0, 2.0)

def categorize_irs(irs):
    if irs < 34:
        return "BAJO"
    if irs < 67:
        return "MEDIO"
    return "ALTO"

# =========================
# ESTACIONALIDAD / HORARIOS
# =========================
def seasonality_and_hours(posts, followers):
    followers = max(1, int(followers or 0))
    dow = Counter()
    hod = Counter()
    eng_dow = defaultdict(list)
    eng_hod = defaultdict(list)

    for p in posts:
        dt = to_dt(p.get("timeStamp"))
        if not dt:
            continue
        e = (int(p.get("likeCount", 0) or 0) + int(p.get("commentCount", 0) or 0)) / followers
        d = dt.weekday()
        h = dt.hour
        dow[d] += 1
        hod[h] += 1
        eng_dow[d].append(e)
        eng_hod[h].append(e)

    avg_dow = {k: (sum(v) / len(v)) for k, v in eng_dow.items()}
    avg_hod = {k: (sum(v) / len(v)) for k, v in eng_hod.items()}

    top_days = sorted(avg_dow.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top_hours = sorted(avg_hod.items(), key=lambda kv: kv[1], reverse=True)[:5]

    return {
        "count_by_dow": dict(dow),
        "count_by_hour": dict(hod),
        "eng_by_dow": avg_dow,
        "eng_by_hour": avg_hod,
        "top_days": top_days,
        "top_hours": top_hours,
    }

# =========================
# INFORME
# =========================
def print_report(username: str, profile: dict, posts: list, max_posts_requested: int, pbar: ProgressBar):
    pbar.update(78, "Procesando/normalizando datos ...")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    followers = int(profile.get("followers", 0))
    following = int(profile.get("following", 0))
    full_name = profile.get("full_name") or "-"
    bio = profile.get("bio") or ""
    highlights = profile.get("highlights", [])

    # Detectamos si solo hay highlights (sin engagement/fecha)
    only_highlights = (
        len(posts) > 0 and all(
            (p.get("likeCount", 0) == 0 and p.get("commentCount", 0) == 0 and not p.get("timeStamp"))
            for p in posts
        )
    )

    # Cálculos
    if not only_highlights and posts:
        s1 = score_interaction(posts)
        s2 = score_regularidad(posts)
        s3 = score_reciprocidad(followers, following)
        s4 = score_menciones(posts)
        connectedness = 0.45 * s1 + 0.20 * s2 + 0.20 * s3 + 0.15 * s4
        irs = clamp(100.0 - connectedness, 0.0, 100.0)
        cat = categorize_irs(irs)
    else:
        s1 = s2 = s3 = s4 = connectedness = irs = None
        cat = None

    # Descriptivos
    n = max(1, len(posts))
    sum_likes = sum(int(p.get("likeCount", 0) or 0) for p in posts)
    sum_cmts = sum(int(p.get("commentCount", 0) or 0) for p in posts)
    sum_ratio = sum(
        (int(p.get("commentCount", 0) or 0) / max(1, int(p.get("likeCount", 0) or 0)))
        for p in posts
    ) if not only_highlights else 0.0
    sum_mentions = sum(len(MENTION_RE.findall(p.get("caption") or "")) for p in posts)

    # Estacionalidad/horas (si hay timestamps)
    timing = seasonality_and_hours(posts, followers) if not only_highlights else None

    pbar.update(92, "Generando informe ...")

    # --------- IMPRESIÓN ---------
    print("\n" + "=" * 96)
    print(f"INFORME HEURÍSTICO DE RIESGO DE SOLEDAD — @{username}")
    print("=" * 96)
    print(f"Fecha de generación: {now}")
    print(f"Parámetros: username=@{username}, max_posts={max_posts_requested}")
    print("-" * 96)
    print("Contexto científico (muy breve): el uso intensivo de RRSS se asocia a mayor soledad percibida;")
    print("la interacción activa (comentar/conversar) se vincula a mejor bienestar que el consumo pasivo.")
    print("Este informe es heurístico, no clínico, y NO reemplaza escalas validadas (p. ej., UCLA).")
    print("-" * 96)
    print("PERFIL")
    print(f"  • Nombre completo: {full_name}")
    print(f"  • Seguidores:      {followers:,}".replace(",", "."))
    print(f"  • Seguidos:        {following:,}".replace(",", "."))
    if bio:
        print(f"  • Bio:             {bio[:200]}{'...' if len(bio)>200 else ''}")
    if highlights:
        print(f"  • Highlights detectados: {len(highlights)} (formato de userInfo tipo 'result')")
    print("-" * 96)
    print("DATOS AGREGADOS")
    print(f"  • Publicaciones analizadas: {len(posts)}")
    if not only_highlights:
        print(f"  • Prom. comentarios/post:   {(sum_cmts/n):.2f}")
        print(f"  • Prom. likes/post:         {(sum_likes/n):.2f}")
        print(f"  • Ratio comentarios/likes:  {(sum_ratio/n):.4f}")
    print(f"  • Menciones @ por caption:  {(sum_mentions/n):.2f}")
    print("-" * 96)

    if not only_highlights and posts:
        print("SUB-ÍNDICES (0–100; mayor = mejor conectividad social)")
        print(f"  • S1 Interacción conversacional ....... {s1:5.1f}")
        print(f"  • S2 Regularidad de publicación ....... {s2:5.1f}")
        print(f"  • S3 Reciprocidad de red (FFR≈1) ...... {s3:5.1f}")
        print(f"  • S4 Referencias sociales (menciones) .. {s4:5.1f}")
        print("-" * 96)
        print(f"ConnectednessScore: {connectedness:5.1f} / 100")
        print(f"ÍNDICE DE RIESGO DE SOLEDAD (IRS): {irs:5.1f} / 100  →  {cat}")
        print("Interpretación:")
        if cat == "ALTO":
            print("  • Señales débiles de conversación/reciprocidad o irregularidad marcada.")
            print("    Sugerencia no clínica: promover comentarios significativos, mencionar/ser mencionado y")
            print("    publicar en las franjas con mayor engagement conversacional detectado.")
        elif cat == "MEDIO":
            print("  • Perfil mixto. Refuerza días/horas de mejor respuesta y fomenta diálogo bidireccional.")
        else:
            print("  • Buenas señales de conectividad. Mantén hábitos activos y horarios óptimos detectados.")
        print("-" * 96)
    else:
        print("⚠️  La API ha devuelto datos sin engagement/fecha (probablemente 'highlights').")
        print("    No es posible estimar interacción/temporalidad → el IRS no puede calcularse con este dataset.")
        print("    Aún así, se listan elementos disponibles como referencia.\n")

    # Estacionalidad/horas (si procede)
    if timing:
        dow_names = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        print("ESTACIONALIDAD (por día de semana)")
        for d in range(7):
            c = timing["count_by_dow"].get(d, 0)
            e = timing["eng_by_dow"].get(d, 0.0)
            print(f"  · {dow_names[d]}: posts={c:3d} | engagement_medio={e:.4f}")
        if timing["top_days"]:
            tops = ", ".join([f"{dow_names[d]}({v:.4f})" for d, v in timing["top_days"]])
            print(f"  → Mejores días (engagement): {tops}")
        else:
            print("  → Mejores días (engagement): -")
        print("-" * 96)
        print("ANÁLISIS HORARIO (por hora 0–23)")
        for h in range(24):
            c = timing["count_by_hour"].get(h, 0)
            e = timing["eng_by_hour"].get(h, 0.0)
            print(f"  · {h:02d}h: posts={c:3d} | engagement_medio={e:.4f}")
        if timing["top_hours"]:
            tops = ", ".join([f"{h:02d}h({v:.4f})" for h, v in timing["top_hours"]])
            print(f"  → Mejores horas (engagement): {tops}")
        else:
            print("  → Mejores horas (engagement): -")
        print("-" * 96)

    # Muestra de posts/highlights
    print("MUESTRA (id | fecha UTC | likes | comments | caption/ título)")
    for p in posts[:20]:
        dt = to_dt(p.get("timeStamp"))
        dt_s = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "-"
        caption = (p.get("caption") or "").replace("\n", " ")
        if len(caption) > 90:
            caption = caption[:90] + "..."
        print(f"  - {p.get('id') or '-'} | {dt_s} | ❤ {p.get('likeCount',0)} | 💬 {p.get('commentCount',0)} | {caption}")
    if len(posts) > 20:
        print(f"  … y {len(posts)-20} más.")
    print("=" * 96)

    pbar.done("Informe generado")


# =========================
# CLI / MAIN
# =========================
def main():
    parser = argparse.ArgumentParser(
        description="Informe heurístico de 'Riesgo de Soledad' en Instagram usando RapidAPI instagram120."
    )
    parser.add_argument("--username", required=True, help="Usuario objetivo (con o sin @)")
    parser.add_argument("--max-posts", type=int, default=100, help="Máximo de posts a analizar (por defecto 100)")
    args = parser.parse_args()

    username = args.username.lstrip("@")
    max_posts = max(1, args.max_posts)

    if API_KEY == "YOUR_RAPIDAPI_KEY_HERE":
        print("[ADVERTENCIA] Establece tu RAPIDAPI_KEY como variable de entorno para evitar errores de autenticación.")

    pbar = ProgressBar()

    try:
        pbar.update(0, "Inicializando ...")
        # 1) USER INFO
        raw_info = get_user_info(username, pbar)
        profile = normalize_userinfo_response(raw_info)
        pbar.update(20, "userInfo normalizado")

        # 2) POSTS (con progreso dinámico)
        posts_raw = get_user_posts(username, max_posts, pbar)
        pbar.update(70, "Normalizando posts ...")
        posts = normalize_posts_response({"data": {"items": posts_raw}} if isinstance(posts_raw, list) and posts_raw and isinstance(posts_raw[0], dict) and "caption" in posts_raw[0] else {"result": posts_raw})
        pbar.update(74, f"Posts normalizados: {len(posts)}")

        # 3) INFORME
        print_report(username, profile, posts, max_posts, pbar)

    except Exception as e:
        pbar.update(100, "Error")
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
