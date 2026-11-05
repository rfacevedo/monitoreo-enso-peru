"""Almacén local en SQLite (biblioteca estándar de Python, sin dependencias nuevas).

Para qué sirve:
- **Caché de fuentes**: guarda el último dato descargado de cada fuente. Si la app se reinicia o una fuente
  se cae, se usa el dato guardado (arranque rápido y respaldo).
- **Series históricas del ENSO**: ONI desde 1950 y Niño semanal desde 1981 (se renuevan en cada descarga).
- **Historial del riesgo**: una «foto» del riesgo calculado por distrito en cada descarga, para ver su evolución.

Límite importante: en Streamlit Community Cloud los archivos locales NO se conservan al reiniciar o
volver a desplegar la app. Por eso el historial de riesgo solo dura mientras viva el servidor; las series del
ENSO no dependen de eso porque se vuelven a descargar completas. Para un histórico permanente habría que
guardar los datos fuera de la app (por ejemplo, archivos en el repositorio o una base de datos externa).
"""

import datetime
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

RUTA = Path(os.environ.get("ENSO_DB", Path(tempfile.gettempdir()) / "monitoreo_enso_peru.sqlite"))
RETENCION_DIAS = 120          # el historial de riesgo se poda pasados estos días

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS fuente (
    nombre TEXT PRIMARY KEY, descargado_utc TEXT NOT NULL, clave TEXT, datos TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS oni (anio INTEGER, trimestre TEXT, anomalia REAL, PRIMARY KEY (anio, trimestre));
CREATE TABLE IF NOT EXISTS nino_semanal (
    semana TEXT PRIMARY KEY, nino12 REAL, nino3 REAL, nino34 REAL, nino4 REAL);
CREATE TABLE IF NOT EXISTS historial_riesgo (
    descarga_utc TEXT, canton TEXT, fecha TEXT, riesgo REAL, alerta TEXT,
    lluvia_mm REAL, mar_idx REAL, horizonte TEXT, PRIMARY KEY (descarga_utc, canton, fecha));
CREATE INDEX IF NOT EXISTS ix_hist_canton ON historial_riesgo (canton, descarga_utc);
"""


# ----------------------------------------------------------------------------------------------
# Conexión y serialización
# ----------------------------------------------------------------------------------------------
@contextmanager
def conectar(ruta=None):
    """Abre la base, confirma los cambios al salir y SIEMPRE cierra la conexión (con ``with sqlite3.connect`` no se cierra)."""
    con = sqlite3.connect(str(ruta or RUTA), timeout=15)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(_ESQUEMA)
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _a_json(obj):
    if isinstance(obj, datetime.datetime):
        return {"__dt__": obj.isoformat()}
    if isinstance(obj, datetime.date):
        return {"__fecha__": obj.isoformat()}
    raise TypeError(f"No serializable: {type(obj)}")


def _de_json(d):
    if "__fecha__" in d:
        return datetime.date.fromisoformat(d["__fecha__"])
    if "__dt__" in d:
        return datetime.datetime.fromisoformat(d["__dt__"])
    return d


def _ahora():
    return datetime.datetime.now(datetime.timezone.utc)


# ----------------------------------------------------------------------------------------------
# Caché de fuentes
# ----------------------------------------------------------------------------------------------
def guardar_fuente(nombre, datos, clave=None, momento=None):
    """Guarda (reemplaza) el último dato bueno de una fuente. Las fechas se conservan."""
    limpio = {k: v for k, v in datos.items() if not k.startswith("_")}
    with conectar() as con:
        con.execute("INSERT OR REPLACE INTO fuente VALUES (?,?,?,?)",
                    (nombre, (momento or _ahora()).isoformat(), clave, json.dumps(limpio, default=_a_json, ensure_ascii=False)))


def leer_fuente(nombre, clave=None):
    """Devuelve ``(datos, edad_en_segundos)`` o ``(None, None)``. Si se pasa ``clave`` y no coincide, no hay dato."""
    with conectar() as con:
        fila = con.execute("SELECT * FROM fuente WHERE nombre=?", (nombre,)).fetchone()
    if not fila or (clave is not None and fila["clave"] != clave):
        return None, None
    descargado = datetime.datetime.fromisoformat(fila["descargado_utc"])
    datos = json.loads(fila["datos"], object_hook=_de_json)
    datos["_descargado"] = descargado          # momento exacto de la descarga original
    return datos, (_ahora() - descargado).total_seconds()


# ----------------------------------------------------------------------------------------------
# Series históricas del ENSO
# ----------------------------------------------------------------------------------------------
def guardar_series_enso(oni_filas, nino_filas):
    """``oni_filas``: (anio, trimestre, anomalia); ``nino_filas``: (semana ISO, nino12, nino3, nino34, nino4)."""
    with conectar() as con:
        con.executemany("INSERT OR REPLACE INTO oni VALUES (?,?,?)", oni_filas)
        con.executemany("INSERT OR REPLACE INTO nino_semanal VALUES (?,?,?,?,?)", nino_filas)


def leer_oni(desde_anio=1950):
    with conectar() as con:
        return [dict(f) for f in con.execute("SELECT anio, trimestre, anomalia FROM oni WHERE anio>=? ORDER BY anio", (desde_anio,))]


def leer_nino_semanal(desde="1981-01-01"):
    with conectar() as con:
        return [dict(f) for f in con.execute("SELECT * FROM nino_semanal WHERE semana>=? ORDER BY semana", (desde,))]


# ----------------------------------------------------------------------------------------------
# Historial del riesgo
# ----------------------------------------------------------------------------------------------
def guardar_riesgo(descarga, registros):
    """Guarda una «foto» del riesgo calculado. Si esa descarga ya está guardada, no hace nada. Devuelve True si guardó."""
    marca = descarga.astimezone(datetime.timezone.utc).isoformat()
    with conectar() as con:
        if con.execute("SELECT 1 FROM historial_riesgo WHERE descarga_utc=? LIMIT 1", (marca,)).fetchone():
            return False
        con.executemany(
            "INSERT OR IGNORE INTO historial_riesgo VALUES (?,?,?,?,?,?,?,?)",
            [(marca, r["canton"], r["fecha"].isoformat(), r["riesgo"], r["alerta"], r["lluvia_mm"], r["mar_idx"], r["horizonte"])
             for r in registros])
        corte = (_ahora() - datetime.timedelta(days=RETENCION_DIAS)).isoformat()
        con.execute("DELETE FROM historial_riesgo WHERE descarga_utc<?", (corte,))
    return True


def leer_evolucion_riesgo():
    """Por descarga y cantón: el mayor riesgo de ese momento (todas las fechas del pronóstico)."""
    with conectar() as con:
        return [dict(f) for f in con.execute(
            "SELECT descarga_utc, canton, MAX(riesgo) AS riesgo_max FROM historial_riesgo "
            "GROUP BY descarga_utc, canton ORDER BY descarga_utc")]


# ----------------------------------------------------------------------------------------------
# Semilla: instantánea de los datos para arrancar sin descargar nada
# ----------------------------------------------------------------------------------------------
SEMILLA = Path(__file__).with_name("datos_semilla.json")


def exportar_semilla(ruta=None):
    """Guarda en un archivo JSON lo que hay en el almacén (fuentes y series del ENSO). Se sube al repositorio para que un
    servidor recién iniciado (Streamlit Cloud borra la base al reiniciar) tenga datos al instante, sin descargar."""
    with conectar() as con:
        datos = {
            "fuente": [dict(f) for f in con.execute("SELECT * FROM fuente")],
            "oni": [list(f) for f in con.execute("SELECT anio, trimestre, anomalia FROM oni")],
            "nino_semanal": [list(f) for f in con.execute("SELECT * FROM nino_semanal")],
        }
    Path(ruta or SEMILLA).write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return datos


def importar_semilla(ruta=None):
    """Carga la semilla en el almacén. Agrega las fuentes que faltan y REEMPLAZA las que la semilla tiene más recientes
    (la tarea diaria de GitHub Actions la renueva); nunca pisa un dato más nuevo del almacén. Las series del ENSO se cargan si
    están vacías o si la semilla trae una fuente «noaa» más reciente. Devuelve cuántas fuentes agregó o reemplazó."""
    try:
        datos = json.loads(Path(ruta or SEMILLA).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    cambiadas = 0
    renovar_series = False
    with conectar() as con:
        for f in datos.get("fuente", []):
            actual = con.execute("SELECT descargado_utc FROM fuente WHERE nombre=?", (f["nombre"],)).fetchone()
            if actual is None or actual["descargado_utc"] < f["descargado_utc"]:     # ISO 8601 en UTC: se ordena como texto
                con.execute("INSERT OR REPLACE INTO fuente (nombre, descargado_utc, clave, datos) VALUES (?,?,?,?)",
                            (f["nombre"], f["descargado_utc"], f["clave"], f["datos"]))
                cambiadas += 1
                renovar_series = renovar_series or f["nombre"] == "noaa"
        if renovar_series or con.execute("SELECT COUNT(*) FROM oni").fetchone()[0] == 0:
            con.executemany("INSERT OR REPLACE INTO oni VALUES (?,?,?)", datos.get("oni", []))
        if renovar_series or con.execute("SELECT COUNT(*) FROM nino_semanal").fetchone()[0] == 0:
            con.executemany("INSERT OR REPLACE INTO nino_semanal VALUES (?,?,?,?,?)", datos.get("nino_semanal", []))
    return cambiadas


def resumen():
    """Datos para mostrar el estado del almacén."""
    with conectar() as con:
        n = lambda t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
        fotos = con.execute("SELECT COUNT(DISTINCT descarga_utc) FROM historial_riesgo").fetchone()[0]
        primera = con.execute("SELECT MIN(descarga_utc) FROM historial_riesgo").fetchone()[0]
        cuentas = {"oni": n("oni"), "nino_semanal": n("nino_semanal")}
    return {"ruta": str(RUTA), "tamano_kb": round(os.path.getsize(RUTA) / 1024, 1) if RUTA.exists() else 0,
            **cuentas, "fotos_riesgo": fotos, "desde": primera}
