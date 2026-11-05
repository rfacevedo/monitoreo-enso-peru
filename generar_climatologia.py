"""Genera ``climatologia.json``: umbrales de lluvia propios de cada distrito.

Por qué: un umbral único (p. ej. 60 mm/día) trata igual a Sechura (zona desértica) y a Tumbes (zona
más lluviosa) y no tiene en cuenta el sesgo de cada producto. Aquí cada pronóstico se compara con el
clima **del propio producto** en ese distrito:

- Diario (NOAA GFS): archivo de pronósticos de Open-Meteo (``historical-forecast-api``), 2022-2025.
  Se guardan, por distrito, la mediana y el percentil 99 de los días lluviosos (>= 1 mm) y de las
  sumas de 3 días (>= 3 mm).
- Mensual (CFSv2): archivo de pronósticos del servidor de CCSR/IRI (2011-hoy). Se guardan, por distrito
  y mes del año, la mediana y el percentil 95 de la lluvia mensual pronosticada (media de miembros).

Uso (tarda ~30 min por la lentitud del servidor de CCSR; repetir una vez al año):

    python generar_climatologia.py

Requiere internet. El resultado (``climatologia.json``) se sube al repositorio junto con la app.
"""

import datetime
import json
import time

import numpy as np
import requests

import fuentes_datos as fd
from modelo_riesgo import CANTONES

SALIDA = "climatologia.json"
INICIO_GFS, FIN_GFS = "2022-01-01", "2025-12-31"


def _reintentar(funcion, intentos=8, espera=6):
    for k in range(intentos):
        try:
            return funcion()
        except Exception:  # noqa: BLE001
            time.sleep(espera * (k + 1))
    raise RuntimeError("Sin respuesta del servicio tras varios intentos")


def estadisticas_diarias(canton):
    def pedir():
        r = requests.get(
            "https://historical-forecast-api.open-meteo.com/v1/forecast",
            params={"latitude": canton["lat"], "longitude": canton["lon"], "start_date": INICIO_GFS,
                    "end_date": FIN_GFS, "daily": "precipitation_sum", "models": "gfs_global",
                    "timezone": "America/Lima"}, timeout=90)
        r.raise_for_status()
        return r.json()["daily"]["precipitation_sum"]

    p = np.array([0.0 if v is None else v for v in _reintentar(pedir)], float)
    s3 = np.convolve(p, np.ones(3), "full")[: len(p)]
    humedos, humedos3 = p[p >= 1.0], s3[s3 >= 3.0]
    return {
        "dia_p50": round(float(np.percentile(humedos, 50)), 2), "dia_p99": round(float(np.percentile(humedos, 99)), 2),
        "d3_p50": round(float(np.percentile(humedos3, 50)), 2), "d3_p99": round(float(np.percentile(humedos3, 99)), 2),
        "dias_analizados": int(len(p)),
    }


def _dods(url):
    return _reintentar(lambda: fd._dods(url), intentos=8, espera=6)


def serie_cfsv2(celdas_cache, canton):
    """Media de miembros [inicio][mes de pronóstico] en mm/día para la celda de 1° del distrito."""
    iy = min(max(round(canton["lat"]) + 9, 0), 6) + 81
    ix = min(max(round(canton["lon"]) + 82, 0), 5) + 278
    if (iy, ix) not in celdas_cache:
        dds = fd._get(fd.URL_CFSV2 + "pr.dds").text
        n_s = int(dds[dds.index("S = ") + 4:].split("]")[0])
        trozos = []
        for a in range(0, n_s, 12):
            b = min(a + 11, n_s - 1)
            arr = np.array(fd._floats(_dods(f"{fd.URL_CFSV2}pr.dods?pr[{a}:{b}][0:31][0:9][{iy}][{ix}]")))
            trozos.append(np.nanmean(arr.reshape(b - a + 1, 32, 10), axis=1))
        celdas_cache[(iy, ix)] = np.vstack(trozos)
    return celdas_cache[(iy, ix)]


def estadisticas_mensuales(serie, objetivos):
    """Por mes del año: mediana y percentil 95 de la lluvia mensual (mm) pronosticada por el CFSv2."""
    base = datetime.date(1960, 1, 1)
    por_mes = {m: [] for m in range(1, 13)}
    for i in range(serie.shape[0]):
        for l in range(serie.shape[1]):
            v = serie[i, l]
            if np.isnan(v):
                continue
            ini = base + datetime.timedelta(days=int(objetivos[i][l]))
            sig = (ini.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
            por_mes[ini.month].append(max(v, 0.0) * (sig - ini).days)
    return {str(m): {"p50": round(float(np.percentile(v, 50)), 1), "p95": round(float(np.percentile(v, 95)), 1), "n": len(v)}
            for m, v in por_mes.items() if v}


def main():
    dds = fd._get(fd.URL_CFSV2 + "pr.dds").text
    n_s = int(dds[dds.index("S = ") + 4:].split("]")[0])
    objetivos = np.array(fd._ints(_dods(f"{fd.URL_CFSV2}pr.dods?target[0:{n_s - 1}][0:9]"))).reshape(n_s, 10)
    celdas, salida = {}, {}
    # El servidor de CCSR tarda ~15 s por bloque: las celdas de 1° distintas se piden en paralelo antes de recorrer los distritos
    from concurrent.futures import ThreadPoolExecutor
    unicas = {}
    for c in CANTONES:
        unicas.setdefault((round(c["lat"]), round(c["lon"])), c)
    print(len(unicas), "celdas de 1° distintas", flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda c: serie_cfsv2(celdas, c), unicas.values()))
    for c in CANTONES:
        print(c["canton"], "…", flush=True)
        est = estadisticas_diarias(c)
        est["mes"] = estadisticas_mensuales(serie_cfsv2(celdas, c), objetivos)
        salida[c["canton"]] = est
    json.dump({
        "generado": datetime.date.today().isoformat(),
        "fuente_diaria": f"NOAA GFS archivado (Open-Meteo historical-forecast) {INICIO_GFS} a {FIN_GFS}",
        "fuente_mensual": "CFSv2 archivado (CCSR/IRI, media de 32 miembros), todos los inicios y plazos 0-9",
        "cantones": salida,
    }, open(SALIDA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Listo:", SALIDA)


if __name__ == "__main__":
    main()
