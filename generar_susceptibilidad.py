"""Genera la capa de SUSCEPTIBILIDAD ESTRUCTURAL a inundación de la costa norte de Perú (piloto) con datos reales.

Parte del análisis de mapas ponderado de ``GeoAnalisis/geotif.py`` (lluvia 40 % + topografía 30 % + nivel del mar 30 %),
pero reemplaza los ráster SIMULADOS (números aleatorios y rectángulos fijos) por datos reales:

- Topografía: Copernicus DEM GLO-30 (ESA, 30 m), abierto en AWS (sin clave), remuestreado a ~155 m.
    * altura sobre el nivel del mar (más baja = más susceptible),
    * relieve local (altura sobre el punto más bajo en ~3 km: aproxima la altura sobre el cauce, «HAND»),
    * pendiente (el terreno plano acumula agua).
- Influencia del mar y de los estuarios: distancia al mar abierto/estuario (DEM <= 0 m conectado al océano), que solo cuenta
  donde el terreno es bajo (por eso se multiplica por la susceptibilidad por altura).
- La LLUVIA no entra en esta capa: es dinámica y ya está en el modelo de riesgo por cantón (NOAA GFS / CFSv2). Esta capa
  indica DÓNDE se inunda con más facilidad el terreno; el modelo por cantón dice CUÁNDO es probable.

Salidas: ``susceptibilidad_inundacion.tif`` (GeoTIFF, EPSG:4326, clases 1 = baja, 2 = media, 3 = alta; 0 = mar/sin dato),
``susceptibilidad_inundacion.png`` (superposición RGBA para el visor) y ``susceptibilidad_inundacion.json`` (extensión y
estadísticas). Requiere ``rasterio``, ``numpy``, ``scipy`` y ``Pillow``. Tarda ~1-2 minutos (descarga ~30 teselas COG).

    python generar_susceptibilidad.py
"""

import json
import math
import sys

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from PIL import Image
from scipy import ndimage

# Extensión: costa norte de Perú (Tumbes, Piura, Lambayeque y La Libertad)
OESTE, SUR, ESTE, NORTE = -81.4, -9.0, -77.2, -3.3
PASO = 1.0 / 600                              # grados (~185 m): 600 px por tesela de 1° exactos (sin costuras) y < 4096 px por lado
URL_TESELA = ("https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
              "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")

# Pesos (adaptados del script original: topografía y mar a partes iguales de importancia, sin la lluvia)
ALTURA_MAX = 80.0                              # m: la costa peruana es un desierto llano que sube despacio (Ecuador usaba 40 m)
PESO_TOPOGRAFIA, PESO_MAR = 0.65, 0.35
PESO_ALTURA, PESO_RELIEVE, PESO_PENDIENTE = 0.50, 0.30, 0.20
UMBRAL_MEDIA, UMBRAL_ALTA = 0.45, 0.70        # el original usaba 0,40 y 0,70; ver estadísticas en el .json
COLORES = {1: (34, 197, 94, 38), 2: (250, 204, 21, 160), 3: (220, 38, 38, 200)}   # RGBA por clase: la «baja» casi transparente


def leer_dem():
    ancho = int(round((ESTE - OESTE) / PASO))
    alto = int(round((NORTE - SUR) / PASO))
    dem = np.full((alto, ancho), np.nan, np.float32)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES", GDAL_HTTP_MAX_RETRY="4"):
        for lat in range(math.floor(SUR), math.ceil(NORTE)):
            for lon in range(math.floor(OESTE), math.ceil(ESTE)):
                url = URL_TESELA.format(ns="N" if lat >= 0 else "S", lat=abs(lat), ew="E" if lon >= 0 else "W", lon=abs(lon))
                try:
                    with rasterio.open(url) as ds:
                        n = int(round(1 / PASO))                # 600
                        bloque = ds.read(1, out_shape=(n, n), resampling=Resampling.average)
                except Exception:          # tesela inexistente: mar abierto
                    continue
                f0 = int(round((NORTE - (lat + 1)) / PASO)); c0 = int(round((lon - OESTE) / PASO))
                f1, c1 = max(f0, 0), max(c0, 0)
                sub = bloque[f1 - f0: f1 - f0 + (alto - f1), c1 - c0: c1 - c0 + (ancho - c1)]
                h = min(sub.shape[0], alto - f1); w = min(sub.shape[1], ancho - c1)
                dem[f1:f1 + h, c1:c1 + w] = sub[:h, :w]
                print(f"  tesela {lat:+d},{lon:+d} ok", file=sys.stderr)
    return np.nan_to_num(dem, nan=0.0)


