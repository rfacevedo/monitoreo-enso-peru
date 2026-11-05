"""Modelo indicativo de riesgo de inundación / huaico por distrito para el piloto de Perú (costa norte).

IMPORTANTE: es un modelo sencillo y transparente para apoyar el monitoreo y la demostración; NO sustituye los comunicados
oficiales del ENFEN ni los avisos del SENAMHI, el INDECI y el CENEPRED. Los pesos y umbrales son una primera calibración
tomada de la experiencia de Ecuador y deben validarse con eventos reales (El Niño costero, emergencias del INDECI).
Autor y mentor del proyecto: Raúl Acevedo.

Riesgo (0 a 1) = w_lluvia · min(1, S_lluvia · (1 + k · ENSO)) + w_mar · S_mar + w_expo · S_expo + w_cauce · S_cauce

- S_lluvia: lluvia pronosticada (NOAA GFS por día; CFSv2 por mes) frente al clima PROPIO de cada distrito
  (``climatologia.json``, generado con ``generar_climatologia.py``): 0 = lluvia típica, 1 = extrema.
- S_mar: 0,5 en distritos costeros cuando el último comunicado del ENFEN indica que el nivel del mar se mantendría por encima de lo
  normal; 0 en caso contrario. Es un indicador CUALITATIVO: el ENFEN no publica una cifra única (pendiente: nivel del mar observado
  de la DHN o de UHSLC).
- S_expo: exposición por baja elevación (inundación) o pendiente supuesta (huaico).
- S_cauce: 1 si el río del distrito está en la lista de ríos con caudales por encima de lo normal del último comunicado del ENFEN.
- ENSO: hasta +15 % sobre la lluvia según ONI y Niño 1+2 (NOAA CPC). Solo afecta a la lluvia: sin lluvia, el mar, la exposición
  y los caudales nunca llevan el riesgo más allá de «bajo».
"""

import datetime
import json
from pathlib import Path

