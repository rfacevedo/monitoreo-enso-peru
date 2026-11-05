"""Genera los datos geográficos del piloto de Perú a partir de las capas oficiales del INEI.

Entradas (se descargan una vez):
- ``DISTRITO.gpkg``: límites distritales del INEI (IDE INEI, actualizados al 2023), https://ide.inei.gob.pe/files/Distrito.rar
- geoBoundaries gbOpen PER ADM0 (contorno nacional simplificado, CC0), https://www.geoboundaries.org/

Salidas:
- ``distritos_norte.py``: los distritos de Tumbes, Piura, Lambayeque y La Libertad para el buscador (contorno simplificado).
- ``peru_perfil.py``: el contorno de Perú siempre visible en el visor.
- Imprime el catálogo de distritos del modelo (con elevación del centroide, de Open-Meteo / Copernicus DEM).

    python generar_distritos.py _datos/DISTRITO.gpkg
"""

import json
import math
import sqlite3
import struct
import sys

import requests

DEPARTAMENTOS = ("TUMBES", "PIURA", "LAMBAYEQUE", "LA LIBERTAD")
TOLERANCIA = 0.004          # grados (~450 m)
ISLOTE_MIN = 0.02           # se descartan polígonos con una extensión menor a 0,02°


# ---------------------------------------------------------------- geometría
def leer_geometria(blob):
    """Anillos exteriores de un MultiPolygon/Polygon GeoPackage: lista de polígonos, cada uno lista de [lon, lat]."""
    flags = blob[3]
    env = (flags >> 1) & 7
    off = 8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]
    return _wkb(blob, off)[0]


def _wkb(b, o):
    en = "<" if b[o] == 1 else ">"
    tipo = struct.unpack_from(en + "I", b, o + 1)[0] % 1000
    o += 5
    if tipo == 3:
        return _poligono(b, o, en)
    if tipo == 6:
        n = struct.unpack_from(en + "I", b, o)[0]
        o += 4
        polis = []
        for _ in range(n):
            ps, o = _wkb(b, o)
            polis += ps
        return polis, o
    raise ValueError(f"Geometría no soportada: {tipo}")


def _poligono(b, o, en):
    nr = struct.unpack_from(en + "I", b, o)[0]
    o += 4
    exterior = None
    for k in range(nr):
        n = struct.unpack_from(en + "I", b, o)[0]
        o += 4
        pts = [list(struct.unpack_from(en + "dd", b, o + 16 * i)) for i in range(n)]
        o += 16 * n
        if k == 0:
            exterior = pts
    return [exterior], o


def area(anillo):
    return sum(anillo[i][0] * anillo[i + 1][1] - anillo[i + 1][0] * anillo[i][1] for i in range(len(anillo) - 1)) / 2


def centroide(anillo):
    a = area(anillo)
    if abs(a) < 1e-12:
        return [sum(p[0] for p in anillo) / len(anillo), sum(p[1] for p in anillo) / len(anillo)]
    cx = cy = 0.0
    for i in range(len(anillo) - 1):
        f = anillo[i][0] * anillo[i + 1][1] - anillo[i + 1][0] * anillo[i][1]
        cx += (anillo[i][0] + anillo[i + 1][0]) * f
        cy += (anillo[i][1] + anillo[i + 1][1]) * f
    return [cx / (6 * a), cy / (6 * a)]


def douglas_peucker(pts, tol):
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    norma = math.hypot(dx, dy)
    mejor, idx = -1.0, 0
    for i in range(1, len(pts) - 1):
        d = (math.hypot(pts[i][0] - x1, pts[i][1] - y1) if norma == 0
             else abs(dy * pts[i][0] - dx * pts[i][1] + x2 * y1 - y2 * x1) / norma)
        if d > mejor:
            mejor, idx = d, i
    if mejor <= tol:
        return [pts[0], pts[-1]]
    return douglas_peucker(pts[: idx + 1], tol)[:-1] + douglas_peucker(pts[idx:], tol)


