"""Genera la capa mensual de anomalía de SST (ERA5, Copernicus) para el visor: ``era5_sst_anom.json``.

Entrada: ``_datos/era5_sst_mensual.nc`` (python descargar_era5_sst.py, requiere la clave del CDS en ~/.cdsapirc).
Anomalía = SST del mes menos su promedio 1991-2020. Un PNG de paleta por mes (últimos 24), incrustable como data URI.

    python generar_era5_capa.py
"""
import base64
import io
import json

import numpy as np
import xarray as xr
from PIL import Image

MESES = 24
TOPE = 4.0                      # °C: escala de color de -4 a +4
ds = xr.open_dataset("_datos/era5_sst_mensual.nc")
tn = "valid_time" if "valid_time" in ds.dims else "time"
sst = ds["sst"] - 273.15
if "expver" in sst.dims:
    sst = sst.mean("expver", skipna=True)
sst = sst.rename({tn: "t"}).sortby("t")
clim = sst.sel(t=slice("1991-01-01", "2020-12-31")).groupby("t.month").mean()
anom = sst.groupby("t.month") - clim
anom = anom.sortby("latitude", ascending=False)             # norte arriba
lat, lon = anom["latitude"].values, anom["longitude"].values
rect = [max(-180.0, float(lon.min()) - 0.125), float(lat.min()) - 0.125, float(lon.max()) + 0.125, float(lat.max()) + 0.125]

def color(a):
    """Azul (frío) - blanco - rojo (cálido); transparente en tierra y casi transparente cerca de 0."""
    rgba = np.zeros(a.shape + (4,), np.uint8)
    mar = ~np.isnan(a)
    a = np.nan_to_num(a)
    v = np.clip(a / TOPE, -1, 1)
    fr = np.abs(v)
    cal = v >= 0
    rgba[..., 0] = np.where(cal, 255, 255 * (1 - fr) + 37 * fr)
    rgba[..., 1] = np.where(cal, 255 * (1 - fr) + 38 * fr, 255 * (1 - fr) + 99 * fr)
    rgba[..., 2] = np.where(cal, 255 * (1 - fr) + 38 * fr, 255 * (1 - fr) + 235 * fr)
    rgba[..., 3] = np.clip(60 + 170 * fr, 0, 230)
    rgba[~mar] = 0
    return rgba

def box(a, n, s, w, e):
    return float(a.sel(latitude=slice(n, s), longitude=slice(w, e)).mean())

ultimos = anom.t.values[-MESES:]
meses, pngs, idx = [], [], {}
for t in ultimos:
    a = anom.sel(t=t)
    img = Image.fromarray(color(a.values), "RGBA").quantize(colors=64, method=Image.FASTOCTREE)
    b = io.BytesIO(); img.save(b, "PNG", optimize=True)
    m = str(t)[:7]
    meses.append(m)
    pngs.append("data:image/png;base64," + base64.b64encode(b.getvalue()).decode("ascii"))
    idx[m] = {"n34": round(box(a, 5, -5, -170, -120), 2), "n12": round(box(a, 0, -10, -90, -80), 2)}
json.dump({"rect": rect, "tope": TOPE, "meses": meses, "png": pngs, "indices": idx,
           "fuente": "ERA5 monthly averaged data on single levels (Copernicus Climate Change Service), anomalía frente a 1991-2020"},
          open("era5_sst_anom.json", "w"), separators=(",", ":"))
print(len(meses), "meses", meses[0], "a", meses[-1], "| rect", rect, "|", sum(len(p) for p in pngs) // 1024, "KB")
