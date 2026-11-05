import os
"""Pruebas sin red de los lectores de fuentes y del modelo de riesgo.

Ejecutar:  python -m unittest test_fuentes_modelo -v
"""
import datetime
import tempfile
import unittest
from pathlib import Path

import almacen
import distritos_norte as dn
import fuentes_datos as fd
import modelo_riesgo as mr

# Las pruebas nunca tocan la base real: usan una SQLite temporal
almacen.RUTA = Path(tempfile.mkdtemp()) / "prueba.sqlite"
almacen.SEMILLA = Path(tempfile.mkdtemp()) / "sin_semilla.json"      # las pruebas no usan la semilla real del repositorio

ENFEN_PORTADA = (
    "COMUNICADO OFICIAL ENFEN N°19- 2026 El calentamiento oceánico persiste... 30 Octubre, 2026 "
    "COMUNICADO OFICIAL ENFEN N° 17 – 2026 Estado de sistema de alerta: Alerta de El Niño Costero El Niño Costero (región Niño 1+2) se mantendría..."
)
ENFEN_TEXTO = (
    "COMUNICADO OFICIAL ENFEN N°19- 2026 30 Oct COMUNICADO OFICIAL ENFEN N°19- 2026 Posted at 18:37h in Noticias by pr3nsa "
    "El calentamiento oceánico persiste en gran parte del Pacífico tropical, registrando la máxima intensidad frente a la costa peruana. "
    "El Niño costero (región Niño 1+2), mantendría una magnitud extraordinaria2 hasta febrero de 2027 (Figura 2); luego, su magnitud tendería a disminuir. "
    "Por su parte, en el Pacífico central (región Niño 3.4) es más probable que El Niño continúe con un calentamiento de magnitud de muy fuerte hasta febrero de 2027 , con un declive. "
    "Las lluvias, de noviembre 2026 a enero 20276, continuarían por encima de lo normal en la costa norte, sierra occidental, costa central. "
    "Los caudales registrarían valores superiores a lo normal en los ríos de la costa norte, principalmente Tumbes, Chira, Piura, La Leche y Jequetepeque. "
    "El nivel del mar se mantendría por encima de sus valores normales durante los próximos meses. "
    "El próximo Comunicado Oficial se emitirá el viernes 13 de noviembre de 2026. Para más información, consultar el Informe Técnico."
)
ENFEN_HOME_HTML = (
    '<a href="https://enfen.imarpe.gob.pe/2026/10/16/comunicado-oficial-enfen-n18-2026/">18</a>'
    '<a href="https://enfen.imarpe.gob.pe/2026/10/30/comunicado-oficial-enfen-n19-2026/">19</a>'
    '<a href="https://enfen.imarpe.gob.pe/2026/09/28/comunicado-oficial-enfen-n-15-2026-estado-de-sistema-de-alerta-alerta-de-el-nino-costero-2/">17</a>'
)


SENAMHI_HTML = (
    "<table><tr><th>Aviso</th><th>Nro.</th><th>Emisión</th><th>Inicio</th><th>Fin</th><th>Duración</th><th>Nivel</th></tr>"
    '<tr><td><a href="./?p=aviso-meteorologico-vigente&a=2026&b=29348&c=00&d=SENA">PRECIPITACIONES EN LA COSTA NORTE Y SIERRA</a></td>'
    "<td>451 (vigente)</td><td>2026-11-02</td><td>2026-11-04</td><td>2026-11-06</td><td>59 Hrs.</td><td>ROJO</td></tr>"
    '<tr><td><a href="./?p=aviso-meteorologico-detalle&a=2025&b=25462&c=00&d=SENA">INCREMENTO DE TEMPERATURA EN LA SIERRA Y COSTA NORTE</a></td>'
    "<td>469</td><td>2025-12-31</td><td>2026-01-01</td><td>2026-01-02</td><td>47 Hrs.</td><td>AMARILLO</td></tr>"
    '<tr><td>PRECIPITACIONES EN LA SIERRA SUR</td><td>455 (emitido)</td><td>2026-11-04</td><td>2026-11-06</td><td>2026-11-08</td><td>71 Hrs.</td><td>NARANJA</td></tr></table>'
)


