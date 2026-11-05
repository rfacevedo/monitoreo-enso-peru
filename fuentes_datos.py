"""Conexión automática a las fuentes de datos del piloto de Perú: NOAA, ENFEN y CIIFEN.

Todas las funciones ``obtener_*`` hacen peticiones web y lanzan una excepción si fallan;
``con_respaldo`` conserva el último dato bueno (en memoria y en SQLite, ver ``almacen.py``) para no dejar
la app sin información cuando una fuente se cae, y ``cargar_todo`` reutiliza lo guardado si es reciente. Las funciones ``parsear_*`` son puras (texto -> datos) para poder probarlas
sin red.

Fuentes (verificadas desde el servidor):
- NOAA CPC: ONI mensual y temperaturas del mar Niño semanales (archivos de texto).
- NOAA GFS vía Open-Meteo: lluvia diaria por distrito (16 días).
- CCSR/IRI (modelo CFSv2 de NOAA/NCEP): lluvia mensual por distrito (OPeNDAP binario).
- SENAMHI: tabla pública de avisos meteorológicos (HTML): número, emisión, inicio, fin y nivel (verde/amarillo/naranja/rojo).
- ENFEN (IMARPE, DHN, IGP, SENAMHI, ANA, INDECI): último comunicado oficial (página web) con estado, magnitud, lluvias y caudales.
- CIIFEN: boletín mensual «El Niño/La Niña en América Latina» (PDF).
"""

import datetime
import hashlib
import io
import re
import struct
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

import almacen

UA = {"User-Agent": "Mozilla/5.0 (monitoreo-enso-peru; GeoCiencias)"}
TIMEOUT = 60

URL_ONI = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
URL_NINO_SEMANAL = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"
URL_OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
URL_CFSV2 = "https://forecast.ccsr.columbia.edu/data/NMME/NOAA-NCEP/CFSv2/early_month_samples/forecast/"
URL_ENFEN = "https://enfen.imarpe.gob.pe/"
URL_SENAMHI_AVISOS = "https://www.senamhi.gob.pe/?&p=aviso-meteorologico"
URL_CIIFEN_LISTA = "https://ciifen.org/el-nino-la-nina-ciifen/"

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

_ULTIMO_BUENO = {}


# ----------------------------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------------------------
def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=kw.pop("timeout", TIMEOUT), **kw)
    r.raise_for_status()
    return r


def con_respaldo(nombre, funcion, clave=None):
    """Ejecuta la función y guarda el resultado (memoria y SQLite). Si falla, devuelve el último dato bueno
    —de memoria o, si se perdió, de SQLite— marcado como «respaldo»; si no hay ninguno, un error."""
    try:
        dato = funcion()
        momento = datetime.datetime.now(datetime.timezone.utc)
        _ULTIMO_BUENO[nombre] = dict(dato)
        try:
            almacen.guardar_fuente(nombre, dato, clave, momento)
        except Exception:  # noqa: BLE001 - el almacén es una ayuda: nunca debe romper la descarga
            pass
        dato["_estado"] = "ok"
        dato["_descargado"] = momento
        return dato
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de red o de formato
        previo = _ULTIMO_BUENO.get(nombre)
        if previo is None:
            try:
                previo, _ = almacen.leer_fuente(nombre, clave)
            except Exception:  # noqa: BLE001
                previo = None
        if previo is not None:
            previo = dict(previo)
            previo["_estado"] = "respaldo"
            previo["_error"] = str(exc)[:120]
            return previo
        return {"_estado": "error", "_error": str(exc)[:120]}


def _normalizar(texto):
    return re.sub(r"\s+", " ", texto.replace("­", "")).strip()


def _texto_pdf(contenido, paginas=None):
    from pypdf import PdfReader

    lector = PdfReader(io.BytesIO(contenido))
    hojas = lector.pages if paginas is None else lector.pages[:paginas]
    return _normalizar(" ".join((h.extract_text() or "") for h in hojas))