# ----------------------------------------------------------------------------------------------
# Catálogo de distritos piloto (capital distrital; elevación de Open-Meteo / Copernicus DEM). Los nombres de las claves
# («canton», «provincia») vienen del modelo de Ecuador: aquí «canton» = distrito y «provincia» = departamento.
# ----------------------------------------------------------------------------------------------
CANTONES = [
    {"canton": 'Tumbes', "provincia": 'Tumbes', "lat": -3.5669, "lon": -80.4515, "elevacion": 26.0, "amenaza": 'inundacion', "costero": True, "rio": 'Tumbes', "ubigeo": '240101'},
    {"canton": 'Zarumilla', "provincia": 'Tumbes', "lat": -3.5, "lon": -80.2667, "elevacion": 11.0, "amenaza": 'inundacion', "costero": False, "rio": 'Zarumilla', "ubigeo": '240301'},
    {"canton": 'Aguas Verdes', "provincia": 'Tumbes', "lat": -3.4833, "lon": -80.25, "elevacion": 6.0, "amenaza": 'inundacion', "costero": True, "rio": 'Zarumilla', "ubigeo": '240302'},
    {"canton": 'Piura', "provincia": 'Piura', "lat": -5.1945, "lon": -80.6328, "elevacion": 34.0, "amenaza": 'inundacion', "costero": False, "rio": 'Piura', "ubigeo": '200101'},
    {"canton": 'Castilla', "provincia": 'Piura', "lat": -5.2, "lon": -80.615, "elevacion": 33.0, "amenaza": 'inundacion', "costero": False, "rio": 'Piura', "ubigeo": '200104'},
    {"canton": 'Catacaos', "provincia": 'Piura', "lat": -5.2667, "lon": -80.6833, "elevacion": 23.0, "amenaza": 'inundacion', "costero": False, "rio": 'Piura', "ubigeo": '200105'},
    {"canton": 'Sullana', "provincia": 'Piura', "lat": -4.9039, "lon": -80.6853, "elevacion": 62.0, "amenaza": 'inundacion', "costero": False, "rio": 'Chira', "ubigeo": '200601'},
    {"canton": 'Bellavista', "provincia": 'Piura', "lat": -4.8897, "lon": -80.6743, "elevacion": 63.0, "amenaza": 'inundacion', "costero": False, "rio": 'Chira', "ubigeo": '200602'},
    {"canton": 'Marcavelica', "provincia": 'Piura', "lat": -4.8833, "lon": -80.6833, "elevacion": 40.0, "amenaza": 'inundacion', "costero": False, "rio": 'Chira', "ubigeo": '200605'},
    {"canton": 'Paita', "provincia": 'Piura', "lat": -5.0892, "lon": -81.1144, "elevacion": 22.0, "amenaza": 'inundacion', "costero": True, "rio": 'Chira', "ubigeo": '200501'},
    {"canton": 'Pariñas', "provincia": 'Piura', "lat": -4.5772, "lon": -81.2719, "elevacion": 9.0, "amenaza": 'inundacion', "costero": True, "rio": None, "ubigeo": '200701'},
    {"canton": 'Sechura', "provincia": 'Piura', "lat": -5.5569, "lon": -80.8222, "elevacion": 15.0, "amenaza": 'inundacion', "costero": True, "rio": 'Piura', "ubigeo": '200801'},
    {"canton": 'Chulucanas', "provincia": 'Piura', "lat": -5.095, "lon": -80.1633, "elevacion": 91.0, "amenaza": 'inundacion', "costero": False, "rio": 'Piura', "ubigeo": '200401'},
    {"canton": 'Tambo Grande', "provincia": 'Piura', "lat": -4.93, "lon": -80.34, "elevacion": 85.0, "amenaza": 'inundacion', "costero": False, "rio": 'Piura', "ubigeo": '200114'},
    {"canton": 'Ayabaca', "provincia": 'Piura', "lat": -4.64, "lon": -79.72, "elevacion": 2872.0, "amenaza": 'deslave', "costero": False, "rio": None, "ubigeo": '200201'},
    {"canton": 'Huancabamba', "provincia": 'Piura', "lat": -5.24, "lon": -79.45, "elevacion": 1932.0, "amenaza": 'deslave', "costero": False, "rio": None, "ubigeo": '200301'},
    {"canton": 'Chiclayo', "provincia": 'Lambayeque', "lat": -6.7714, "lon": -79.8409, "elevacion": 34.0, "amenaza": 'inundacion', "costero": False, "rio": 'Chancay-Lambayeque', "ubigeo": '140101'},
    {"canton": 'Jose Leonardo Ortiz', "provincia": 'Lambayeque', "lat": -6.75, "lon": -79.8333, "elevacion": 30.0, "amenaza": 'inundacion', "costero": False, "rio": 'Chancay-Lambayeque', "ubigeo": '140105'},
    {"canton": 'Lambayeque', "provincia": 'Lambayeque', "lat": -6.7011, "lon": -79.9061, "elevacion": 19.0, "amenaza": 'inundacion', "costero": False, "rio": 'La Leche', "ubigeo": '140301'},
    {"canton": 'Mochumi', "provincia": 'Lambayeque', "lat": -6.5333, "lon": -79.8833, "elevacion": 35.0, "amenaza": 'inundacion', "costero": False, "rio": 'La Leche', "ubigeo": '140305'},
    {"canton": 'Illimo', "provincia": 'Lambayeque', "lat": -6.4833, "lon": -79.8667, "elevacion": 44.0, "amenaza": 'inundacion', "costero": False, "rio": 'La Leche', "ubigeo": '140303'},
    {"canton": 'Ferreñafe', "provincia": 'Lambayeque', "lat": -6.6381, "lon": -79.7881, "elevacion": 42.0, "amenaza": 'inundacion', "costero": False, "rio": 'Chancay-Lambayeque', "ubigeo": '140201'},
    {"canton": 'Trujillo', "provincia": 'La Libertad', "lat": -8.1116, "lon": -79.0287, "elevacion": 38.0, "amenaza": 'inundacion', "costero": False, "rio": 'Moche', "ubigeo": '130101'},
    {"canton": 'La Esperanza', "provincia": 'La Libertad', "lat": -8.0833, "lon": -79.05, "elevacion": 65.0, "amenaza": 'inundacion', "costero": False, "rio": 'Moche', "ubigeo": '130105'},
    {"canton": 'Huanchaco', "provincia": 'La Libertad', "lat": -8.0833, "lon": -79.1167, "elevacion": 13.0, "amenaza": 'inundacion', "costero": True, "rio": 'Moche', "ubigeo": '130104'},
    {"canton": 'Chepen', "provincia": 'La Libertad', "lat": -7.2236, "lon": -79.4306, "elevacion": 135.0, "amenaza": 'inundacion', "costero": False, "rio": 'Jequetepeque', "ubigeo": '130401'},
    {"canton": 'Guadalupe', "provincia": 'La Libertad', "lat": -7.25, "lon": -79.4667, "elevacion": 97.0, "amenaza": 'inundacion', "costero": False, "rio": 'Jequetepeque', "ubigeo": '130702'},
    {"canton": 'Pacasmayo', "provincia": 'La Libertad', "lat": -7.4, "lon": -79.5667, "elevacion": 7.0, "amenaza": 'inundacion', "costero": True, "rio": 'Jequetepeque', "ubigeo": '130704'},
    {"canton": 'Otuzco', "provincia": 'La Libertad', "lat": -7.9042, "lon": -78.5667, "elevacion": 2677.0, "amenaza": 'deslave', "costero": False, "rio": None, "ubigeo": '130601'},
    {"canton": 'Chicama', "provincia": 'La Libertad', "lat": -7.8433, "lon": -79.1467, "elevacion": 126.0, "amenaza": 'inundacion', "costero": True, "rio": 'Chicama', "ubigeo": '130202'},
]