class PruebasLectores(unittest.TestCase):
    def test_avisos_senamhi(self):
        avisos = fd.parsear_avisos_senamhi(SENAMHI_HTML)
        self.assertEqual([a["numero"] for a in avisos], [451, 469, 455])
        self.assertEqual((avisos[0]["estado"], avisos[0]["nivel"]), ("vigente", "ROJO"))
        self.assertEqual(avisos[0]["fin"], datetime.date(2026, 11, 6))
        self.assertTrue(avisos[0]["url"].startswith("https://www.senamhi.gob.pe/?p=aviso-meteorologico-vigente"))
        with self.assertRaises(ValueError):
            fd.parsear_avisos_senamhi("<html>sin tabla</html>")

    def test_avisos_costa_norte_vigentes(self):
        avisos = fd.parsear_avisos_senamhi(SENAMHI_HTML)
        norte = fd.avisos_costa_norte({"avisos": avisos}, datetime.date(2026, 11, 5))
        self.assertEqual([a["numero"] for a in norte], [451])             # el 469 ya terminó; el 455 es de la sierra sur
        self.assertEqual(fd.avisos_costa_norte({"avisos": avisos}, datetime.date(2026, 11, 7)), [])
        self.assertEqual(fd.avisos_costa_norte({"_estado": "error"}, datetime.date(2026, 11, 5)), [])
        self.assertEqual(fd.avisos_costa_norte(None, datetime.date(2026, 11, 5)), [])

    def test_oni(self):
        r = fd.parsear_oni("SEAS YR TOTAL ANOM\n  DJF 1950  24.72  -1.53\n  JAS 2026  29.12   2.16\n")
        self.assertEqual(r["trimestre"], "JAS 2026")
        self.assertAlmostEqual(r["anomalia"], 2.16)

    def test_nino_semanal(self):
        r = fd.parsear_nino_semanal(" 14OCT2026     26.2 5.2     29.2 4.2     30.0 3.3     29.7 1.1\n 21OCT2026     26.3 5.2     29.4 4.3     30.1 3.4     29.8 1.1\n")
        self.assertEqual(r["semana"], datetime.date(2026, 10, 21))
        self.assertAlmostEqual(r["nino12"], 5.2)
        self.assertAlmostEqual(r["nino34"], 3.4)

    def test_nino_semanal_con_valores_negativos_pegados(self):
        # La Niña: las anomalías negativas van pegadas al valor anterior; la última fila no se puede saltar
        texto = (" Week          SST SSTA     SST SSTA     SST SSTA     SST SSTA\n"
                 " 02SEP1981     20.6-0.1     24.8-0.1     26.5-0.2     28.3-0.3\n"
                 " 11NOV2026     24.1-1.2     25.0-0.9     26.4-1.1     28.0-0.6\n")
        r = fd.parsear_nino_semanal(texto)
        self.assertEqual(r["semana"], datetime.date(2026, 11, 11))
        self.assertAlmostEqual(r["nino12"], -1.2)
        self.assertAlmostEqual(r["nino34"], -1.1)

    def test_enfen(self):
        r = fd.parsear_enfen(ENFEN_TEXTO, ENFEN_PORTADA)
        self.assertEqual(r["comunicado"], "19-2026")
        self.assertEqual((r["magnitud_nino12"], r["hasta_nino12"]), ("extraordinaria", "febrero de 2027"))
        self.assertEqual((r["magnitud_nino34"], r["hasta_nino34"]), ("muy fuerte", "febrero de 2027"))
        self.assertEqual(r["estado"], "Alerta de El Niño Costero")
        self.assertTrue(r["lluvia_costa_norte_sobre_normal"])
        self.assertEqual(r["rios_caudal_alto"], ["Tumbes", "Chira", "Piura", "La Leche", "Jequetepeque"])
        self.assertTrue(r["nivel_mar_elevado"])
        self.assertEqual(r["proximo"], "viernes 13 de noviembre de 2026")

    def test_enfen_ultimo_comunicado_de_la_portada(self):
        fecha, url = fd.ultimo_comunicado_enfen(ENFEN_HOME_HTML)
        self.assertEqual(fecha, datetime.date(2026, 10, 30))
        self.assertTrue(url.endswith("/2026/10/30/comunicado-oficial-enfen-n19-2026/"))

    def test_enfen_sin_datos_falla_claro(self):
        with self.assertRaises(ValueError):
            fd.parsear_enfen("texto sin las cifras esperadas")

    def test_ciifen(self):
        r = fd.parsear_ciifen(
            "• Durante septiembre... Septiembre de 2026: El Niño continúa fortaleciéndose Condiciones climáticas ENOS "
            "El Niño/La Niña en América Latina • El pronóstico para el trimestre noviembre de 2026 a enero de 2027 prevé el "
            "fortalecimiento de El Niño. Se espera que se mantenga hasta el primer trimestre de 2027. • Otro punto")
        self.assertEqual(r["estado"], "El Niño continúa fortaleciéndose")
        self.assertIn("Se espera que", r["perspectiva"])

    def test_ultimo_boletin_ciifen(self):
        html = ('<a href="https://ciifen.org/wp-content/uploads/2026/09/Boletin_ElNino_LaNina_Septiembre_2026.pdf">'
                '<a href="https://ciifen.org/wp-content/uploads/2026/10/Boletin_ElNino_LaNina_Octubre_2026.pdf">')
        self.assertEqual(fd.ultimo_boletin_ciifen(html)[:2], (2026, 10))

    def test_con_respaldo(self):
        self.assertEqual(fd.con_respaldo("prueba", lambda: {"x": 1})["_estado"], "ok")
        r = fd.con_respaldo("prueba", lambda: 1 / 0)          # falla: devuelve el último bueno
        self.assertEqual((r["_estado"], r["x"]), ("respaldo", 1))
        self.assertEqual(fd.con_respaldo("otra", lambda: 1 / 0)["_estado"], "error")