# ----------------------------------------------------------------------------------------------
# NOAA CPC: ONI y Niño semanal
# ----------------------------------------------------------------------------------------------
def parsear_oni(texto):
    """Última fila del ONI: ``  JAS 2026  29.12   2.16`` -> trimestre y anomalía (°C)."""
    ultima = None
    for linea in texto.splitlines():
        m = re.match(r"^\s*([A-Z]{3})\s+(\d{4})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$", linea)
        if m:
            ultima = m
    if not ultima:
        raise ValueError("ONI: sin filas")
    return {"trimestre": f"{ultima.group(1)} {ultima.group(2)}", "anomalia": float(ultima.group(4)),
            "url": URL_ONI}


def parsear_nino_semanal(texto):
    """Última semana: SST y anomalía de Niño 1+2, 3, 3.4 y 4.

    El archivo es de ancho fijo y los valores negativos van pegados al anterior (``20.6-0.1``),
    por eso se extraen con una expresión que no exige espacios entre números.
    """
    fila = re.compile(r"^\s*(\d{2}[A-Z]{3}\d{4})\s+((?:-?\d+\.\d\s*){8})\s*$")
    ultima = None
    for linea in texto.splitlines():
        m = fila.match(linea)
        if m:
            ultima = m
    if not ultima:
        raise ValueError("Niño semanal: sin filas")
    v = [float(x) for x in re.findall(r"-?\d+\.\d", ultima.group(2))]
    fecha = datetime.datetime.strptime(ultima.group(1).title(), "%d%b%Y").date()
    return {"semana": fecha, "nino12": v[1], "nino3": v[3], "nino34": v[5], "nino4": v[7], "url": URL_NINO_SEMANAL}


def parsear_oni_serie(texto):
    """Todas las filas del ONI: ``(año, trimestre, anomalía)``."""
    filas = []
    for linea in texto.splitlines():
        m = re.match(r"^\s*([A-Z]{3})\s+(\d{4})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$", linea)
        if m:
            filas.append((int(m.group(2)), m.group(1), float(m.group(4))))
    return filas


def parsear_nino_serie(texto):
    """Todas las semanas: ``(fecha ISO, nino12, nino3, nino34, nino4)`` (anomalías en °C)."""
    fila = re.compile(r"^\s*(\d{2}[A-Z]{3}\d{4})\s+((?:-?\d+\.\d\s*){8})\s*$")
    filas = []
    for linea in texto.splitlines():
        m = fila.match(linea)
        if m:
            v = [float(x) for x in re.findall(r"-?\d+\.\d", m.group(2))]
            fecha = datetime.datetime.strptime(m.group(1).title(), "%d%b%Y").date()
            filas.append((fecha.isoformat(), v[1], v[3], v[5], v[7]))
    return filas


def asegurar_series_enso():
    """Si el almacén no tiene las series completas del ONI y del Niño (p. ej. tras reiniciar el servidor, o si los
    datos vienen de una caché anterior), las descarga y las guarda. Son dos archivos de texto pequeños."""
    estado = almacen.resumen()
    if estado["oni"] == 0 or estado["nino_semanal"] == 0:
        almacen.guardar_series_enso(parsear_oni_serie(_get(URL_ONI).text),
                                    parsear_nino_serie(_get(URL_NINO_SEMANAL).text))


def obtener_noaa_enso():
    texto_oni, texto_nino = _get(URL_ONI).text, _get(URL_NINO_SEMANAL).text
    oni, semanal = parsear_oni(texto_oni), parsear_nino_semanal(texto_nino)
    try:                                   # series completas para los gráficos históricos
        almacen.guardar_series_enso(parsear_oni_serie(texto_oni), parsear_nino_serie(texto_nino))
    except Exception:  # noqa: BLE001
        pass
    return {"oni": oni, "semanal": semanal}


