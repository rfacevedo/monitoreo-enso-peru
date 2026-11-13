"""Índices Niño de ERA5 (SST mensual) y comparación con NOAA CPC. Salida: _datos/era5_indices.json y resumen por pantalla."""
import json, re
import numpy as np, requests, xarray as xr

ds = xr.open_dataset("_datos/era5_sst_mensual.nc")
tn = "valid_time" if "valid_time" in ds.dims else "time"
sst = ds["sst"] - 273.15
if "expver" in sst.dims:                      # meses recientes pueden venir con expver (ERA5T)
    sst = sst.mean("expver", skipna=True)
sst = sst.rename({tn: "t"}).sortby("t")
lat, lon = ("latitude", "longitude")
def caja(s, n, s_, o, e):
    return s.sel({lat: slice(n, s_), lon: slice(o, e)}).mean([lat, lon])
cajas = {"nino34": (5, -5, -170, -120), "nino12": (0, -10, -90, -80), "nino3": (5, -5, -150, -90), "nino4": (5, -5, 160 - 360, -150)}
sal = {}
base = sst.sel(t=slice("1991-01-01", "2020-12-31"))
for k, (n, s_, o, e) in cajas.items():
    x = caja(sst, n, s_, o, e)
    clim = caja(base, n, s_, o, e).groupby("t.month").mean()
    an = x.groupby("t.month") - clim
    sal[k] = {"sst": x, "anom": an}
# ONI-like: media móvil de 3 meses de la anomalía Niño 3.4
oni_era5 = sal["nino34"]["anom"].rolling(t=3, center=True).mean()
# NOAA ONI
txt = requests.get("https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt", timeout=60, headers={"User-Agent": "Mozilla/5.0"}).text
mes0 = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6, "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}
oni = {}
for l in txt.splitlines():
    p = l.split()
    if len(p) >= 4 and p[0] in mes0:
        oni[f"{p[1]}-{mes0[p[0]]:02d}"] = float(p[3])
f = oni_era5.t.dt.strftime("%Y-%m").values
e = oni_era5.values
pares = [(oni[a], v) for a, v in zip(f, e) if a in oni and not np.isnan(v) and a >= "1991-01"]
x, y = np.array(pares).T
print("meses comparados", len(x), "| r =", round(float(np.corrcoef(x, y)[0, 1]), 3), "| sesgo ERA5-ONI =", round(float((y - x).mean()), 2), "| RMSE =", round(float(((y - x) ** 2).mean() ** .5), 2))
ult = sal["nino34"]["anom"].dropna("t")[-6:]
print("Últimos meses ERA5 (anomalía Niño 3.4):", [(str(t)[:7], round(float(v), 2)) for t, v in zip(ult.t.values, ult.values)])
for k in sal:
    a = sal[k]["anom"].dropna("t")
    print(k, "último", str(a.t.values[-1])[:7], "SST", round(float(sal[k]["sst"].dropna("t")[-1]), 2), "anom", round(float(a[-1]), 2))
json.dump({"ultimo_mes": str(ult.t.values[-1])[:7],
           **{k: {str(t)[:7]: round(float(v), 3) for t, v in zip(sal[k]["anom"].t.values, sal[k]["anom"].values) if not np.isnan(v)} for k in sal}},
          open("_datos/era5_indices.json", "w"))
# años de El Niño fuerte: pico ERA5 vs ONI
for a in ("1997-12", "2015-12", "2023-12"):
    print(a, "ONI(NDJ aprox)", oni.get(a), "ERA5", round(float(oni_era5.sel(t=a).values[0]), 2) if a in f else None)