class PruebasAlmacen(unittest.TestCase):
    def setUp(self):
        almacen.RUTA = Path(tempfile.mkdtemp()) / "t.sqlite"
        fd._ULTIMO_BUENO.clear()

    def test_fuente_conserva_fechas_y_clave(self):
        datos = {"emision": datetime.date(2026, 10, 1), "cantones": {"A": [(datetime.date(2026, 11, 1), 3.5)]}, "_estado": "ok"}
        almacen.guardar_fuente("gfs", datos, clave="abc")
        leido, edad = almacen.leer_fuente("gfs", "abc")
        self.assertEqual(leido["emision"], datetime.date(2026, 10, 1))
        self.assertEqual(leido["cantones"]["A"][0], [datetime.date(2026, 11, 1), 3.5])   # tupla -> lista, fecha intacta
        self.assertNotIn("_estado", leido)
        self.assertIsInstance(leido["_descargado"], datetime.datetime)
        self.assertGreaterEqual(edad, 0)
        self.assertEqual(almacen.leer_fuente("gfs", "otra-clave"), (None, None))        # catálogo distinto: no sirve
        self.assertEqual(almacen.leer_fuente("no_existe"), (None, None))

    def test_series_enso(self):
        almacen.guardar_series_enso([(1998, "DJF", 2.2), (2026, "JAS", 2.16)], [("2026-10-21", 5.2, 4.3, 3.4, 1.1)])
        almacen.guardar_series_enso([(2026, "JAS", 2.16)], [("2026-10-21", 5.2, 4.3, 3.4, 1.1)])   # repetir no duplica
        self.assertEqual([f["anio"] for f in almacen.leer_oni(1990)], [1998, 2026])
        self.assertEqual(len(almacen.leer_nino_semanal()), 1)

    def test_historial_riesgo(self):
        d1 = datetime.datetime(2026, 11, 1, 12, tzinfo=datetime.timezone.utc)
        d2 = d1 + datetime.timedelta(hours=3)
        reg = lambda r: [{"canton": "Piura", "fecha": datetime.date(2026, 11, 2), "riesgo": r, "alerta": "BAJO",
                          "lluvia_mm": 1.0, "mar_idx": 0.0, "horizonte": "GFS · día"},
                         {"canton": "Piura", "fecha": datetime.date(2026, 11, 3), "riesgo": r / 2, "alerta": "BAJO",
                          "lluvia_mm": 1.0, "mar_idx": 0.0, "horizonte": "GFS · día"}]
        self.assertTrue(almacen.guardar_riesgo(d1, reg(0.4)))
        self.assertFalse(almacen.guardar_riesgo(d1, reg(0.9)))        # misma descarga: no se repite
        self.assertTrue(almacen.guardar_riesgo(d2, reg(0.6)))
        evo = almacen.leer_evolucion_riesgo()
        self.assertEqual([round(e["riesgo_max"], 2) for e in evo], [0.4, 0.6])
        self.assertEqual(almacen.resumen()["fotos_riesgo"], 2)

    def test_asegurar_series_descarga_solo_si_el_almacen_esta_vacio(self):
        oni = "SEAS YR TOTAL ANOM\n  JAS 2026  29.12   2.16\n"
        nino = " 21OCT2026     26.3 5.2     29.4 4.3     30.1 3.4     29.8 1.1\n"
        pedidos = []

        class R:
            def __init__(self, texto):
                self.text = texto

        original = fd._get
        try:
            fd._get = lambda url, **kw: pedidos.append(url) or R(oni if url == fd.URL_ONI else nino)
            fd.asegurar_series_enso()
            self.assertEqual(len(pedidos), 2)
            self.assertEqual(almacen.resumen()["oni"], 1)
            fd.asegurar_series_enso()                       # ya hay series: no vuelve a descargar
            self.assertEqual(len(pedidos), 2)
        finally:
            fd._get = original

    def test_evolucion_con_marcas_de_tiempo_de_formatos_mixtos(self):
        """Falló en la nube: marcas con y sin microsegundos hacían fallar pd.to_datetime y rompían la página."""
        import pandas as pd
        reg = [{"canton": "Piura", "fecha": datetime.date(2026, 11, 2), "riesgo": 0.5, "alerta": "BAJO",
                "lluvia_mm": 1.0, "mar_idx": 0.0, "horizonte": "GFS · día"}]
        almacen.guardar_riesgo(datetime.datetime(2026, 11, 1, 20, 49, 32, 123456, tzinfo=datetime.timezone.utc), reg)
        almacen.guardar_riesgo(datetime.datetime(2026, 11, 1, 23, 51, 21, tzinfo=datetime.timezone.utc), reg)
        evo = pd.DataFrame(almacen.leer_evolucion_riesgo())
        momentos = pd.to_datetime(evo["descarga_utc"], format="ISO8601", utc=True).dt.tz_convert("America/Lima")
        self.assertEqual(len(momentos), 2)
        self.assertEqual(momentos.iloc[1].hour, 18)                       # 23:51 UTC = 18:51 en Perú

    def test_respaldo_desde_sqlite_si_se_pierde_la_memoria(self):
        fd.con_respaldo("enfen_prueba", lambda: {"comunicado": "19-2026"})
        fd._ULTIMO_BUENO.clear()                                       # reinicio del proceso
        r = fd.con_respaldo("enfen_prueba", lambda: 1 / 0)
        self.assertEqual((r["_estado"], r["comunicado"]), ("respaldo", "19-2026"))

    def test_arranque_rapido_no_descarga_si_hay_dato_reciente(self):
        llamadas = []
        originales = (fd.obtener_noaa_enso, fd.obtener_gfs, fd.obtener_cfsv2, fd.obtener_enfen, fd.obtener_senamhi, fd.obtener_ciifen)
        try:
            fd.obtener_noaa_enso = lambda: llamadas.append("noaa") or {"oni": {"anomalia": 2.0}, "semanal": {"nino12": 4.0}}
            fd.obtener_gfs = lambda c: llamadas.append("gfs") or {"cantones": {}}
            fd.obtener_cfsv2 = lambda c: llamadas.append("cfs") or {"cantones": {}}
            fd.obtener_senamhi = lambda: llamadas.append("senamhi") or {"avisos": []}
            fd.obtener_enfen = lambda: llamadas.append("enfen") or {"magnitud_nino12": "fuerte"}
            fd.obtener_ciifen = lambda: llamadas.append("ciifen") or {"estado": "x"}
            primera = fd.cargar_todo(mr.CANTONES)
            self.assertEqual(sorted(llamadas), ["cfs", "ciifen", "enfen", "gfs", "noaa", "senamhi"])
            llamadas.clear()
            segunda = fd.cargar_todo(mr.CANTONES)                      # todo reciente en SQLite: cero descargas
            self.assertEqual(llamadas, [])
            self.assertEqual(segunda["enfen"]["_origen"], "sqlite")
            self.assertEqual(segunda["enfen"]["magnitud_nino12"], "fuerte")
            fd.cargar_todo(mr.CANTONES, max_edad_s=-1)                 # caducado: vuelve a descargar
            self.assertEqual(len(llamadas), 6)
            self.assertEqual(primera["_cargado"], segunda["_cargado"])   # mismo momento: no se duplica la «foto» del riesgo
        finally:
            (fd.obtener_noaa_enso, fd.obtener_gfs, fd.obtener_cfsv2, fd.obtener_enfen, fd.obtener_senamhi, fd.obtener_ciifen) = originales