# ----------------------------------------------------------------------------------------------
# Parámetros del modelo (ajustables)
# ----------------------------------------------------------------------------------------------
PESOS = {
    # Principio de diseño (heredado de Ecuador): una lluvia extrema por sí sola lleva a «alto riesgo»; «crítico» exige además
    # agravantes (mar, caudales). Sin datos de suelo/pendiente, el huaico no pasa de «alto riesgo».
    "inundacion": {"lluvia": 0.55, "mar": 0.15, "expo": 0.15, "cauce": 0.15},
    "deslave": {"lluvia": 0.70, "mar": 0.00, "expo": 0.20, "cauce": 0.00},
}
# Umbrales de respaldo (se usan solo si falta climatologia.json o la estadística de un distrito)
LLUVIA_DIA_MM = 60.0
LLUVIA_3DIAS_MM = 120.0
LLUVIA_MES_MM = 450.0
# CFSv2 mensual: 0 = mediana del mismo mes del año; el percentil 95 del archivo vale 0,5 y se llega a 1 cuando el exceso sobre
# la mediana es el doble del que marca el percentil 95.
MES_FACTOR_SATURACION = 2.0
MAR_ELEVADO = 0.5             # S_mar cuando el ENFEN indica nivel del mar por encima de lo normal
ELEVACION_EXPOSICION_M = 60.0  # por debajo de esta cota hay exposición a inundación (lineal)
PENDIENTE_SUPUESTA = 0.6      # exposición fija de los distritos de huaico (sin datos de pendiente)
ENSO_MAX_AMPLIFICACION = 0.15
ONI_SATURA = 2.0              # °C de ONI que saturan el índice ENSO
NINO12_SATURA = 4.0           # °C de anomalía en Niño 1+2 que saturan el índice ENSO

# Umbrales de alerta (primera calibración, heredada de Ecuador; pendiente de validar con eventos de Perú).
UMBRALES = {"critico": 0.85, "alto": 0.70, "amarilla": 0.50, "deslave_medio": 0.40}