# ----------------------------------------------------------------------------------------------
# NOAA GFS (vía Open-Meteo): lluvia diaria por distrito
# ----------------------------------------------------------------------------------------------
def obtener_gfs(cantones, dias=16):
    """Lluvia diaria (mm) de los próximos ``dias`` días para cada distrito (diccionario por nombre)."""
    params = {
        "latitude": ",".join(str(c["lat"]) for c in cantones),
        "longitude": ",".join(str(c["lon"]) for c in cantones),
        "daily": "precipitation_sum", "models": "gfs_global",
        "forecast_days": dias, "timezone": "America/Lima",
    }
    j = _get(URL_OPEN_METEO, params=params).json()
    j = j if isinstance(j, list) else [j]
    if len(j) != len(cantones):
        raise ValueError("GFS: respuesta incompleta")
    salida = {}
    for c, o in zip(cantones, j):
        fechas = [datetime.date.fromisoformat(t) for t in o["daily"]["time"]]
        mm = [0.0 if v is None else float(v) for v in o["daily"]["precipitation_sum"]]
        salida[c["canton"]] = list(zip(fechas, mm))
    return {"cantones": salida, "url": "https://open-meteo.com/en/docs/gfs-api"}


# ----------------------------------------------------------------------------------------------
# CCSR/IRI (CFSv2): lluvia mensual por distrito, lectura OPeNDAP binaria (.dods)
# ----------------------------------------------------------------------------------------------
def _dods(url):
    """Devuelve (DataView-like bytes de datos). Formato: texto DDS + '\\nData:\\n' + XDR big-endian."""
    b = _get(url, timeout=90).content
    i = b.find(b"\nData:\n")
    if i < 0:
        raise ValueError("Respuesta OPeNDAP inválida")
    return b[i + 7:]


def _floats(datos):
    n = struct.unpack(">I", datos[:4])[0]
    return struct.unpack(">%df" % n, datos[8:8 + 4 * n])


def _ints(datos):
    n = struct.unpack(">I", datos[:4])[0]
    return struct.unpack(">%di" % n, datos[8:8 + 4 * n])


def obtener_cfsv2(cantones):
    """Lluvia mensual (mm/mes) de los próximos 10 meses para cada distrito (media de 32 miembros)."""
    NM, NL, NY, NX = 32, 10, 7, 6          # miembros, meses, lat -9..-3, lon -82..-77 (rejilla de 1°)
    dds = _get(URL_CFSV2 + "pr.dds").text
    pos = dds.index("S = ")
    n_s = int(re.match(r"\d+", dds[pos + 4:]).group(0))
    s = n_s - 1
    rng = f"[{s}][0:{NM - 1}][0:{NL - 1}][81:87][278:283]"
    pr = _floats(_dods(f"{URL_CFSV2}pr.dods?pr{rng}"))
    dias = _ints(_dods(f"{URL_CFSV2}pr.dods?target[{s}][0:{NL - 1}]"))
    lats = _ints(_dods(f"{URL_CFSV2}pr.dods?Y[81:87]"))
    lons = _ints(_dods(f"{URL_CFSV2}pr.dods?X[278:283]"))
    emision = _ints(_dods(f"{URL_CFSV2}pr.dods?S[{s}]"))[0]
    if lats[0] != -9 or lats[-1] != -3 or lons[0] != 278:
        raise ValueError("La rejilla del servicio CFSv2 cambió")
    base = datetime.date(1960, 1, 1)
    meses = [base + datetime.timedelta(days=d) for d in dias]
    celdas = NY * NX
    por_miembro = NL * celdas
    media = [[0.0] * celdas for _ in range(NL)]
    for m in range(NM):
        for l in range(NL):
            fila = media[l]
            off = m * por_miembro + l * celdas
            for j in range(celdas):
                fila[j] += pr[off + j] / NM
    salida = {}
    for c in cantones:
        iy = min(max(round(c["lat"]) + 9, 0), NY - 1)
        ix = min(max(round(c["lon"]) + 82, 0), NX - 1)
        serie = []
        for l in range(NL):
            ini = meses[l]
            sig = (ini.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
            dias_mes = (sig - ini).days
            serie.append((ini, max(media[l][iy * NX + ix], 0.0) * dias_mes))   # mm/día -> mm/mes
        salida[c["canton"]] = serie
    return {"cantones": salida, "emision": base + datetime.timedelta(days=emision), "url": URL_CFSV2}


# ----------------------------------------------------------------------------------------------
# ENFEN: comunicado oficial más reciente (página web de enfen.imarpe.gob.pe)
# ----------------------------------------------------------------------------------------------
def _texto_html(html_texto):
    """Texto visible de una página HTML (sin scripts ni estilos), con espacios normalizados."""
    import html as _html
    limpio = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_texto, flags=re.S | re.I)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", limpio))).strip()


