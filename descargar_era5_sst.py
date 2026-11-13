"""Descarga la SST mensual de ERA5 (CDS de Copernicus) del Pacífico tropical oriental, 1991 a la fecha.

Usa la clave de ~/.cdsapirc o de las variables CDSAPI_URL / CDSAPI_KEY (no se guarda en el repositorio). Salida: _datos/era5_sst_mensual.nc

    python descargar_era5_sst.py
"""
import datetime
import os

import cdsapi

hoy = datetime.date.today()
anios = [str(a) for a in range(1991, hoy.year + 1)]
os.makedirs("_datos", exist_ok=True)
# Credenciales: ~/.cdsapirc, o las variables CDSAPI_URL y CDSAPI_KEY (GitHub Actions)
c = cdsapi.Client()
c.retrieve("reanalysis-era5-single-levels-monthly-means", {
    "product_type": "monthly_averaged_reanalysis",
    "variable": ["sea_surface_temperature"],
    "year": anios,
    "month": [f"{m:02d}" for m in range(1, 13)],
    "time": "00:00",
    "area": [15, -180, -20, -60],          # N, O, S, E: Pacífico tropical (Niño 1+2 a Niño 4) y costas de EC/PE/CO
    "data_format": "netcdf",
    "download_format": "unarchived",
}, "_datos/era5_sst_mensual.nc")
print("listo")