class PruebasSinDescargaAlEntrar(unittest.TestCase):
    """Entrar a la aplicación no descarga nada de internet: solo el botón «Actualizar datos» lo hace."""

    def setUp(self):
        self.llamadas = []
        self.originales = (fd.obtener_noaa_enso, fd.obtener_gfs, fd.obtener_cfsv2, fd.obtener_enfen, fd.obtener_senamhi, fd.obtener_ciifen)
        fd.obtener_noaa_enso = lambda: self.llamadas.append("noaa") or {"oni": {"anomalia": 2.0}, "semanal": {"nino12": 4.0}}
        fd.obtener_gfs = lambda c: self.llamadas.append("gfs") or {"cantones": {}}
        fd.obtener_cfsv2 = lambda c: self.llamadas.append("cfs") or {"cantones": {}}
        fd.obtener_senamhi = lambda: self.llamadas.append("senamhi") or {"avisos": []}
        fd.obtener_enfen = lambda: self.llamadas.append("enfen") or {"magnitud_nino12": "fuerte"}
        fd.obtener_ciifen = lambda: self.llamadas.append("ciifen") or {"estado": "x"}
        self.ruta = almacen.RUTA
        almacen.RUTA = Path(tempfile.mkdtemp()) / "entrar.sqlite"          # base limpia para cada prueba

    def tearDown(self):
        almacen.RUTA = self.ruta
        (fd.obtener_noaa_enso, fd.obtener_gfs, fd.obtener_cfsv2, fd.obtener_enfen, fd.obtener_senamhi, fd.obtener_ciifen) = self.originales

    def _guardar_antiguo(self, horas):
        viejo = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=horas)
        clave = fd._clave_cantones(mr.CANTONES)
        for nombre, datos, k in (("noaa", {"oni": {"anomalia": 1.0}, "semanal": {"nino12": 2.0}}, None), ("gfs", {"cantones": {}}, clave),
                                 ("cfsv2", {"cantones": {}}, clave), ("enfen", {"magnitud_nino12": "debil"}, None), ("senamhi", {"avisos": []}, None), ("ciifen", {"estado": "y"}, None)):
            almacen.guardar_fuente(nombre, datos, k, viejo)

    def test_dato_muy_antiguo_no_dispara_descarga(self):
        self._guardar_antiguo(horas=200)                       # más de una semana: antes habría descargado
        datos = fd.cargar_todo(mr.CANTONES)
        self.assertEqual(self.llamadas, [])
        self.assertEqual(datos["enfen"]["magnitud_nino12"], "debil")
        self.assertEqual(datos["enfen"]["_origen"], "sqlite")

    def test_refrescar_descarga_todo(self):
        self._guardar_antiguo(horas=1)
        datos = fd.cargar_todo(mr.CANTONES, refrescar=True)
        self.assertEqual(sorted(self.llamadas), ["cfs", "ciifen", "enfen", "gfs", "noaa", "senamhi"])
        self.assertEqual(datos["enfen"]["magnitud_nino12"], "fuerte")

    def test_actualizar_todo_no_se_solapa(self):
        fd._BLOQUEO_DESCARGA.acquire()                         # otro usuario ya está descargando
        try:
            self.assertIsNone(fd.actualizar_todo(mr.CANTONES))
            self.assertTrue(fd.descarga_en_curso())
        finally:
            fd._BLOQUEO_DESCARGA.release()
        self.assertIsNotNone(fd.actualizar_todo(mr.CANTONES))  # libre: descarga
        self.assertFalse(fd.descarga_en_curso())

    def test_semilla_evita_la_descarga_con_el_almacen_vacio(self):
        self._guardar_antiguo(horas=5)
        almacen.guardar_series_enso([(2026, "JAS", 2.1)], [("2026-10-28", 1.0, 2.0, 3.0, 4.0)])
        semilla = Path(tempfile.mkdtemp()) / "semilla.json"
        almacen.exportar_semilla(semilla)
        almacen.RUTA = Path(tempfile.mkdtemp()) / "servidor_nuevo.sqlite"    # servidor recién iniciado: almacén vacío
        self.assertEqual(almacen.importar_semilla(semilla), 6)
        self.assertEqual(almacen.importar_semilla(semilla), 0)               # no pisa ni duplica
        self.assertEqual(almacen.resumen()["oni"], 1)
        almacen.SEMILLA, previa = semilla, almacen.SEMILLA
        try:
            almacen.RUTA = Path(tempfile.mkdtemp()) / "otro_servidor.sqlite"
            datos = fd.cargar_todo(mr.CANTONES)                              # importa la semilla por su cuenta
        finally:
            almacen.SEMILLA = previa
        self.assertEqual(self.llamadas, [])
        self.assertEqual(datos["noaa"]["oni"]["anomalia"], 1.0)

    def test_semilla_del_repositorio_es_valida(self):
        import json
        with open(Path(__file__).with_name("datos_semilla.json"), encoding="utf-8") as f:
            semilla = json.load(f)
        self.assertEqual({x["nombre"] for x in semilla["fuente"]}, {"noaa", "gfs", "cfsv2", "enfen", "senamhi", "ciifen"})
        self.assertGreater(len(semilla["oni"]), 800)
        self.assertGreater(len(semilla["nino_semanal"]), 2000)