def simplificar(anillo, tol=TOLERANCIA):
    s = douglas_peucker(anillo, tol)
    if s[0] != s[-1]:
        s.append(s[0])
    # anillo plano [lon, lat, lon, lat, ...] y sin repetir el primer punto (el visor cierra el contorno)
    return [round(v, 4) for x, y in s[:-1] for v in (x, y)] if len(s) >= 4 else None


def titulo(txt):
    menores = {"de", "del", "la", "las", "los", "el", "y"}
    p = txt.lower().split()
    return " ".join(w if (i and w in menores) else w.capitalize() for i, w in enumerate(p))


# ---------------------------------------------------------------- salidas
def distritos(gpkg):
    con = sqlite3.connect(gpkg)
    cond = ",".join("?" * len(DEPARTAMENTOS))
    filas = con.execute(
        f"SELECT ubigeo, nombdist, nombprov, nombdep, geom FROM DISTRITO WHERE nombdep IN ({cond}) ORDER BY ubigeo", DEPARTAMENTOS).fetchall()
    salida = []
    for ubigeo, dist, prov, dep, blob in filas:
        polis = leer_geometria(blob)
        grande = max(polis, key=lambda a: abs(area(a)))
        anillos = []
        for a in polis:
            xs, ys = [p[0] for p in a], [p[1] for p in a]
            if max(max(xs) - min(xs), max(ys) - min(ys)) < ISLOTE_MIN and a is not grande:
                continue
            s = simplificar(a)
            if s:
                anillos.append(s)
        todos = [p for a in polis for p in a]
        xs, ys = [p[0] for p in todos], [p[1] for p in todos]
        salida.append({"u": ubigeo, "n": titulo(dist), "p": f"{titulo(prov)}, {titulo(dep)}",
                       "c": [round(v, 4) for v in centroide(grande)],
                       "b": [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)], "r": anillos})
    return salida


def perfil_peru():
    api = requests.get("https://www.geoboundaries.org/api/current/gbOpen/PER/ADM0/", timeout=60).json()
    gj = requests.get(api["simplifiedGeometryGeoJSON"], timeout=120).json()
    anillos = []
    for f in gj["features"]:
        g = f["geometry"]
        polis = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for p in polis:
            a = [[x, y] for x, y in p[0]]
            xs, ys = [q[0] for q in a], [q[1] for q in a]
            if max(max(xs) - min(xs), max(ys) - min(ys)) < 0.03:
                continue
            s = simplificar(a, 0.01)
            if s:
                anillos.append(s)
    return anillos


def elevaciones(puntos):
    r = requests.get("https://api.open-meteo.com/v1/elevation", timeout=60,
                     params={"latitude": ",".join(str(p[1]) for p in puntos), "longitude": ",".join(str(p[0]) for p in puntos)})
    r.raise_for_status()
    return r.json()["elevation"]


def main(gpkg):
    d = distritos(gpkg)
    with open("distritos_norte.py", "w", encoding="utf-8") as f:
        f.write('"""Distritos de Tumbes, Piura, Lambayeque y La Libertad para el buscador «Buscar Distrito».\n\n'
                "Fuente: INEI, Infraestructura de Datos Espaciales (IDE INEI), límite distrital actualizado al 2023,\n"
                "https://ide.inei.gob.pe/ . Contornos simplificados (Douglas-Peucker, 0,004°, ~450 m); islotes menores a 0,02° descartados.\n"
                'Campos: u = ubigeo, n = distrito, p = provincia y departamento, c = centro [lon, lat], b = caja [oeste, sur, este, norte], r = contorno.\n"""\n\n')
        f.write("DISTRITOS_NORTE = " + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n")
    p = perfil_peru()
    with open("peru_perfil.py", "w", encoding="utf-8") as f:
        f.write('"""Perfil (límite nacional) de Perú. Fuente: geoBoundaries gbOpen PER ADM0 (simplificado), licencia CC0 1.0,\n'
                'https://www.geoboundaries.org/ . Simplificado con Douglas-Peucker (0,01°); islotes menores a 0,03° descartados."""\n\n')
        f.write("PERU_PERFIL = " + json.dumps(p, separators=(",", ":")) + "\n")
    print(len(d), "distritos;", sum(len(x["r"]) for x in d), "anillos;", len(p), "anillos en el perfil")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "_datos/DISTRITO.gpkg")