def calcular(dem):
    # Mar abierto y estuarios: terreno <= 0 m conectado con el borde del mapa
    bajo = dem <= 0.5
    etiquetas, _ = ndimage.label(bajo)
    borde = np.unique(np.concatenate([etiquetas[0, :], etiquetas[-1, :], etiquetas[:, 0], etiquetas[:, -1]]))
    mar = np.isin(etiquetas, borde[borde > 0])
    tierra = ~mar

    paso_m = PASO * 111_000.0
    gy, gx = np.gradient(dem, paso_m)
    pendiente = np.degrees(np.arctan(np.hypot(gx, gy)))
    relieve = dem - ndimage.minimum_filter(dem, size=21)                 # ~3 km

    s_altura = np.clip((ALTURA_MAX - dem) / (ALTURA_MAX - 3.0), 0, 1)    # <= 3 m = 1; >= ALTURA_MAX = 0
    s_relieve = np.clip((20.0 - relieve) / 20.0, 0, 1)
    s_pend = np.clip(1.0 - pendiente / 6.0, 0, 1)
    topografia = PESO_ALTURA * s_altura + PESO_RELIEVE * s_relieve + PESO_PENDIENTE * s_pend

    dist_km = ndimage.distance_transform_edt(~mar) * paso_m / 1000.0
    s_mar = np.exp(-dist_km / 25.0) * s_altura                           # el mar solo influye donde el terreno es bajo
    indice = np.clip(PESO_TOPOGRAFIA * topografia + PESO_MAR * s_mar, 0, 1)
    clases = np.where(indice >= UMBRAL_ALTA, 3, np.where(indice >= UMBRAL_MEDIA, 2, 1)).astype(np.uint8)
    clases[mar] = 0
    return clases, indice, tierra


def main():
    print("Leyendo el DEM Copernicus GLO-30 …", file=sys.stderr)
    dem = leer_dem()
    clases, indice, tierra = calcular(dem)
    alto, ancho = clases.shape
    transform = from_origin(OESTE, NORTE, PASO, PASO)
    with rasterio.open("susceptibilidad_inundacion.tif", "w", driver="GTiff", height=alto, width=ancho, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=transform, nodata=0, compress="deflate") as dst:
        dst.write(clases, 1)
    rgba = np.zeros((alto, ancho, 4), np.uint8)
    for k, color in COLORES.items():
        rgba[clases == k] = color
    Image.fromarray(rgba, "RGBA").save("susceptibilidad_inundacion.png", optimize=True)
    n_tierra = int(tierra.sum())
    estad = {c: round(100.0 * float((clases == c).sum()) / n_tierra, 1) for c in (1, 2, 3)}
    json.dump({"extension": {"oeste": OESTE, "sur": SUR, "este": ESTE, "norte": NORTE}, "paso_grados": PASO,
               "tamano_px": [ancho, alto], "porcentaje_de_tierra_por_clase": {"baja": estad[1], "media": estad[2], "alta": estad[3]},
               "pesos": {"topografia": PESO_TOPOGRAFIA, "mar": PESO_MAR}, "umbrales": {"media": UMBRAL_MEDIA, "alta": UMBRAL_ALTA},
               "fuente": "Copernicus DEM GLO-30 (ESA) vía AWS Open Data"},
              open("susceptibilidad_inundacion.json", "w"), indent=1, ensure_ascii=False)
    print("Listo. Porcentaje de tierra por clase (baja/media/alta):", estad, file=sys.stderr)


if __name__ == "__main__":
    main()