class PruebasActualizacionDiaria(unittest.TestCase):
    def setUp(self):
        self.ruta = almacen.RUTA
        almacen.RUTA = Path(tempfile.mkdtemp()) / "diaria.sqlite"

    def tearDown(self):
        almacen.RUTA = self.ruta

    def test_toca_actualizar_solo_al_cambiar_el_dia_en_ecuador(self):
        utc = datetime.timezone.utc
        ayer = datetime.datetime(2026, 11, 2, 20, 0, tzinfo=utc)                  # 15:00 del 2 en Perú
        self.assertFalse(fd.toca_actualizar(ayer, datetime.datetime(2026, 11, 3, 9, 0, tzinfo=utc)))    # 04:00 del 3: aún no
        self.assertTrue(fd.toca_actualizar(ayer, datetime.datetime(2026, 11, 3, 10, 30, tzinfo=utc)))   # 05:30 del 3: toca
        self.assertFalse(fd.toca_actualizar(datetime.datetime(2026, 11, 3, 10, 40, tzinfo=utc),
                                            datetime.datetime(2026, 11, 3, 22, 0, tzinfo=utc)))        # ya es de hoy
        self.assertTrue(fd.toca_actualizar(None, datetime.datetime(2026, 11, 3, 12, 0, tzinfo=utc)))     # sin datos y de día
        # 03:00 UTC del 3 = 22:00 del 2 en Perú: sigue siendo el mismo día
        self.assertFalse(fd.toca_actualizar(ayer, datetime.datetime(2026, 11, 3, 3, 0, tzinfo=utc)))

    def test_semilla_mas_nueva_reemplaza_y_la_mas_vieja_no(self):
        semilla = Path(tempfile.mkdtemp()) / "semilla.json"
        viejo = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc)
        nuevo = datetime.datetime(2026, 11, 3, tzinfo=datetime.timezone.utc)
        almacen.guardar_fuente("enfen", {"magnitud_nino12": "debil"}, None, nuevo)
        almacen.exportar_semilla(semilla)                                     # semilla con el dato NUEVO (10 cm)
        almacen.guardar_fuente("enfen", {"magnitud_nino12": "otra"}, None, nuevo + datetime.timedelta(days=1))
        self.assertEqual(almacen.importar_semilla(semilla), 0)               # el almacén tiene algo más nuevo: no se pisa
        self.assertEqual(almacen.leer_fuente("enfen")[0]["magnitud_nino12"], "otra")
        almacen.guardar_fuente("enfen", {"magnitud_nino12": "otra2"}, None, viejo)  # el almacén tiene algo más viejo
        self.assertEqual(almacen.importar_semilla(semilla), 1)
        self.assertEqual(almacen.leer_fuente("enfen")[0]["magnitud_nino12"], "debil")

    def test_hilo_diario_actualiza_cuando_cambia_el_dia(self):
        llamadas, avisos = [], []
        originales = (fd.actualizar_todo, fd._ultima_descarga, fd.toca_actualizar, fd._ACTUALIZADOR["hilo"])
        try:
            fd._ACTUALIZADOR["hilo"] = None
            fd._ultima_descarga = lambda c: None
            fd.toca_actualizar = lambda ultima, ahora=None: True
            fd.actualizar_todo = lambda c: llamadas.append(1) or {"ok": 1}
            hilo = fd.iniciar_actualizador_diario(mr.CANTONES, al_actualizar=lambda: avisos.append(1), cada_s=0.05, espera_inicial_s=0)
            self.assertIs(fd.iniciar_actualizador_diario(mr.CANTONES), hilo)    # una sola vez por proceso
            import time
            for _ in range(60):
                if llamadas and avisos:
                    break
                time.sleep(0.05)
            hilo.parar.set()
            hilo.join(2)
            self.assertTrue(llamadas and avisos)
        finally:
            (fd.actualizar_todo, fd._ultima_descarga, fd.toca_actualizar, fd._ACTUALIZADOR["hilo"]) = originales

    def test_actualizar_semilla_conserva_la_semilla_si_falla_la_lluvia(self):
        import subprocess, sys, os
        raiz = Path(__file__).parent
        copia = Path(tempfile.mkdtemp())
        for f in ("actualizar_semilla.py", "almacen.py", "fuentes_datos.py", "modelo_riesgo.py", "distritos_norte.py", "climatologia.json", "datos_semilla.json"):
            if (raiz / f).exists():                                             # climatologia.json puede faltar en un clon nuevo
                (copia / f).write_bytes((raiz / f).read_bytes())
        antes = (copia / "datos_semilla.json").read_bytes()
        sitecustomize = copia / "sitecustomize.py"                              # sin red: toda petición HTTP falla
        sitecustomize.write_text("import requests\ndef _f(*a, **k):\n    raise RuntimeError('sin red')\nrequests.get = _f\n")
        r = subprocess.run([sys.executable, "actualizar_semilla.py"], cwd=copia, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONPATH": str(copia)})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)                 # la lluvia sale «respaldo», no «ok»: error
        self.assertIn("respaldo", r.stdout)
        self.assertEqual((copia / "datos_semilla.json").read_bytes(), antes)


class PruebasBuscadorDistritos(unittest.TestCase):
    def test_datos_del_buscador(self):
        lista = dn.DISTRITOS_NORTE
        self.assertEqual(len(lista), 199)
        self.assertEqual({c["p"].split(", ")[1] for c in lista}, {"Tumbes", "Piura", "Lambayeque", "La Libertad"})
        for c in lista:
            self.assertTrue(c["r"], c["n"])                              # todo distrito tiene contorno
            oeste, sur, este, norte = c["b"]
            self.assertTrue(oeste <= c["c"][0] <= este and sur <= c["c"][1] <= norte, c["n"])
            self.assertTrue(-82 < oeste < este < -76.5 and -9.2 < sur < norte < -3, c["n"])      # dentro de la costa norte
            for anillo in c["r"]:
                self.assertEqual(len(anillo) % 2, 0)                     # anillo plano [lon, lat, lon, lat, ...]
                self.assertGreaterEqual(len(anillo), 6)
        self.assertEqual(len({c["u"] for c in lista}), 199)              # ubigeos únicos

    def test_distritos_del_modelo_existen_en_el_buscador(self):
        nombres = {c["n"] for c in dn.DISTRITOS_NORTE}
        for c in mr.CANTONES:
            self.assertIn(c["canton"], nombres, c["canton"])
            self.assertIn(c["ubigeo"], {d["u"] for d in dn.DISTRITOS_NORTE if d["n"] == c["canton"]})

    def test_distritos_del_modelo_dentro_de_su_distrito(self):
        por = {d["u"]: d for d in dn.DISTRITOS_NORTE}
        for c in mr.CANTONES:
            oeste, sur, este, norte = por[c["ubigeo"]]["b"]
            self.assertTrue(oeste <= c["lon"] <= este and sur <= c["lat"] <= norte, c["canton"])


class PruebasFormatoContornos(unittest.TestCase):
    def test_anillo_plano_acepta_pares_y_planos(self):
        import re
        fuente = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
        cuerpo = re.search(r"def _anillo_plano\(anillo\):.*?\n\n\n", fuente, re.S).group(0)
        ns = {}
        exec(cuerpo, ns)                                                 # solo la función pura, sin cargar Streamlit
        plano, pares = [1.0, 2.0, 3.0, 4.0], [[1.0, 2.0], [3.0, 4.0]]
        self.assertEqual(ns["_anillo_plano"](plano), plano)
        self.assertEqual(ns["_anillo_plano"](pares), plano)
        self.assertEqual(ns["_anillo_plano"]([]), [])


class PruebasModelo(unittest.TestCase):
    def cantón(self, nombre):
        return next(c for c in mr.CANTONES if c["canton"] == nombre)

    def test_indice_enso(self):
        self.assertEqual(mr.indice_enso(None, None), 0.0)
        self.assertEqual(mr.indice_enso(2.5, 6.0), 1.0)
        self.assertAlmostEqual(mr.indice_enso(1.0, 2.0), 0.5)

    def test_niveles(self):
        self.assertEqual(mr.nivel_alerta(0.9, "inundacion"), "CRÍTICO")
        self.assertEqual(mr.nivel_alerta(0.75, "inundacion"), "ALTO RIESGO")
        self.assertEqual(mr.nivel_alerta(0.55, "inundacion"), "ALERTA AMARILLA")
        self.assertEqual(mr.nivel_alerta(0.45, "deslave"), "MEDIO (HUAICO)")
        self.assertEqual(mr.nivel_alerta(0.45, "inundacion"), "BAJO")

    def test_mas_lluvia_mas_riesgo(self):
        c = self.cantón("Piura")
        self.assertLess(mr.calcular_riesgo(c, 0.1, 0.5, 1.0, 0.5), mr.calcular_riesgo(c, 0.8, 0.5, 1.0, 0.5))

    def test_enso_amplifica_y_tope(self):
        c = self.cantón("Tumbes")
        self.assertLess(mr.calcular_riesgo(c, 0.4, 0, 0, 0.0), mr.calcular_riesgo(c, 0.4, 0, 0, 1.0))
        peor = mr.calcular_riesgo(c, 1.0, 1.0, 1.0, 1.0)      # todo saturado: nunca > 1
        self.assertTrue(0.85 <= peor <= 1.0, peor)

    def test_mar_solo_en_costeros(self):
        enfen = {"_estado": "ok", "nivel_mar_elevado": True}
        self.assertEqual(mr.s_mar_enfen(self.cantón("Paita"), enfen), mr.MAR_ELEVADO)
        self.assertEqual(mr.s_mar_enfen(self.cantón("Piura"), enfen), 0.0)             # no costero
        self.assertEqual(mr.s_mar_enfen(self.cantón("Paita"), {"_estado": "ok", "nivel_mar_elevado": False}), 0.0)
        self.assertEqual(mr.s_mar_enfen(self.cantón("Paita"), {"_estado": "error"}), 0.0)

    def test_cauce_segun_los_rios_del_enfen(self):
        enfen = {"_estado": "ok", "rios_caudal_alto": ["Tumbes", "Chira", "Piura", "La Leche", "Jequetepeque"]}
        self.assertEqual(mr.s_cauce_enfen(self.cantón("Sullana"), enfen), 1.0)         # río Chira
        self.assertEqual(mr.s_cauce_enfen(self.cantón("Mochumi"), enfen), 1.0)         # río La Leche
        self.assertEqual(mr.s_cauce_enfen(self.cantón("Trujillo"), enfen), 0.0)        # río Moche: no figura
        self.assertEqual(mr.s_cauce_enfen(self.cantón("Ayabaca"), enfen), 0.0)         # sin río asignado
        self.assertEqual(mr.s_cauce_enfen(self.cantón("Sullana"), {"_estado": "error"}), 0.0)
        # sin tildes ni mayúsculas
        self.assertEqual(mr.s_cauce_enfen(self.cantón("Sullana"), {"rios_caudal_alto": ["CHIRÁ"]}), 1.0)

    def test_construir_registros(self):
        hoy = datetime.date(2026, 11, 1)
        datos = {
            "noaa": {"oni": {"anomalia": 2.0}, "semanal": {"nino12": 4.0}},
            "enfen": {"_estado": "ok", "nivel_mar_elevado": True, "rios_caudal_alto": ["Piura"]},
            "gfs": {"cantones": {c["canton"]: [(hoy + datetime.timedelta(days=i), 10.0) for i in range(16)] for c in mr.CANTONES}},
            "cfsv2": {"cantones": {c["canton"]: [(datetime.date(2026, m, 1), 300.0) for m in (10, 11, 12)] for c in mr.CANTONES}},
        }
        regs, enso = mr.construir_registros(datos, hoy=hoy)
        self.assertEqual(enso, 1.0)
        horizontes = {r["horizonte"] for r in regs}
        self.assertEqual(horizontes, {"GFS · día", "CFSv2 · mes"})
        # solo entra de CFSv2 el mes posterior al horizonte diario (15-dic); 15-oct es pasado y 15-nov cae dentro de GFS
        self.assertEqual({r["fecha"] for r in regs if r["horizonte"] == "CFSv2 · mes"}, {datetime.date(2026, 12, 15)})
        self.assertTrue(all(0.0 <= r["riesgo"] <= 1.0 for r in regs))
        piura = next(r for r in regs if r["canton"] == "Piura")
        self.assertEqual((piura["mar_idx"], piura["cauce_idx"]), (0.0, 1.0))           # no costero; río Piura en la lista
        paita = next(r for r in regs if r["canton"] == "Paita")
        self.assertEqual(paita["mar_idx"], mr.MAR_ELEVADO)

    def test_lluvia_local_normaliza_por_distrito(self):
        clima = {"cantones": {
            "Tumbes": {"dia_p50": 6.0, "dia_p99": 90.0, "d3_p50": 18.0, "d3_p99": 170.0,
                       "mes": {"3": {"p50": 100.0, "p95": 400.0}}},
            "Sechura": {"dia_p50": 1.0, "dia_p99": 20.0, "d3_p50": 3.0, "d3_p99": 30.0, "mes": {}},
        }}
        tum, sec = self.cantón("Tumbes"), self.cantón("Sechura")
        # 20 mm en un día: más extremo en Sechura (zona desértica) que en Tumbes
        self.assertGreater(mr.s_lluvia_dia(sec, 20, 20, clima), mr.s_lluvia_dia(tum, 20, 20, clima))
        self.assertEqual(mr.s_lluvia_dia(tum, 3, 3, clima), 0.0)          # lluvia típica -> 0
        self.assertEqual(mr.s_lluvia_dia(tum, 120, 120, clima), 1.0)      # por encima del P99 -> 1
        # mediana 100 y P95 400: el P95 vale 0,5 (saturación en 100 + 2·300 = 700 mm)
        self.assertAlmostEqual(mr.s_lluvia_mes(tum, 3, 400, clima), 0.5)
        self.assertEqual(mr.s_lluvia_mes(tum, 3, 700, clima), 1.0)
        self.assertEqual(mr.s_lluvia_mes(tum, 3, 80, clima), 0.0)
        # sin estadística del mes -> umbral de respaldo
        self.assertAlmostEqual(mr.s_lluvia_mes(sec, 3, mr.LLUVIA_MES_MM / 2, clima), 0.5)
        # sin climatología -> umbrales de respaldo
        self.assertAlmostEqual(mr.s_lluvia_dia(tum, mr.LLUVIA_DIA_MM / 2, 0, {}), 0.5)

    def test_sin_lluvia_el_resto_no_dispara_alerta(self):
        # techo con ENSO máximo, mar elevado y río en alerta pero sin lluvia: nunca pasa de BAJO
        for c in mr.CANTONES:
            r = mr.calcular_riesgo(c, 0.0, mr.MAR_ELEVADO if c["costero"] else 0.0, 1.0, 1.0)
            self.assertEqual(mr.nivel_alerta(r, c["amenaza"]) in ("BAJO",), True, (c["canton"], r))

    def test_catalogo_del_piloto(self):
        self.assertEqual(len(mr.CANTONES), 30)
        self.assertEqual({c["provincia"] for c in mr.CANTONES}, {"Tumbes", "Piura", "Lambayeque", "La Libertad"})
        self.assertEqual(len({c["canton"] for c in mr.CANTONES}), 30)
        self.assertTrue(all(-9 < c["lat"] < -3 and -82 < c["lon"] < -77 for c in mr.CANTONES))
        self.assertTrue(all(sum(w.values()) <= 1.0 + 1e-9 for w in mr.PESOS.values()))

    def test_sin_fuentes_no_rompe(self):
        regs, enso = mr.construir_registros({}, hoy=datetime.date(2026, 11, 1))
        self.assertEqual((regs, enso), ([], 0.0))


class TestSusceptibilidad(unittest.TestCase):
    """La capa de susceptibilidad a inundación (generar_susceptibilidad.py) y su archivo de metadatos."""

    def test_json_coherente(self):
        import json
        with open(os.path.join(os.path.dirname(__file__), "susceptibilidad_inundacion.json"), encoding="utf-8") as f:
            j = json.load(f)
        e = j["extension"]
        self.assertEqual((e["oeste"], e["sur"], e["este"], e["norte"]), (-81.4, -9.0, -77.2, -3.3))
        pct = j["porcentaje_de_tierra_por_clase"]
        self.assertAlmostEqual(sum(pct.values()), 100.0, delta=0.5)
        self.assertGreater(pct["baja"], pct["media"])
        self.assertLess(max(j["tamano_px"]), 4096)          # límite de textura de WebGL

    def test_png_tamano_y_colores(self):
        from PIL import Image
        with Image.open(os.path.join(os.path.dirname(__file__), "susceptibilidad_inundacion.png")) as im:
            self.assertEqual(im.mode, "RGBA")
            self.assertLess(max(im.size), 4096)

    def test_calculo_sintetico(self):
        try:
            import numpy as np
            import generar_susceptibilidad as g
        except ImportError:
            self.skipTest("requiere rasterio, scipy y numpy (solo para regenerar la capa)")
        dem = np.zeros((120, 120), np.float32)
        dem[:, :20] = 0.0                                  # mar al oeste
        dem[:, 20:60] = 2.0                                # llanura baja junto al mar
        dem[:, 60:] = 400.0                                # tierras altas
        clases, indice, tierra = g.calcular(dem)
        self.assertEqual(clases[60, 5], 0)                 # mar
        self.assertEqual(clases[60, 30], 3)                # llanura costera: alta
        self.assertEqual(clases[60, 100], 1)               # altura: baja


if __name__ == "__main__":
    unittest.main()