def ultimo_comunicado_enfen(html_portada):
    """De la portada del ENFEN, el comunicado más reciente: ``(fecha, url)``."""
    enlaces = set(re.findall(r"https://enfen\.imarpe\.gob\.pe/(20\d\d)/(\d\d)/(\d\d)/(comunicado-oficial-enfen[a-z0-9\-]*)/", html_portada))
    if not enlaces:
        raise ValueError("ENFEN: sin comunicados en la portada")
    a, m, d, slug = max(enlaces, key=lambda e: (e[0], e[1], e[2]))
    return datetime.date(int(a), int(m), int(d)), f"https://enfen.imarpe.gob.pe/{a}/{m}/{d}/{slug}/"


def _lista_rios(txt):
    rios = [r.strip(" .") for r in re.split(r",|\sy\s", txt) if r.strip(" .")]
    return [r[:1].upper() + r[1:] for r in rios if len(r) < 40]


def parsear_enfen(texto_comunicado, texto_portada=""):
    """Extrae del comunicado ENFEN lo que usa el modelo. Lo que no se encuentre queda en None / vacío.

    ``texto_portada`` (opcional) sirve para el «Estado de sistema de alerta», que no todos los comunicados repiten.
    """
    t = _normalizar(texto_comunicado)
    out = {"comunicado": None, "magnitud_nino12": None, "hasta_nino12": None, "magnitud_nino34": None, "hasta_nino34": None,
           "estado": None, "lluvia_costa_norte_sobre_normal": False, "rios_caudal_alto": [], "nivel_mar_elevado": False,
           "proximo": None, "resumen": None}
    m = re.search(r"COMUNICADO OFICIAL ENFEN\s*N\s*°?\s*(\d+)\s*[-–]?\s*(20\d\d)", t, re.I)
    if m:
        out["comunicado"] = f"{int(m.group(1)):02d}-{m.group(2)}"
    m = re.search(r"Niño costero \(regi[oó]n Niño 1\s*\+\s*2\)[^.]{0,60}?magnitud\s+(?:de\s+)?([a-záéíóú]+)\d?\s+hasta\s+([a-záéíóú]+(?:\s+de)?\s+\d{4})", t, re.I)
    if m:
        out["magnitud_nino12"], out["hasta_nino12"] = m.group(1).lower(), m.group(2)
    m = re.search(r"Niño 3\.4\)[^.]{0,120}?magnitud\s+(?:de\s+)?((?:muy\s+)?[a-záéíóú]+)\d?\s*,?\s+hasta\s+([a-záéíóú]+(?:\s+de)?\s+\d{4})", t, re.I)
    if m:
        out["magnitud_nino34"], out["hasta_nino34"] = m.group(1).lower(), m.group(2)
    m = re.search(r"Estado de sistema de alerta:\s*((?:Alerta|Vigilancia|Fin de alerta|No activo)[A-Za-zÁÉÍÓÚáéíóúñ ]{0,40}?(?:Costero|Costera|Costeros|activo))",
                  _normalizar(texto_portada or texto_comunicado), re.I)
    if m:
        out["estado"] = m.group(1).strip()
    if re.search(r"lluvias[^.]{0,80}por encima de lo normal en la costa norte", t, re.I):
        out["lluvia_costa_norte_sobre_normal"] = True
    m = re.search(r"caudales[^.]{0,80}superiores a lo normal en los r[ií]os de la costa norte,?\s*principalmente\s+([^.]{5,160})\.", t, re.I)
    if m:
        out["rios_caudal_alto"] = _lista_rios(m.group(1))
    if re.search(r"nivel del mar[^.]{0,60}por encima de (?:sus )?valores normales", t, re.I):
        out["nivel_mar_elevado"] = True
    m = re.search(r"pr[oó]ximo Comunicado Oficial se emitir[aá] el ([^.]{5,60})\.", t, re.I)
    if m:
        out["proximo"] = m.group(1).strip()
    m = re.search(r"Posted at \d\d:\d\dh in \w+ by \w+\s+(.+?\.)\s+[A-ZÁÉÍÓÚ]", t)
    if m:
        out["resumen"] = m.group(1)[:300]
    if not out["magnitud_nino12"] and not out["rios_caudal_alto"]:
        raise ValueError("ENFEN: no se encontró la magnitud de El Niño costero ni los caudales")
    return out