def _cargar_climatologia():
    """Estadísticas locales de lluvia por distrito (si el archivo no existe, el modelo usa los umbrales de respaldo)."""
    try:
        with open(Path(__file__).with_name("climatologia.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


CLIMATOLOGIA = _cargar_climatologia()


def _tope(x, minimo=0.0, maximo=1.0):
    return max(minimo, min(maximo, x))


def _normalizar(valor, bajo, alto):
    """0 en ``bajo`` (lluvia típica) y 1 en ``alto`` (lluvia extrema), lineal entre ambos."""
    return _tope((valor - bajo) / (alto - bajo)) if alto > bajo else 0.0


def s_lluvia_dia(canton, dia_mm, acum3_mm, clima=None):
    """S_lluvia (0-1) de un día GFS: el mayor entre la lluvia del día y la acumulada en 3 días, frente al clima local."""
    est = (CLIMATOLOGIA if clima is None else clima).get("cantones", {}).get(canton["canton"])
    if est:
        return max(_normalizar(dia_mm, est["dia_p50"], est["dia_p99"]),
                   _normalizar(acum3_mm, est["d3_p50"], est["d3_p99"]))
    return _tope(max(dia_mm / LLUVIA_DIA_MM, acum3_mm / LLUVIA_3DIAS_MM))


def s_lluvia_mes(canton, mes, mes_mm, clima=None):
    """S_lluvia (0-1) de un mes CFSv2 frente a la mediana y el percentil 95 del mismo mes del año en ese distrito."""
    est = ((CLIMATOLOGIA if clima is None else clima).get("cantones", {}).get(canton["canton"]) or {}).get("mes", {}).get(str(mes))
    if est:
        return _normalizar(mes_mm, est["p50"], est["p50"] + MES_FACTOR_SATURACION * (est["p95"] - est["p50"]))
    return _tope(mes_mm / LLUVIA_MES_MM)


def indice_enso(oni, nino12):
    """Índice ENSO 0-1: promedio de ONI (trimestral) y Niño 1+2 (semanal). Sin datos -> 0."""
    partes = []
    if oni is not None:
        partes.append(_tope(oni / ONI_SATURA))
    if nino12 is not None:
        partes.append(_tope(nino12 / NINO12_SATURA))
    return sum(partes) / len(partes) if partes else 0.0


def nivel_alerta(riesgo, amenaza):
    if riesgo >= UMBRALES["critico"]:
        return "CRÍTICO"
    if riesgo >= UMBRALES["alto"]:
        return "ALTO RIESGO"
    if amenaza == "deslave" and riesgo >= UMBRALES["deslave_medio"]:
        return "MEDIO (HUAICO)"
    if riesgo >= UMBRALES["amarilla"]:
        return "ALERTA AMARILLA"
    return "BAJO"


def s_mar_enfen(canton, enfen):
    """S_mar (0-1): distrito costero y comunicado ENFEN con nivel del mar por encima de lo normal."""
    if not canton["costero"] or not enfen or enfen.get("_estado") == "error":
        return 0.0
    return MAR_ELEVADO if enfen.get("nivel_mar_elevado") else 0.0


def s_cauce_enfen(canton, enfen):
    """S_cauce (0-1): 1 si el río del distrito figura con caudales por encima de lo normal en el último comunicado del ENFEN."""
    if not canton.get("rio") or not enfen or enfen.get("_estado") == "error":
        return 0.0
    rios = [_sin_tilde(r) for r in (enfen.get("rios_caudal_alto") or [])]
    return 1.0 if _sin_tilde(canton["rio"]) in rios else 0.0


def _sin_tilde(texto):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn").strip()


def calcular_riesgo(canton, s_lluvia, s_mar, s_cauce, enso):
    """Riesgo 0-1 de un distrito para un valor de lluvia ya normalizado (S_lluvia, 0-1)."""
    w = PESOS[canton["amenaza"]]
    if canton["amenaza"] == "deslave":
        s_expo = PENDIENTE_SUPUESTA
    else:
        s_expo = _tope((ELEVACION_EXPOSICION_M - canton["elevacion"]) / ELEVACION_EXPOSICION_M)
    s_ll = _tope(s_lluvia * (1.0 + ENSO_MAX_AMPLIFICACION * enso))
    return _tope(w["lluvia"] * s_ll + w["mar"] * s_mar + w["expo"] * s_expo + w["cauce"] * s_cauce)


def construir_registros(datos, hoy=None):
    """Convierte las fuentes descargadas en registros por distrito y fecha (diarios GFS + mensuales CFSv2)."""
    hoy = hoy or datetime.date.today()
    noaa = datos.get("noaa") or {}
    oni = (noaa.get("oni") or {}).get("anomalia")
    nino12 = (noaa.get("semanal") or {}).get("nino12")
    enfen = datos.get("enfen")
    enso = indice_enso(oni, nino12)

    gfs = (datos.get("gfs") or {}).get("cantones") or {}
    cfs = (datos.get("cfsv2") or {}).get("cantones") or {}
    registros = []
    for c in CANTONES:
        mar = s_mar_enfen(c, enfen)
        cauce = s_cauce_enfen(c, enfen)
        diaria = gfs.get(c["canton"], [])
        ultima_gfs = diaria[-1][0] if diaria else None
        mm = [v for _, v in diaria]
        for i, (fecha, dia_mm) in enumerate(diaria):
            acum3 = sum(mm[max(0, i - 2): i + 1])
            s_ll = s_lluvia_dia(c, dia_mm, acum3)
            r = calcular_riesgo(c, s_ll, mar, cauce, enso)
            registros.append(_fila(c, fecha, dia_mm, mar, cauce, r, "GFS · día"))
        for ini, mes_mm in cfs.get(c["canton"], []):
            fecha = ini.replace(day=15)
            if ultima_gfs is not None and fecha <= ultima_gfs:
                continue                       # el detalle diario de GFS manda dentro de su horizonte
            if fecha < hoy:
                continue
            s_ll = s_lluvia_mes(c, ini.month, mes_mm)
            r = calcular_riesgo(c, s_ll, mar, cauce, enso)
            registros.append(_fila(c, fecha, mes_mm, mar, cauce, r, "CFSv2 · mes"))
    return registros, enso


def _fila(c, fecha, lluvia_mm, mar_idx, cauce_idx, riesgo, horizonte):
    return {
        "canton": c["canton"], "provincia": c["provincia"], "elevacion": c["elevacion"],
        "lluvia_mm": round(lluvia_mm, 1), "mar_idx": round(mar_idx, 2), "cauce_idx": round(cauce_idx, 2),
        "riesgo": round(riesgo, 2), "alerta": nivel_alerta(riesgo, c["amenaza"]),
        "lat": c["lat"], "lon": c["lon"], "fecha": fecha, "horizonte": horizonte,
    }