def obtener_enfen():
    portada = _get(URL_ENFEN, timeout=60).text
    fecha, url = ultimo_comunicado_enfen(portada)
    datos = parsear_enfen(_texto_html(_get(url, timeout=60).text), _texto_html(portada))
    datos.update({"fecha": fecha, "url": url})
    return datos


# ----------------------------------------------------------------------------------------------
# SENAMHI: avisos meteorológicos (tabla HTML pública)
# ----------------------------------------------------------------------------------------------
def parsear_avisos_senamhi(html_texto):
    """Filas de la tabla de avisos del SENAMHI: ``[{titulo, numero, estado, emision, inicio, fin, horas, nivel, url}]``."""
    import html as _html
    avisos = []
    for fila in re.findall(r"<tr[^>]*>(.*?)</tr>", html_texto, flags=re.S | re.I):
        celdas = [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                  for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", fila, flags=re.S | re.I)]
        if len(celdas) < 7:
            continue
        m = re.match(r"(\d+)\s*(?:\((\w+)\))?", celdas[1])
        fechas = [re.fullmatch(r"\d{4}-\d{2}-\d{2}", c) for c in celdas[2:5]]
        if not m or not all(fechas):
            continue
        enlace = re.search(r'href="([^"]+)"', fila)
        url = None
        if enlace:
            url = "https://www.senamhi.gob.pe/" + enlace.group(1).lstrip("./") if not enlace.group(1).startswith("http") else enlace.group(1)
        avisos.append({
            "titulo": celdas[0], "numero": int(m.group(1)), "estado": m.group(2) or "",
            "emision": datetime.date.fromisoformat(celdas[2]), "inicio": datetime.date.fromisoformat(celdas[3]),
            "fin": datetime.date.fromisoformat(celdas[4]), "horas": celdas[5], "nivel": celdas[6].upper(), "url": url})
    if not avisos:
        raise ValueError("SENAMHI: no se encontró la tabla de avisos")
    return avisos


def obtener_senamhi():
    avisos = parsear_avisos_senamhi(_get(URL_SENAMHI_AVISOS, timeout=90).text)
    avisos.sort(key=lambda a: (a["emision"], a["numero"]), reverse=True)   # la numeración se reinicia cada año
    return {"avisos": avisos[:15], "url": URL_SENAMHI_AVISOS}


def avisos_costa_norte(senamhi, hoy):
    """Avisos del SENAMHI que mencionan la costa norte y siguen vigentes (``fin`` >= hoy), del más reciente al más antiguo."""
    if not senamhi or senamhi.get("_estado") == "error":
        return []
    return [a for a in senamhi.get("avisos", []) if "COSTA NORTE" in a["titulo"].upper() and a["fin"] >= hoy]


# ----------------------------------------------------------------------------------------------
# CIIFEN: boletín mensual El Niño / La Niña (PDF)
# ----------------------------------------------------------------------------------------------
def ultimo_boletin_ciifen(html):
    """Del listado de CIIFEN, el PDF más reciente: ``(año, mes, url)``."""
    enlaces = re.findall(r"https://ciifen\.org/wp-content/uploads/(\d{4})/(\d{2})/(Boletin_ElNino_LaNina_[^\"'<> ]+?\.pdf)", html)
    if not enlaces:
        raise ValueError("CIIFEN: sin boletines")
    anio, mes, nombre = max(enlaces, key=lambda e: (int(e[0]), int(e[1])))
    return int(anio), int(mes), f"https://ciifen.org/wp-content/uploads/{anio}/{mes}/{nombre}"


def parsear_ciifen(texto):
    """Estado del evento (titular del boletín) y perspectiva del trimestre próximo."""
    t = _normalizar(texto)
    out = {"estado": None, "perspectiva": None, "periodo": None}
    meses = "|".join(m.capitalize() for m in MESES)
    # El titular viene como «Septiembre de 2026: El Niño continúa fortaleciéndose» y el texto extraído
    # a veces lo pega con los rótulos de la diapositiva siguiente: se corta ahí.
    m = re.search(rf"({meses})\s+de\s+(\d{{4}}):\s*(.+?)(?=\s+Condiciones\s|\s+El Niño/La Niña en|\s*[•·]|$)", t)
    if m:
        out["periodo"] = f"{m.group(1)} {m.group(2)}"
        out["estado"] = m.group(3).strip()[:140]
    m = re.search(r"(El pronóstico para el trimestre[^•·]{10,260}?\.)(\s+Se espera que[^•·]{5,160}?\.)?", t)
    if m:
        out["perspectiva"] = (m.group(1) + (m.group(2) or "")).strip()[:300]
    if not out["estado"]:
        raise ValueError("CIIFEN: no se encontró el estado del evento")
    return out


def obtener_ciifen():
    anio, mes, url = ultimo_boletin_ciifen(_get(URL_CIIFEN_LISTA).text)
    texto = _texto_pdf(_get(url, timeout=90).content, paginas=3)
    datos = parsear_ciifen(texto)
    datos.update({"boletin": f"{MESES[mes - 1].capitalize()} {anio}", "url": url})
    return datos


# ----------------------------------------------------------------------------------------------
# Carga conjunta (en paralelo)
# ----------------------------------------------------------------------------------------------
def _clave_cantones(cantones):
    """Huella del catálogo de cantones: si cambia, lo guardado de GFS/CFSv2 ya no sirve."""
    texto = "|".join(f"{c['canton']}:{c['lat']}:{c['lon']}" for c in cantones)
    return hashlib.sha1(texto.encode()).hexdigest()[:12]


_BLOQUEO_DESCARGA = threading.Lock()


def descarga_en_curso():
    """True si otro usuario está actualizando los datos en este momento."""
    return _BLOQUEO_DESCARGA.locked()


def cargar_todo(cantones, max_edad_s=None, refrescar=False):
    """Obtiene todas las fuentes en paralelo.

    Por defecto NO descarga nada de internet: usa lo guardado en SQLite sea cual sea su antigüedad, para que entrar a la
    aplicación sea instantáneo aunque haya muchos usuarios. Solo descarga:
    - con ``refrescar=True`` (botón «Actualizar datos»: ignora lo guardado), o
    - si de una fuente no hay ningún dato (ni en SQLite ni en la semilla ``datos_semilla.json``): último recurso.
    Con ``max_edad_s`` (segundos) se vuelve a descargar lo guardado hace más tiempo que eso. Si una descarga falla se usa
    el último dato bueno (``_estado`` = «respaldo»).
    """
    if refrescar:
        max_edad_s = -1
    else:
        try:
            almacen.importar_semilla()          # solo agrega lo que falta; no pisa nada
        except Exception:  # noqa: BLE001
            pass
    clave = _clave_cantones(cantones)
    tareas = {
        "noaa": (lambda: obtener_noaa_enso(), None),
        "gfs": (lambda: obtener_gfs(cantones), clave),
        "cfsv2": (lambda: obtener_cfsv2(cantones), clave),
        "enfen": (lambda: obtener_enfen(), None),
        "senamhi": (lambda: obtener_senamhi(), None),
        "ciifen": (lambda: obtener_ciifen(), None),
    }

    def una(nombre, funcion, k):
        try:
            guardado, edad = almacen.leer_fuente(nombre, k)
        except Exception:  # noqa: BLE001
            guardado, edad = None, None
        if guardado is not None and edad is not None and (max_edad_s is None or edad < max_edad_s):
            guardado.update({"_estado": "ok", "_origen": "sqlite", "_edad_s": edad})   # «_descargado» ya viene del almacén
            _ULTIMO_BUENO[nombre] = {k2: v for k2, v in guardado.items() if not k2.startswith("_")}
            return guardado
        dato = con_respaldo(nombre, funcion, k)
        dato.setdefault("_edad_s", 0.0)
        dato.setdefault("_descargado", datetime.datetime.now(datetime.timezone.utc))
        return dato

    with ThreadPoolExecutor(max_workers=6) as pool:
        futuros = {n: pool.submit(una, n, f, k) for n, (f, k) in tareas.items()}
        resultados = {n: f.result() for n, f in futuros.items()}
    # Momento de la descarga más antigua (con arranque rápido es el original, no el de ahora): así la «foto» del
    # riesgo no se repite cada vez que arranca un proceso
    momentos = [r["_descargado"] for r in resultados.values() if r.get("_descargado")]
    resultados["_cargado"] = min(momentos).replace(microsecond=0) if momentos else datetime.datetime.now(datetime.timezone.utc)
    return resultados


def actualizar_todo(cantones):
    """Descarga de nuevo todas las fuentes (botón «Actualizar datos»). Si otro usuario ya está actualizando, no hace nada
    y devuelve None; así un montón de clics no lanzan un montón de descargas."""
    if not _BLOQUEO_DESCARGA.acquire(blocking=False):
        return None
    try:
        return cargar_todo(cantones, refrescar=True)
    finally:
        _BLOQUEO_DESCARGA.release()


# ----------------------------------------------------------------------------------------------
# Actualización diaria en segundo plano (mientras la app esté despierta)
# ----------------------------------------------------------------------------------------------
HORA_ACTUALIZACION = 5            # hora de Perú desde la que se renueva el día
_ACTUALIZADOR = {"hilo": None}


def _hoy_peru(ahora_utc=None):
    ahora = ahora_utc or datetime.datetime.now(datetime.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return ahora.astimezone(ZoneInfo("America/Lima"))
    except Exception:  # noqa: BLE001
        return ahora - datetime.timedelta(hours=5)


def toca_actualizar(ultima_descarga_utc, ahora_utc=None):
    """True si los datos son de un día anterior (hora de Perú) y ya pasó la hora de renovarlos."""
    ahora = _hoy_peru(ahora_utc)
    if ultima_descarga_utc is None:
        return ahora.hour >= HORA_ACTUALIZACION
    return ultima_descarga_utc.astimezone(ahora.tzinfo).date() < ahora.date() and ahora.hour >= HORA_ACTUALIZACION


def _ultima_descarga(cantones):
    """Momento de la descarga más antigua entre las fuentes guardadas (None si falta alguna)."""
    momentos = []
    for nombre, k in (("noaa", None), ("gfs", _clave_cantones(cantones)), ("cfsv2", _clave_cantones(cantones)),
                      ("enfen", None), ("senamhi", None), ("ciifen", None)):
        datos, _ = almacen.leer_fuente(nombre, k)
        if datos is None:
            return None
        momentos.append(datos["_descargado"])
    return min(momentos)


def iniciar_actualizador_diario(cantones, al_actualizar=None, cada_s=1800, espera_inicial_s=90):
    """Lanza (una sola vez por proceso) un hilo que cada ``cada_s`` segundos comprueba si cambió el día y, si es así,
    descarga las fuentes y llama a ``al_actualizar`` (p. ej. para vaciar la caché de la app). No descarga al arrancar. Solo
    corre mientras la app esté despierta: la renovación garantizada la hace la tarea diaria de GitHub Actions."""
    if _ACTUALIZADOR["hilo"] is not None:
        return _ACTUALIZADOR["hilo"]
    parar = threading.Event()

    def ciclo():
        if parar.wait(espera_inicial_s):
            return
        while not parar.is_set():
            try:
                if toca_actualizar(_ultima_descarga(cantones)) and not descarga_en_curso():
                    if actualizar_todo(cantones) is not None and al_actualizar:
                        al_actualizar()
            except Exception:  # noqa: BLE001 - el hilo nunca debe morir por un fallo de red
                pass
            parar.wait(cada_s)

    hilo = threading.Thread(target=ciclo, name="actualizador-diario", daemon=True)
    hilo.parar = parar
    _ACTUALIZADOR["hilo"] = hilo
    hilo.start()
    return hilo
