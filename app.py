import base64
import calendar
import datetime
import html
import importlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

import almacen
import fuentes_datos
import modelo_riesgo
from distritos_norte import DISTRITOS_NORTE
from peru_perfil import PERU_PERFIL

# Streamlit Cloud no recarga los módulos propios cuando se actualiza el repositorio (solo vuelve a ejecutar app.py),
# y la app seguía usando el modelo anterior. Se recargan aquí; se conserva el «último dato bueno» de las fuentes.
_ultimo_bueno = dict(fuentes_datos._ULTIMO_BUENO)
importlib.reload(almacen)
importlib.reload(fuentes_datos)
fuentes_datos._ULTIMO_BUENO.update(_ultimo_bueno)
importlib.reload(modelo_riesgo)
from modelo_riesgo import CANTONES, PESOS, UMBRALES, ENSO_MAX_AMPLIFICACION, construir_registros  # noqa: E402

# Se fija 1.105.2: desde 1.113 los workers son módulos ES y el navegador los bloquea
# al cargarse desde un CDN dentro del iframe de Streamlit (el globo queda vacío).
CESIUM_VERSION = "1.105.2"
CESIUM_CDN = f"https://cdn.jsdelivr.net/npm/cesium@{CESIUM_VERSION}/Build/Cesium"

COLORES_ALERTA = {
    "CRÍTICO": "#8B0000",
    "ALTO RIESGO": "#FF4D4D",
    "ALERTA AMARILLA": "#FFCC00",
    "MEDIO (HUAICO)": "#FFA500",
    "BAJO": "#16A34A",
}


# ==========================================
# 0. FUNCIONES DE SOPORTE
# ==========================================
def estilo_filas_alertas(row):
    """Aplica colores condicionales a las celdas según el nivel de alerta."""
    val_alerta = row['alerta']
    if val_alerta == "CRÍTICO":
        bg_geo = 'background-color: #FEE2E2; color: #991B1B; font-weight: bold;'
        bg_alerta = 'background-color: #8B0000; color: white; font-weight: bold;'
    elif val_alerta == "ALTO RIESGO":
        bg_geo = 'background-color: #FFEDD5; color: #9A3412; font-weight: bold;'
        bg_alerta = 'background-color: #FF4D4D; color: white; font-weight: bold;'
    elif val_alerta == "ALERTA AMARILLA":
        bg_geo = 'background-color: #FEF9C3; color: #713F12; font-weight: bold;'
        bg_alerta = 'background-color: #FFCC00; color: black; font-weight: bold;'
    elif val_alerta == "MEDIO (HUAICO)":
        bg_geo = 'background-color: #F3F4F6; color: #374151; font-weight: bold;'
        bg_alerta = 'background-color: #FFA500; color: white; font-weight: bold;'
    elif val_alerta == "BAJO":
        bg_geo = 'background-color: #DCFCE7; color: #166534; font-weight: bold;'
        bg_alerta = 'background-color: #16A34A; color: white; font-weight: bold;'
    else:
        return [''] * 7
    # columnas: fecha, canton, provincia, lluvia_mm, mar_idx, alerta, horizonte
    return ['', bg_geo, bg_geo, '', '', bg_alerta, '']


def sumar_un_mes(fecha):
    """Devuelve la misma fecha un mes después (ajusta el día si el mes siguiente es más corto)."""
    anio = fecha.year + fecha.month // 12
    mes = fecha.month % 12 + 1
    dia = min(fecha.day, calendar.monthrange(anio, mes)[1])
    return datetime.date(anio, mes, dia)


def fecha_actual_peru():
    """Fecha de hoy en hora de Perú (el servidor de Streamlit Cloud trabaja en UTC)."""
    try:
        return datetime.datetime.now(ZoneInfo("America/Lima")).date()
    except Exception:
        return datetime.date.today()


# ==========================================
# 1. VISOR CESIUM (2D / 3D)
# ==========================================
# Plantilla HTML plana (sin f-string) para no chocar con las llaves de JS/CSS.
# Sin Cesium ion: ninguna capa necesita token. Topología = OSM, imagen = Esri.
@st.cache_resource
def _susceptibilidad_uri():
    """Capa de susceptibilidad a inundación como data URI (el repositorio es privado: una URL raw de GitHub daría 404).

    Se guarda en caché de recurso: la imagen (~220 KB) se lee y codifica una sola vez por proceso (no se necesita acceso a GitHub al ejecutar)."""
    try:
        datos = (Path(__file__).with_name("susceptibilidad_inundacion.png")).read_bytes()
    except OSError:
        return ""
    return "data:image/png;base64," + base64.b64encode(datos).decode("ascii")


CESIUM_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>window.CESIUM_BASE_URL = "__CDN__/";</script>
<script src="__CDN__/Cesium.js"></script>
<link href="__CDN__/Widgets/widgets.css" rel="stylesheet">
<style>
  html, body, #cesiumContainer { width: 100%; height: 100%; margin: 0; padding: 0; overflow: hidden;
    font-family: -apple-system, 'Segoe UI', Arial, sans-serif; background: #0A192F; }
  .toolbar { position: absolute; top: 10px; left: 10px; right: 90px; z-index: 10; display: flex; gap: 10px;
    flex-wrap: wrap; align-items: flex-start; }
  .grupo { display: flex; align-items: center; background: rgba(29,53,87,.95); border-radius: 8px;
    overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,.45); }
  .grupo button { border: 0; background: transparent; color: #CBD5E1; padding: 8px 14px;
    font-size: 13px; font-weight: 600; cursor: pointer; }
  .grupo button + button { border-left: 1px solid rgba(255,255,255,.15); }
  .grupo button:hover { background: rgba(255,255,255,.1); }
  .grupo button.activo { background: #2563EB; color: #fff; }
  .grupo label { color: #94A3B8; font-size: 12px; padding: 0 4px 0 12px; }
  .grupo select { border: 0; background: transparent; color: #fff; font-size: 13px; font-weight: 600;
    padding: 8px 10px 8px 4px; cursor: pointer; outline: none; }
  .grupo select option { color: #000; }
  .grupo input[type=date] { border: 0; background: transparent; color: #fff; font-size: 13px;
    font-weight: 600; padding: 7px 10px 7px 4px; outline: none; color-scheme: dark; }
  #estado { color: #fff; font-size: 12px; background: rgba(29,53,87,.92); padding: 6px 10px;
    border-radius: 8px; display: none; }
  .leyenda { position: absolute; bottom: 52px; z-index: 10; background: rgba(29,53,87,.92);
    color: #fff; font-size: 12px; padding: 8px 12px; border-radius: 8px; line-height: 1.7; }
  /* Mapas base apilados: Esri arriba, OSM debajo, del mismo ancho */
  #grpBase { flex-direction: column; align-items: stretch; }
  #grpBase button { text-align: left; }
  #grpBase button + button { border-left: 0; border-top: 1px solid rgba(255,255,255,.15); }
  /* Controles de vista: columna pegada al extremo derecho del visor, bajo el botón de pantalla completa */
  #grpVista { position: absolute; right: 10px; top: 46px; z-index: 10; flex-direction: column; align-items: stretch; }
  #grpVista label { padding: 4px 0 3px; text-align: center; }
  #grpVista button { padding: 5px 0; min-width: 40px; font-size: 14px; }
  .cesium-infoBox { right: 58px !important; }   /* la ficha de información no tapa la columna */
  #grpVista button + button { border-left: 0; border-top: 1px solid rgba(255,255,255,.15); }
  /* Controles del pronóstico (▶ + línea de tiempo): barra al pie del mapa, centrada */
  #boxTiempo { display: none; position: absolute; left: 50%; bottom: 8px; transform: translateX(-50%);
    z-index: 12; width: min(680px, calc(100% - 480px)); min-width: 280px; align-items: center; padding: 0 8px 0 0; }
  /* Ventanas estrechas: la barra sube sobre los créditos de Cesium y ocupa casi todo el ancho */
  @media (max-width: 760px) {
    #boxTiempo { bottom: 58px; width: calc(100% - 20px); min-width: 0; }
    .leyenda { bottom: 102px; }
  }
  #btnPlay { flex: 0 0 auto; width: 34px; height: 34px; border: 0; background: #2563EB; color: #fff; cursor: pointer;
    font-size: 14px; line-height: 1; padding: 0; margin-right: 8px; }
  #btnPlay:hover { background: #1D4ED8; }
  #slotTimeline { position: relative; flex: 1 1 auto; min-width: 0; height: 27px; }
  #slotTimeline .cesium-viewer-timelineContainer { position: relative !important; left: 0 !important;
    right: auto !important; bottom: auto !important; width: 100% !important; height: 27px; }
  /* Buscador «Buscar Distrito»: botón arriba a la derecha; el panel se abre debajo y por encima de la columna Vista */
  #grpBuscar { position: absolute; right: 10px; top: 10px; z-index: 14; }
  #grpBuscar button { padding: 8px 14px; }
  #panelBuscar { display: none; position: absolute; right: 10px; top: 48px; z-index: 15; width: 292px; flex-direction: column;
    align-items: stretch; padding: 10px; color: #fff; font-size: 13px; }
  #panelBuscar .fila { display: flex; gap: 6px; }
  #txtBuscar { flex: 1 1 auto; min-width: 0; border: 0; border-radius: 6px; padding: 8px 10px; font-size: 14px; color: #111; outline: none; }
  #panelBuscar button.x { background: rgba(255,255,255,.14); border-radius: 6px; padding: 0 10px; color: #fff; }
  #listaBuscar { list-style: none; margin: 6px 0 0; padding: 0; max-height: 210px; overflow-y: auto; }
  #listaBuscar li { padding: 7px 8px; border-radius: 6px; cursor: pointer; }
  #listaBuscar li small { color: #94A3B8; margin-left: 6px; }
  #listaBuscar li:hover, #listaBuscar li.sel { background: #2563EB; }
  #listaBuscar li:hover small, #listaBuscar li.sel small { color: #DBEAFE; }
  #resBuscar { margin-top: 8px; line-height: 1.45; }
  #resBuscar .chip { display: inline-block; padding: 1px 8px; border-radius: 10px; font-weight: 700; color: #fff; margin-left: 4px; }
  #resBuscar small { color: #CBD5E1; display: block; margin-top: 3px; }
  #leyenda { left: 10px; }
  #leyendaNoaa { right: 10px; display: none; max-width: 230px; }
  .leyenda i { display: inline-block; width: 11px; height: 11px; border-radius: 50%; margin-right: 6px;
    border: 1px solid #fff; vertical-align: middle; }
  .leyenda b { display: block; margin-bottom: 2px; }
  .leyenda .sw { display: inline-block; width: 16px; height: 10px; margin-right: 6px; vertical-align: middle; border-radius: 2px; }
  .leyenda small { color: #94A3B8; display: block; line-height: 1.3; margin-top: 4px; }
  #aviso { display: none; position: absolute; inset: 0; z-index: 20; color: #fff; background: #0A192F;
    align-items: center; justify-content: center; text-align: center; padding: 24px; }
</style>
</head>
<body>
<div id="cesiumContainer"></div>
<div id="aviso">Este navegador no admite WebGL, necesario para el visor Cesium.</div>
<div class="toolbar">
  <div class="grupo" id="grpBase">
    <button id="btnEsri" class="activo">Imagen (Esri)</button>
    <button id="btnTopo">Topográfico (OSM)</button>
    <button id="btnOSM">Base (OSM)</button>
    <button id="btnOSMt">OSM transparente</button>
  </div>
  <div class="grupo">
    <label for="selCapa">Capa</label>
    <select id="selCapa">
      <option value="none">Ninguna</option>
      <optgroup label="NOAA">
        <option value="lluvia">Lluvia pronosticada (GFS)</option>
        <option value="viento">Viento pronosticado (GFS)</option>
        <option value="nubes">Nubes / vapor de agua (GOES)</option>
        <option value="sst">Temp. superficie del mar (GFS)</option>
      </optgroup>
      <optgroup label="Terreno (Copernicus DEM)">
        <option value="susc" selected>Susceptibilidad a inundación (terreno)</option>
      </optgroup>
      <optgroup label="Pronóstico estacional CCSR/IRI">
        <option value="c_pr">CCSR/IRI Precipitación (CFSv2)</option>
        <option value="c_tas">CCSR/IRI Temperatura (CFSv2)</option>
      </optgroup>
      <optgroup label="Satélite MODIS (NASA)">
        <option value="m_tc">MODIS Color real (Terra)</option>
        <option value="m_lst">MODIS Temp. superficie terrestre (día)</option>
        <option value="m_ndvi">MODIS Vegetación NDVI (8 días)</option>
        <option value="m_cf">MODIS Fracción de nubes (día)</option>
      </optgroup>
    </select>
  </div>
  <div class="grupo" id="boxMes" style="display:none">
    <label for="selMes" id="lblMes">Mes</label>
    <select id="selMes"></select>
  </div>
  <div class="grupo" id="boxFecha" style="display:none">
    <label for="inpFecha">Fecha</label>
    <input type="date" id="inpFecha">
  </div>
  <div class="grupo"><button id="btnHome">Inicio</button></div>
  <span id="estado"></span>
</div>
<div class="grupo" id="boxTiempo">
  <button id="btnPlay" type="button" title="Reproducir" aria-label="Reproducir">&#9654;</button>
  <div id="slotTimeline"></div>
</div>
<div class="grupo" id="grpBuscar">
  <button id="btnBuscar" type="button" title="Buscar un distrito del piloto (Tumbes, Piura, Lambayeque y La Libertad) y ubicarlo en el mapa">&#128269; Buscar Distrito</button>
</div>
<div class="grupo" id="panelBuscar">
  <div class="fila">
    <input id="txtBuscar" type="text" placeholder="Escribe el distrito (p. ej. Piura)" autocomplete="off" aria-label="Nombre del distrito">
    <button id="btnLimpiar" class="x" type="button" title="Quitar la selección del mapa" aria-label="Quitar selección">&#10005;</button>
  </div>
  <ul id="listaBuscar"></ul>
  <div id="resBuscar"></div>
</div>
<div class="grupo" id="grpVista">
  <label>Vista</label>
  <button id="vInclinar" type="button" title="Inclinar: ver más horizonte" aria-label="Inclinar">&#9650;</button>
  <button id="vEnderezar" type="button" title="Enderezar: ver desde arriba" aria-label="Enderezar">&#9660;</button>
  <button id="vGirarIzq" type="button" title="Girar a la izquierda" aria-label="Girar a la izquierda">&#10226;</button>
  <button id="vGirarDer" type="button" title="Girar a la derecha" aria-label="Girar a la derecha">&#10227;</button>
  <button id="vZoomMas" type="button" title="Acercar" aria-label="Acercar">+</button>
  <button id="vZoomMenos" type="button" title="Alejar" aria-label="Alejar">&minus;</button>
  <button id="vNorte" type="button" title="Norte arriba y vista cenital" aria-label="Norte arriba">N</button>
</div>
<div class="leyenda" id="leyenda"></div>
<div class="leyenda" id="leyendaNoaa"></div>
<script>
(function () {
  const PUNTOS = __PUNTOS__;
  const COLORES = __COLORES__;

  Cesium.Ion.defaultAccessToken = "";

  let viewer;
  try {
    viewer = new Cesium.Viewer('cesiumContainer', {
      baseLayer: false,            // sin imagen por defecto de ion
      baseLayerPicker: false,
      geocoder: false,
      homeButton: false,
      sceneModePicker: false,      // solo vista 3D
      scene3DOnly: true,
      navigationHelpButton: false,
      animation: false,
      timeline: true,              // solo se muestra con las capas de pronóstico mensual (CCSR/IRI)
      fullscreenButton: true,
      infoBox: true,
      selectionIndicator: true,
      requestRenderMode: true,     // renderiza solo cuando algo cambia
      maximumRenderTimeChange: Infinity
    });
  } catch (e) {
    document.getElementById('aviso').style.display = 'flex';
    return;
  }

  // La línea de tiempo de Cesium se mueve a la barra superior izquierda (junto al botón ▶)
  const timelineEl = viewer.timeline.container;
  const boxTiempo = document.getElementById('boxTiempo');
  document.getElementById('slotTimeline').appendChild(timelineEl);
  timelineEl.style.display = 'none';

  // ---- Mapas base: Esri (imagen, por defecto) y OSM (topografía) ----
  const capaEsri = viewer.imageryLayers.addImageryProvider(
    new Cesium.UrlTemplateImageryProvider({
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      maximumLevel: 19,
      credit: 'Imagen © Esri, Maxar, Earthstar Geographics'
    }));
  // Topográfico (OSM): OpenTopoMap, mapa topográfico con curvas de nivel hecho con datos de OpenStreetMap
  const capaTopo = viewer.imageryLayers.addImageryProvider(
    new Cesium.UrlTemplateImageryProvider({
      url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
      subdomains: ['a', 'b', 'c'],
      maximumLevel: 17,
      credit: 'Topografía © colaboradores de OpenStreetMap, SRTM | © OpenTopoMap (CC-BY-SA)'
    }));
  // Base (OSM): capa estándar de OpenStreetMap
  const capaOSM = viewer.imageryLayers.addImageryProvider(
    new Cesium.OpenStreetMapImageryProvider({ url: 'https://tile.openstreetmap.org/' }));

  // Opacidad de los mapas base: 90 % (deja ver un poco el fondo y resalta las capas encima)
  const OPACIDAD_BASE = 0.9;
  [capaEsri, capaTopo, capaOSM].forEach(function (l) { l.alpha = OPACIDAD_BASE; });

  // OSM transparente: la imagen de Esri queda debajo y el OSM estándar encima al 50 %, de modo que se ven calles y
  // nombres sobre la imagen satelital (OSM no publica teselas transparentes propias; la mezcla se hace aquí).
  const OPACIDAD_OSM_TRANSPARENTE = 0.5;

  function setBase(base) {
    capaOSM.show = base === 'osm' || base === 'osmt';
    capaOSM.alpha = base === 'osmt' ? OPACIDAD_OSM_TRANSPARENTE : OPACIDAD_BASE;
    capaTopo.show = base === 'topo';
    capaEsri.show = base === 'esri' || base === 'osmt';
    document.getElementById('btnOSMt').classList.toggle('activo', base === 'osmt');
    document.getElementById('btnOSM').classList.toggle('activo', base === 'osm');
    document.getElementById('btnTopo').classList.toggle('activo', base === 'topo');
    document.getElementById('btnEsri').classList.toggle('activo', base === 'esri');
    viewer.scene.requestRender();
  }

  // ---- Capas NOAA ----
  // WMS directos de NOAA nowCOAST (globales). Lluvia y viento salen del modelo GFS de NOAA.
  const WMS = {
    nubes: { url: 'https://nowcoast.noaa.gov/geoserver/satellite/wms',
             layers: 'global_water_vapor_imagery_mosaic', alpha: 0.65,
             credit: 'NOAA GOES / nowCOAST',
             titulo: 'Hora', span: 3600000, intervalo: 2000,
             etiqueta: function (ms) { const i = new Date(ms).toISOString(); return i.slice(5, 10) + ' ' + i.slice(11, 16) + ' UTC'; },
             leyenda: '<b>Vapor de agua (GOES)</b>Más claro/blanco = más humedad y nubosidad en altura.<small>Satélite NOAA, mosaico global; imágenes de las últimas horas (usa la línea de tiempo).</small>' },
    sst:   { url: 'https://nowcoast.noaa.gov/geoserver/sea_surface_temperature/wms',
             layers: 'global_sea_surface_temperature', alpha: 0.7,
             credit: 'NOAA GFS / nowCOAST',
             titulo: 'Día', span: 86400000, intervalo: 2500,
             etiqueta: function (ms) { return new Date(ms).toISOString().slice(0, 10); },
             leyenda: '<b>Temp. superficie del mar</b>Colores cálidos = agua más caliente (clave para El Niño).<small>Modelo GFS de NOAA, vía nowCOAST; últimos días disponibles (usa la línea de tiempo).</small>' }
  };
  // Una sola capa WMS activa; se recrea con el parámetro TIME al moverse por la línea de tiempo
  let capaWms = null;
  const tiemposWms = {};     // clave -> promesa con la lista de instantes [ms] del servicio
  function quitarWms() {
    if (capaWms) { viewer.imageryLayers.remove(capaWms, true); capaWms = null; }
  }
  function ponerWms(clave, iso) {
    quitarWms();
    const c = WMS[clave];
    const params = { transparent: true, format: 'image/png' };
    if (iso) { params.time = iso; }
    capaWms = viewer.imageryLayers.addImageryProvider(new Cesium.WebMapServiceImageryProvider({
      url: c.url, layers: c.layers, parameters: params, credit: c.credit
    }));
    capaWms.alpha = c.alpha;
    viewer.scene.requestRender();
  }
  // Lee del GetCapabilities los instantes disponibles (dimensión TIME) de la capa
  function cargarTiemposWms(clave) {
    if (tiemposWms[clave]) { return tiemposWms[clave]; }
    const c = WMS[clave];
    tiemposWms[clave] = fetch(c.url + '?service=WMS&version=1.3.0&request=GetCapabilities').then(function (r) {
      if (!r.ok) { throw new Error('HTTP ' + r.status); }
      return r.text();
    }).then(function (txt) {
      const xml = new DOMParser().parseFromString(txt, 'text/xml');
      const capa = Array.from(xml.getElementsByTagName('Layer')).find(function (l) {
        const n = Array.from(l.children).find(function (h) { return h.tagName === 'Name'; });
        return n && n.textContent === c.layers;
      });
      const dim = capa && Array.from(capa.children).find(function (h) {
        return h.tagName === 'Dimension' && h.getAttribute('name') === 'time';
      });
      if (!dim) { return []; }
      return dim.textContent.split(',').map(function (t) { return Date.parse(t.trim()); })
        .filter(function (v) { return !isNaN(v); }).sort(function (x, y) { return x - y; });
    });
    tiemposWms[clave].catch(function () { delete tiemposWms[clave]; });
    return tiemposWms[clave];
  }

  // Satélite MODIS (Terra) vía NASA GIBS: teselas diarias, se recrean al cambiar la fecha
  const MODIS = {
    m_tc:   { capa: 'MODIS_Terra_CorrectedReflectance_TrueColor', mt: 'GoogleMapsCompatible_Level9', ext: 'jpg', nivel: 9, alpha: 1.0,
              leyenda: '<b>MODIS Color real (Terra)</b>Imagen tal como la ve el satélite: nubes, humo, sedimentos en ríos y costa.' },
    m_lst:  { capa: 'MODIS_Terra_Land_Surface_Temp_Day', mt: 'GoogleMapsCompatible_Level7', ext: 'png', nivel: 7, alpha: 0.75,
              leyenda: '<b>MODIS Temp. superficie terrestre (día)</b>Colores cálidos = suelo más caliente. Los huecos son nubes.' },
    m_ndvi: { capa: 'MODIS_Terra_NDVI_8Day', mt: 'GoogleMapsCompatible_Level9', ext: 'png', nivel: 9, alpha: 0.8,
              leyenda: '<b>MODIS Vegetación NDVI (8 días)</b>Verde intenso = vegetación sana; tonos pardos = suelo seco o sin cubierta.' },
    m_cf:   { capa: 'MODIS_Terra_Cloud_Fraction_Day', mt: 'GoogleMapsCompatible_Level6', ext: 'png', nivel: 6, alpha: 0.75,
              leyenda: '<b>MODIS Fracción de nubes (día)</b>Más opaco/claro = mayor cobertura de nubes.' }
  };
  let capaModis = null;
  const inpFecha = document.getElementById('inpFecha');
  const boxFecha = document.getElementById('boxFecha');
  (function () {
    const hoy = new Date();
    const f = function (dias) {
      return new Date(hoy.getTime() - dias * 86400000).toISOString().slice(0, 10);
    };
    inpFecha.max = f(1);
    inpFecha.value = f(2);   // las capas diarias de GIBS llegan con 1-3 días de retraso
  })();

  // Susceptibilidad estructural a inundación: imagen fija generada con generar_susceptibilidad.py (Copernicus DEM GLO-30)
  const SUSC = {
    url: '__SUSC__',                 // imagen incrustada (data URI): el repositorio es privado, no sirve una URL raw
    rect: [-81.4, -9.0, -77.2, -3.3],
    leyenda: '<b>Susceptibilidad a inundación</b>' +
      '<div><span class="sw" style="background:#dc2626"></span> Alta</div>' +
      '<div><span class="sw" style="background:#facc15"></span> Media</div>' +
      '<div><span class="sw" style="background:#22c55e;opacity:.4"></span> Baja (casi transparente)</div>' +
      '<small>Terreno, no pronóstico: dónde el suelo se inunda con más facilidad (altura, relieve local, pendiente y cercanía al mar o estuario). ' +
      'Copernicus DEM GLO-30 © ESA, ~185 m. Indicativo, sin validar con inundaciones reales.</small>'
  };
  let capaSusc = null;
  function quitarSusc() {
    if (capaSusc) { viewer.imageryLayers.remove(capaSusc, true); capaSusc = null; }
  }
  function ponerSusc() {
    quitarSusc();
    capaSusc = viewer.imageryLayers.addImageryProvider(new Cesium.SingleTileImageryProvider({
      url: SUSC.url, rectangle: Cesium.Rectangle.fromDegrees(SUSC.rect[0], SUSC.rect[1], SUSC.rect[2], SUSC.rect[3]),
      credit: 'Copernicus DEM GLO-30 © ESA'
    }));
    capaSusc.alpha = 0.85;
    viewer.scene.requestRender();
  }
  function quitarModis() {
    if (capaModis) { viewer.imageryLayers.remove(capaModis, true); capaModis = null; }
  }
  function ponerModis(clave) {
    quitarModis();
    const c = MODIS[clave];
    const url = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/' + c.capa + '/default/' +
      inpFecha.value + '/' + c.mt + '/{z}/{y}/{x}.' + c.ext;
    capaModis = viewer.imageryLayers.addImageryProvider(new Cesium.UrlTemplateImageryProvider({
      url: url, maximumLevel: c.nivel, credit: 'NASA EOSDIS GIBS / MODIS Terra'
    }));
    capaModis.alpha = c.alpha;
    viewer.scene.requestRender();
  }

  // Pronóstico estacional CCSR/IRI (Columbia): media de los 32 miembros del CFSv2 (NOAA/NCEP, NMME),
  // 10 meses de pronóstico, rejilla de 1°. Se lee por OPeNDAP binario (.dods) directo desde el
  // navegador: el servidor de CCSR responde con CORS abierto. Los meses se navegan con la línea de tiempo.
  const CCSR_BASE = 'https://forecast.ccsr.columbia.edu/data/NMME/NOAA-NCEP/CFSv2/early_month_samples/forecast/';
  const CCSR = {
    c_pr:  { v: 'pr',  titulo: 'Precipitación (CFSv2)', unidad: 'mm/día', dec: 1, minimo: 0.2,
             escala: [[0, '#E0F2FE'], [1, '#A6D8F0'], [3, '#4FA3E0'], [6, '#2563EB'], [10, '#7C3AED']] },
    c_tas: { v: 'tas', titulo: 'Temperatura (CFSv2)', unidad: '°C', dec: 0, minimo: -100,
             escala: [[-100, '#3B82F6'], [14, '#22D3EE'], [18, '#22C55E'], [22, '#FACC15'], [26, '#F97316'], [30, '#EF4444']] }
  };
  const NM = 32, NL = 10, NY = 7, NX = 6;          // miembros, meses de pronóstico, lat -9..-3, lon -82..-77
  const MESES_ES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
  const fuenteCcsr = new Cesium.CustomDataSource('ccsr');
  viewer.dataSources.add(fuenteCcsr);
  const boxMes = document.getElementById('boxMes');
  const selMes = document.getElementById('selMes');
  let ccsrDatos = null;     // {meses:[ms], emision, pr:[mes][celda], tas:[mes][celda], lats, lons}
  let ccsrMes = null;       // mes elegido (se conserva al cambiar entre precipitación y temperatura)

  function etiquetaMes(ms) {
    const d = new Date(ms);
    return MESES_ES[d.getUTCMonth()] + '-' + d.getUTCFullYear();
  }

  // Lee una respuesta OPeNDAP binaria: texto DDS + "Data:" + datos XDR (big-endian)
  function dods(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) { throw new Error('HTTP ' + r.status); }
      return r.arrayBuffer();
    }).then(function (buf) {
      const b = new Uint8Array(buf);
      const marca = [10, 68, 97, 116, 97, 58, 10];   // salto de línea + "Data:" + salto de línea
      let i = 0;
      while (i < b.length - 7) {
        let igual = true;
        for (let k = 0; k < 7; k++) { if (b[i + k] !== marca[k]) { igual = false; break; } }
        if (igual) { break; }
        i++;
      }
      if (i >= b.length - 7) { throw new Error('Respuesta OPeNDAP inválida'); }
      return new DataView(buf, i + 7);
    });
  }
  function leerInts(dv) {
    const n = dv.getUint32(0), a = [];
    for (let k = 0; k < n; k++) { a.push(dv.getInt32(8 + 4 * k)); }
    return a;
  }
  function leerFloats(dv) {
    const n = dv.getUint32(0), a = new Float32Array(n);
    for (let k = 0; k < n; k++) { a[k] = dv.getFloat32(8 + 4 * k); }
    return a;
  }
  // Media de los miembros: entrada ordenada [miembro][mes][lat][lon]
  function mediaMiembros(f) {
    const celdas = NY * NX, porMiembro = NL * celdas, salida = [];
    for (let l = 0; l < NL; l++) {
      const fila = new Array(celdas).fill(0);
      for (let m = 0; m < NM; m++) {
        for (let j = 0; j < celdas; j++) { fila[j] += f[m * porMiembro + l * celdas + j] / NM; }
      }
      salida.push(fila);
    }
    return salida;
  }

  function cargarCcsr() {
    if (ccsrDatos) { return Promise.resolve(ccsrDatos); }
    // La última emisión es el último índice de S: se lee su tamaño del .dds
    return fetch(CCSR_BASE + 'pr.dds').then(function (r) {
      if (!r.ok) { throw new Error('HTTP ' + r.status); }
      return r.text();
    }).then(function (dds) {
      const p = dds.indexOf('S = ');
      const nS = parseInt(dds.substring(p + 4), 10);
      if (!(nS > 0)) { throw new Error('No se pudo leer la emisión'); }
      const s = nS - 1;
      const rng = '[' + s + '][0:' + (NM - 1) + '][0:' + (NL - 1) + '][81:87][278:283]';
      return Promise.all([
        dods(CCSR_BASE + 'pr.dods?pr' + rng),
        dods(CCSR_BASE + 'tas.dods?tas' + rng),
        dods(CCSR_BASE + 'pr.dods?target[' + s + '][0:' + (NL - 1) + ']'),
        dods(CCSR_BASE + 'pr.dods?Y[81:87]'),
        dods(CCSR_BASE + 'pr.dods?X[278:283]'),
        dods(CCSR_BASE + 'pr.dods?S[' + s + ']')
      ]);
    }).then(function (r) {
      const lats = leerInts(r[3]), lonsE = leerInts(r[4]);
      if (lats[0] !== -9 || lats[NY - 1] !== -3 || lonsE[0] !== 278) {
        throw new Error('La rejilla del servicio cambió');
      }
      const dia0 = Date.UTC(1960, 0, 1);
      ccsrDatos = {
        meses: leerInts(r[2]).map(function (d) { return dia0 + d * 86400000; }),
        emision: dia0 + leerInts(r[5])[0] * 86400000,
        pr: mediaMiembros(leerFloats(r[0])),
        tas: mediaMiembros(leerFloats(r[1])),
        lats: lats,
        lons: lonsE.map(function (x) { return x > 180 ? x - 360 : x; })
      };
      selMes.innerHTML = '';
      ccsrDatos.meses.forEach(function (ms, i) {
        const o = document.createElement('option');
        o.value = i; o.textContent = etiquetaMes(ms);
        selMes.appendChild(o);
      });
      return ccsrDatos;
    });
  }

  function indiceMesDesdeFecha(ms) {
    let idx = 0;
    ccsrDatos.meses.forEach(function (m, i) { if (m <= ms) { idx = i; } });
    return idx;
  }

  function dibujarCcsr(clave, mes) {
    fuenteCcsr.entities.removeAll();
    const c = CCSR[clave], serie = ccsrDatos[c.v][mes], etq = etiquetaMes(ccsrDatos.meses[mes]);
    ccsrDatos.lats.forEach(function (lat, y) {
      ccsrDatos.lons.forEach(function (lon, x) {
        const val = serie[y * NX + x];
        if (isNaN(val) || val < c.minimo) { return; }
        const col = Cesium.Color.fromCssColorString(colorEscala(c.escala, val));
        fuenteCcsr.entities.add({
          name: c.titulo + ' ' + etq,
          position: Cesium.Cartesian3.fromDegrees(lon, lat),
          rectangle: {
            coordinates: Cesium.Rectangle.fromDegrees(lon - 0.5, lat - 0.5, lon + 0.5, lat + 0.5),
            material: col.withAlpha(0.55), height: 0
          },
          label: {
            text: val.toFixed(c.dec) + (clave === 'c_tas' ? '°' : ''),
            font: '600 11px sans-serif', fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK, outlineWidth: 3,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE, scale: 0.9,
            distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 1500000)
          },
          description: c.titulo + ' · ' + etq + ': ' + val.toFixed(2) + ' ' + c.unidad +
            '<br>Media de ' + NM + ' miembros del CFSv2 (NOAA/NCEP, NMME)' +
            '<br>Punto de rejilla: ' + lat + '°, ' + lon + '°'
        });
      });
    });
    viewer.scene.requestRender();
  }

  function leyendaCcsr(clave) {
    const c = CCSR[clave], e = c.escala;
    let html = '<b>CCSR/IRI · ' + c.titulo + '</b>';
    e.forEach(function (t, i) {
      let txt;
      if (i === 0 && e.length > 1 && clave === 'c_tas') { txt = '< ' + e[1][0]; }
      else if (!e[i + 1]) { txt = '≥ ' + t[0]; }
      else { txt = t[0] + '–' + e[i + 1][0]; }
      html += '<div><span class="sw" style="background:' + t[1] + '"></span>' + txt + ' ' + c.unidad + '</div>';
    });
    html += '<small>Media de 32 miembros del CFSv2 (NOAA/NCEP, NMME), rejilla de 1°. ' +
      'Emisión: ' + etiquetaMes(ccsrDatos.emision) + '. Usa la línea de tiempo o el selector «Mes».</small>' +
      '<small>Graphics provided by the International Research Institute for Climate and Society, ' +
      'Columbia University (CCSR/IRI).</small>';
    return html;
  }

  // ===== Motor de tiempo común: línea de tiempo de Cesium + selector + reproducción ▶/❚❚ =====
  // Lo usan CCSR/IRI (meses), GFS (días), MODIS (días) y los WMS de NOAA (horas / días).
  const btnPlay = document.getElementById('btnPlay');
  const lblTiempo = document.getElementById('lblMes');
  let tiempo = null;        // {titulo, fechas:[ms], idx, span, intervalo, etiqueta(ms), render(i), selector}
  let timerTiempo = null;

  function detenerTiempo() {
    if (timerTiempo) { clearInterval(timerTiempo); timerTiempo = null; }
    btnPlay.innerHTML = '&#9654;';
    btnPlay.title = 'Reproducir';
    btnPlay.setAttribute('aria-label', 'Reproducir');
  }
  function quitarTiempo() {
    detenerTiempo();
    tiempo = null;
    boxMes.style.display = 'none';
    boxTiempo.style.display = 'none';
    timelineEl.style.display = 'none';
  }
  function indiceTiempo(ms) {
    let idx = 0;
    tiempo.fechas.forEach(function (f, i) { if (f <= ms) { idx = i; } });
    return idx;
  }
  function irAIndice(i, desdeTimeline) {
    if (!tiempo) { return; }
    tiempo.idx = i;
    selMes.value = String(i);
    if (!desdeTimeline) {
      viewer.clock.currentTime = Cesium.JulianDate.fromDate(new Date(tiempo.fechas[i] + tiempo.span / 2));
    }
    tiempo.render(i);
    viewer.scene.requestRender();
  }
  function iniciarTiempo(cfg) {
    detenerTiempo();
    tiempo = cfg;
    const n = cfg.fechas.length;
    lblTiempo.textContent = cfg.titulo;
    selMes.innerHTML = '';
    cfg.fechas.forEach(function (ms, i) {
      const o = document.createElement('option');
      o.value = i; o.textContent = cfg.etiqueta(ms);
      selMes.appendChild(o);
    });
    const ini = Cesium.JulianDate.fromDate(new Date(cfg.fechas[0]));
    const fin = Cesium.JulianDate.fromDate(new Date(cfg.fechas[n - 1] + cfg.span));
    viewer.clock.startTime = ini.clone();
    viewer.clock.stopTime = fin.clone();
    viewer.clock.clockRange = Cesium.ClockRange.CLAMPED;
    viewer.clock.shouldAnimate = false;
    boxTiempo.style.display = 'flex';
    timelineEl.style.display = 'block';
    viewer.timeline.zoomTo(ini, fin);
    viewer.timeline.resize();
    boxMes.style.display = cfg.selector === false ? 'none' : 'flex';
    irAIndice(Math.min(Math.max(cfg.idx, 0), n - 1));
  }
  window.addEventListener('resize', function () {
    if (tiempo) { viewer.timeline.resize(); }
  });
  btnPlay.onclick = function () {
    if (timerTiempo) { detenerTiempo(); return; }
    if (!tiempo) { return; }
    btnPlay.innerHTML = '&#10074;&#10074;';
    btnPlay.title = 'Pausar';
    btnPlay.setAttribute('aria-label', 'Pausar');
    timerTiempo = setInterval(function () {
      if (!tiempo) { detenerTiempo(); return; }
      irAIndice((tiempo.idx + 1) % tiempo.fechas.length);   // al llegar al final vuelve al inicio
    }, tiempo.intervalo);
  };
  // Línea de tiempo: al arrastrarla cambia el instante mostrado
  viewer.timeline.addEventListener('settime', function (e) {
    if (!tiempo) { return; }
    const idx = indiceTiempo(Cesium.JulianDate.toDate(e.timeJulian).getTime());
    if (idx !== tiempo.idx) { irAIndice(idx, true); }
    viewer.scene.requestRender();
  }, false);

  function mostrarCcsr(clave) {
    // Mes por defecto: el que contiene la fecha de hoy (o el primero del pronóstico)
    if (ccsrMes === null) { ccsrMes = indiceMesDesdeFecha(Date.now()); }
    leyendaNoaa.innerHTML = leyendaCcsr(clave);
    leyendaNoaa.style.display = 'block';
    iniciarTiempo({
      titulo: 'Mes', fechas: ccsrDatos.meses, idx: ccsrMes, span: 30 * 86400000, intervalo: 2500,
      etiqueta: etiquetaMes,
      render: function (i) { ccsrMes = i; dibujarCcsr(clave, i); }
    });
  }

  // Lluvia / viento: pronóstico diario del GFS (NOAA) en rejilla de 1°, servido por Open-Meteo
  const fuenteGfs = new Cesium.CustomDataSource('gfs');
  viewer.dataSources.add(fuenteGfs);
  const REJ = { latMin: -9, latMax: -3, lonMin: -82, lonMax: -77 };
  let gfs = null;           // [{lat, lon, daily:{...}}]
  let gfsCargando = false;
  const estado = document.getElementById('estado');

  function setEstado(txt) {
    estado.textContent = txt || '';
    estado.style.display = txt ? 'inline-block' : 'none';
  }

  function cargarGfs() {
    if (gfs) { return Promise.resolve(gfs); }
    const lats = [], lons = [];
    for (let la = REJ.latMin; la <= REJ.latMax; la++) {
      for (let lo = REJ.lonMin; lo <= REJ.lonMax; lo++) { lats.push(la); lons.push(lo); }
    }
    const url = 'https://api.open-meteo.com/v1/forecast?latitude=' + lats.join(',') +
      '&longitude=' + lons.join(',') +
      '&daily=precipitation_sum,wind_speed_10m_max,wind_direction_10m_dominant' +
      '&models=gfs_global&forecast_days=7&timezone=auto';
    return fetch(url).then(function (r) {
      if (!r.ok) { throw new Error('HTTP ' + r.status); }
      return r.json();
    }).then(function (j) {
      const arr = Array.isArray(j) ? j : [j];
      gfs = arr.map(function (o, i) { return { lat: lats[i], lon: lons[i], daily: o.daily }; });
      return gfs;
    });
  }

  const ESCALA_LLUVIA = [
    [1, '#A6D8F0'], [5, '#4FA3E0'], [15, '#2563EB'], [30, '#7C3AED'], [60, '#C026D3']
  ];
  const ESCALA_VIENTO = [
    [0, '#38BDF8'], [10, '#22C55E'], [20, '#FACC15'], [30, '#F97316'], [45, '#EF4444']
  ];
  function colorEscala(esc, v) {
    let c = esc[0][1];
    for (let i = 0; i < esc.length; i++) { if (v >= esc[i][0]) { c = esc[i][1]; } }
    return c;
  }
  function leyendaEscala(titulo, esc, unidad, nota) {
    return '<b>' + titulo + '</b>' + esc.map(function (e, i) {
      const sig = esc[i + 1] ? '–' + esc[i + 1][0] : '+';
      return '<div><span class="sw" style="background:' + e[1] + '"></span>' + e[0] + sig + ' ' + unidad + '</div>';
    }).join('') + '<small>' + nota + '</small>';
  }

  function dibujarGfs(tipo, dia) {
    fuenteGfs.entities.removeAll();
    gfs.forEach(function (p) {
      const d = p.daily;
      const lluvia = d.precipitation_sum[dia];
      const vel = d.wind_speed_10m_max[dia];
      const dir = d.wind_direction_10m_dominant[dia];
      if (tipo === 'lluvia') {
        if (lluvia === null || lluvia < 1) { return; }
        const col = Cesium.Color.fromCssColorString(colorEscala(ESCALA_LLUVIA, lluvia));
        fuenteGfs.entities.add({
          name: 'Lluvia GFS ' + d.time[dia],
          rectangle: {
            coordinates: Cesium.Rectangle.fromDegrees(p.lon - 0.5, p.lat - 0.5, p.lon + 0.5, p.lat + 0.5),
            material: col.withAlpha(0.55), height: 0
          },
          position: Cesium.Cartesian3.fromDegrees(p.lon, p.lat),
          label: {
            text: Math.round(lluvia) + ' mm', font: '600 11px sans-serif',
            fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.BLACK, outlineWidth: 3,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE, scale: 0.9,
            distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 1500000)
          },
          description: 'Precipitación acumulada del día: ' + lluvia + ' mm<br>Fecha: ' + d.time[dia] +
            '<br>Punto de rejilla: ' + p.lat + '°, ' + p.lon + '°'
        });
      } else if (tipo === 'viento') {
        if (vel === null) { return; }
        const col = Cesium.Color.fromCssColorString(colorEscala(ESCALA_VIENTO, vel));
        // La dirección meteorológica indica de dónde sopla; la flecha apunta hacia dónde va.
        const rumbo = Cesium.Math.toRadians(dir + 180);
        const largoKm = 25 + Math.min(vel, 50) * 1.4;
        const dLat = largoKm * Math.cos(rumbo) / 111;
        const dLon = largoKm * Math.sin(rumbo) / (111 * Math.cos(Cesium.Math.toRadians(p.lat)));
        fuenteGfs.entities.add({
          name: 'Viento GFS ' + d.time[dia],
          position: Cesium.Cartesian3.fromDegrees(p.lon, p.lat),
          polyline: {
            positions: Cesium.Cartesian3.fromDegreesArray([p.lon, p.lat, p.lon + dLon, p.lat + dLat]),
            width: 9,
            material: new Cesium.PolylineArrowMaterialProperty(col)
          },
          description: 'Viento máximo del día: ' + vel + ' km/h<br>Sopla desde: ' + Math.round(dir) +
            '°<br>Fecha: ' + d.time[dia] + '<br>Punto de rejilla: ' + p.lat + '°, ' + p.lon + '°'
        });
      }
    });
    viewer.scene.requestRender();
  }

  const leyendaNoaa = document.getElementById('leyendaNoaa');
  const selCapa = document.getElementById('selCapa');
  const fechaIso = function (ms) { return new Date(ms).toISOString().slice(0, 10); };

  function aplicarCapaNoaa(sinVuelo) {
    const sel = selCapa.value;
    quitarTiempo();
    quitarWms();
    quitarModis();
    quitarSusc();
    fuenteGfs.entities.removeAll();
    fuenteCcsr.entities.removeAll();
    boxFecha.style.display = 'none';
    setEstado('');
    if (sel === 'none') {
      leyendaNoaa.style.display = 'none';
    } else if (sel === 'susc') {
      leyendaNoaa.innerHTML = SUSC.leyenda;
      leyendaNoaa.style.display = 'block';
      ponerSusc();
      if (!sinVuelo) { viewer.camera.flyTo({ destination: Cesium.Rectangle.fromDegrees(-81.4, -8.8, -77.6, -3.4), duration: 1.5 }); }
    } else if (CCSR[sel]) {
      setEstado('Cargando pronóstico CCSR/IRI (CFSv2)…');
      cargarCcsr().then(function () {
        if (selCapa.value !== sel) { return; }
        setEstado('');
        mostrarCcsr(sel);
      }).catch(function (e) {
        setEstado('No se pudo obtener el pronóstico CCSR/IRI (' + e.message + '). Reintenta en unos segundos.');
        ccsrDatos = null;
      });
    } else if (MODIS[sel]) {
      // Línea de tiempo diaria: los últimos 30 días disponibles de GIBS
      boxFecha.style.display = 'flex';
      leyendaNoaa.innerHTML = MODIS[sel].leyenda + '<small>NASA GIBS, satélite Terra. Se actualiza a diario con 1–3 días de retraso. Usa la línea de tiempo o el campo Fecha.</small>';
      leyendaNoaa.style.display = 'block';
      const hoy0 = Date.parse(fechaIso(Date.now()) + 'T00:00:00Z');
      const fechas = [];
      for (let k = 30; k >= 1; k--) { fechas.push(hoy0 - k * 86400000); }
      inpFecha.min = fechaIso(fechas[0]);
      iniciarTiempo({
        titulo: 'Día', selector: false, fechas: fechas, idx: fechas.length - 2, span: 86400000, intervalo: 3000,
        etiqueta: fechaIso,
        render: function (i) { inpFecha.value = fechaIso(fechas[i]); ponerModis(sel); }
      });
    } else if (WMS[sel]) {
      const c = WMS[sel];
      leyendaNoaa.innerHTML = c.leyenda;
      leyendaNoaa.style.display = 'block';
      ponerWms(sel);                      // de inmediato, la imagen más reciente
      setEstado('Leyendo los instantes disponibles de NOAA…');
      cargarTiemposWms(sel).then(function (fechas) {
        if (selCapa.value !== sel) { return; }
        setEstado('');
        if (!fechas.length) { return; }   // sin dimensión de tiempo: se queda la última imagen
        iniciarTiempo({
          titulo: c.titulo, fechas: fechas, idx: fechas.length - 1, span: c.span, intervalo: c.intervalo,
          etiqueta: c.etiqueta,
          render: function (i) { ponerWms(sel, new Date(fechas[i]).toISOString()); }
        });
      }).catch(function () {
        if (selCapa.value === sel) { setEstado('No se pudo leer la línea de tiempo de NOAA; se muestra la última imagen.'); }
      });
    } else {
      // lluvia o viento (GFS, 7 días)
      leyendaNoaa.innerHTML = sel === 'lluvia'
        ? leyendaEscala('Lluvia del día (GFS)', ESCALA_LLUVIA, 'mm', 'Modelo GFS de NOAA, rejilla de 1° (vía Open-Meteo). Sin color = menos de 1 mm. Usa la línea de tiempo.')
        : leyendaEscala('Viento máx. del día (GFS)', ESCALA_VIENTO, 'km/h', 'La flecha apunta hacia donde sopla el viento. Modelo GFS de NOAA, rejilla de 1° (vía Open-Meteo). Usa la línea de tiempo.');
      leyendaNoaa.style.display = 'block';
      setEstado('Cargando pronóstico NOAA GFS…');
      cargarGfs().then(function () {
        if (selCapa.value !== sel) { return; }
        setEstado('');
        const fechas = gfs[0].daily.time.map(function (t) { return Date.parse(t + 'T00:00:00Z'); });
        iniciarTiempo({
          titulo: 'Día', fechas: fechas, idx: 0, span: 86400000, intervalo: 2000,
          etiqueta: fechaIso,
          render: function (i) { dibujarGfs(sel, i); }
        });
      }).catch(function () {
        setEstado('No se pudo obtener el pronóstico GFS. Reintenta en unos segundos.');
        gfs = null;
      });
    }
    viewer.scene.requestRender();
  }

  selCapa.onchange = function () { aplicarCapaNoaa(false); };
  selMes.onchange = function () {
    irAIndice(parseInt(selMes.value, 10));
  };
  inpFecha.onchange = function () {
    const sel = selCapa.value;
    if (!MODIS[sel] || !inpFecha.value) { return; }
    const i = tiempo ? tiempo.fechas.indexOf(Date.parse(inpFecha.value + 'T00:00:00Z')) : -1;
    if (i >= 0) { irAIndice(i); } else { ponerModis(sel); }   // fuera de la ventana: solo cambia la capa
  };

  // ---- Puntos de riesgo ----
  PUNTOS.forEach(function (p) {
    const color = Cesium.Color.fromCssColorString(COLORES[p.alerta] || '#FFA500');
    viewer.entities.add({
      name: p.canton,
      position: Cesium.Cartesian3.fromDegrees(p.lon, p.lat),
      // Área de influencia proporcional al riesgo
      ellipse: {
        semiMajorAxis: 4000 + p.riesgo * 11000,          // radio de afectación (la mitad que antes)
        semiMinorAxis: 4000 + p.riesgo * 11000,
        material: color.withAlpha(0.28),
        outline: true,
        outlineColor: color,
        height: 0
      },
      point: {
        pixelSize: 16,
        color: color,
        outlineColor: Cesium.Color.WHITE,
        outlineWidth: 2,
        disableDepthTestDistance: Number.POSITIVE_INFINITY
      },
      label: {
        text: p.canton + ' (' + Math.round(p.riesgo * 100) + '%)',
        font: '600 13px sans-serif',
        fillColor: Cesium.Color.WHITE,
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 3,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        pixelOffset: new Cesium.Cartesian2(0, -14),
        // Rótulo normal de cerca (< 120 km) y cada vez más pequeño al alejar la cámara (50 % a 1 800 km)
        scaleByDistance: new Cesium.NearFarScalar(120000, 1.0, 1800000, 0.5),
        disableDepthTestDistance: Number.POSITIVE_INFINITY
      },
      description:
        '<table class="cesium-infoBox-defaultTable"><tbody>' +
        '<tr><th>Departamento</th><td>' + p.provincia + '</td></tr>' +
        '<tr><th>Alerta</th><td>' + p.alerta + '</td></tr>' +
        '<tr><th>Riesgo</th><td>' + Math.round(p.riesgo * 100) + '%</td></tr>' +
        '<tr><th>Lluvia</th><td>+' + p.lluvia_mm + (p.horizonte.indexOf('mes') >= 0 ? ' mm/mes' : ' mm/día') + '</td></tr>' +
        '<tr><th>Mar (distrito costero)</th><td>' + (p.mar_idx > 0 ? 'por encima de lo normal (ENFEN)' : 'sin alerta') + '</td></tr>' +
        '<tr><th>Caudal del río</th><td>' + (p.cauce_idx > 0 ? 'por encima de lo normal (ENFEN)' : 'sin alerta') + '</td></tr>' +
        '<tr><th>Elevación</th><td>' + p.elevacion + ' m</td></tr>' +
        '<tr><th>Fecha</th><td>' + p.fecha + '</td></tr>' +
        '<tr><th>Pronóstico</th><td>' + p.horizonte + '</td></tr>' +
        '</tbody></table>'
    });
  });

  // ---- Perfil de Perú: siempre visible, encima de cualquier mapa base o capa ----
  // Dos líneas pegadas al suelo (halo oscuro + trazo amarillo) para que se vea
  // sobre imagen satelital, OSM y capas semitransparentes. Ningún selector la oculta.
  const PERFIL = __PERFIL__;
  const fuentePerfil = new Cesium.CustomDataSource('perfil-peru');
  viewer.dataSources.add(fuentePerfil);
  PERFIL.forEach(function (anillo) {
    const pos = Cesium.Cartesian3.fromDegreesArray(anillo);
    pos.push(pos[0]);   // cierra el contorno
    fuentePerfil.entities.add({
      polyline: { positions: pos, width: 6, clampToGround: true,
                  material: Cesium.Color.BLACK.withAlpha(0.55) }
    });
    fuentePerfil.entities.add({
      name: 'Perfil de Perú',
      polyline: { positions: pos, width: 3, clampToGround: true,
                  material: Cesium.Color.fromCssColorString('#FFD60A') }
    });
  });
  // Mantiene el perfil como última fuente de datos (se dibuja sobre las demás)
  viewer.dataSources.raiseToTop(fuentePerfil);

  // ---- Leyenda de alertas ----
  const usadas = Array.from(new Set(PUNTOS.map(function (p) { return p.alerta; })));
  document.getElementById('leyenda').innerHTML = usadas.map(function (a) {
    return '<div><i style="background:' + (COLORES[a] || '#FFA500') + '"></i>' + a + '</div>';
  }).join('') +
    '<div><span style="display:inline-block;width:18px;height:0;border-top:3px solid #FFD60A;' +
    'margin-right:6px;vertical-align:middle"></span>Perfil de Perú</div>';

  // ---- Cámara ----
  function irAInicio(duracion) {
    // Vista inclinada 15° hacia el norte (cámara al sur mirando al norte, norte arriba) sobre la costa norte del Perú
    viewer.camera.flyToBoundingSphere(new Cesium.BoundingSphere(Cesium.Cartesian3.fromDegrees(-79.4, -5.7, 0), 1), {
      offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-75), 1150000),
      duration: duracion === undefined ? 1.5 : duracion
    });
  }

  document.getElementById('btnTopo').onclick = function () { setBase('topo'); };
  document.getElementById('btnOSM').onclick = function () { setBase('osm'); };
  document.getElementById('btnOSMt').onclick = function () { setBase('osmt'); };
  document.getElementById('btnEsri').onclick = function () { setBase('esri'); };
  document.getElementById('btnHome').onclick = function () { irAInicio(1.2); };

  // ---- Controles de vista: inclinar, girar, acercar y norte ----
  // Cesium no trae botones de inclinación en su núcleo; se orbita alrededor del punto
  // del terreno que está en el centro de la pantalla.
  const PASO = Cesium.Math.toRadians(10);
  function centroPantalla() {
    const c = viewer.scene.canvas;
    const rayo = viewer.camera.getPickRay(new Cesium.Cartesian2(c.clientWidth / 2, c.clientHeight / 2));
    return rayo ? viewer.scene.globe.pick(rayo, viewer.scene) : undefined;
  }
  function orbitar(dHeading, dPitch) {
    const objetivo = centroPantalla();
    if (!objetivo) { return; }
    const cam = viewer.camera;
    const rango = Cesium.Cartesian3.distance(cam.positionWC, objetivo);
    const pitch = Cesium.Math.clamp(cam.pitch + dPitch, Cesium.Math.toRadians(-90), Cesium.Math.toRadians(-15));
    cam.lookAt(objetivo, new Cesium.HeadingPitchRange(cam.heading + dHeading, pitch, rango));
    cam.lookAtTransform(Cesium.Matrix4.IDENTITY);   // suelta la cámara para que siga libre
    viewer.scene.requestRender();
  }
  function zoomVista(factor) {
    const altura = viewer.camera.positionCartographic.height;
    if (factor > 0) { viewer.camera.zoomIn(altura * factor); } else { viewer.camera.zoomOut(altura * -factor); }
    viewer.scene.requestRender();
  }
  document.getElementById('vInclinar').onclick = function () { orbitar(0, PASO); };
  document.getElementById('vEnderezar').onclick = function () { orbitar(0, -PASO); };
  document.getElementById('vGirarIzq').onclick = function () { orbitar(-PASO, 0); };
  document.getElementById('vGirarDer').onclick = function () { orbitar(PASO, 0); };
  document.getElementById('vZoomMas').onclick = function () { zoomVista(0.35); };
  document.getElementById('vZoomMenos').onclick = function () { zoomVista(-0.35); };
  document.getElementById('vNorte').onclick = function () {
    // Norte arriba y mirando desde arriba, sin mover el centro de la vista
    orbitar(-viewer.camera.heading, Cesium.Math.toRadians(-90) - viewer.camera.pitch);
  };


  // ---- Buscar Distrito ----
  // 199 distritos de Tumbes, Piura, Lambayeque y La Libertad (INEI): se escribe el nombre y el mapa se acerca al distrito,
  // dibuja su contorno y lo rotula. Si el distrito está en el modelo de riesgo, muestra su riesgo.
  const LISTA_CANTONES = __CANTONES__;
  const RIESGOS = __RIESGOS__;
  const COMBINANTES = new RegExp('[' + String.fromCharCode(0x300) + '-' + String.fromCharCode(0x36f) + ']', 'g');
  function norm(t) { return t.normalize('NFD').replace(COMBINANTES, '').toLowerCase().trim(); }
  LISTA_CANTONES.forEach(function (c) { c.claves = [norm(c.n)].concat((c.a || []).map(norm)); });

  const fuenteBuscar = new Cesium.CustomDataSource('buscar-canton');
  viewer.dataSources.add(fuenteBuscar);
  const panelBuscar = document.getElementById('panelBuscar');
  const txtBuscar = document.getElementById('txtBuscar');
  const listaBuscar = document.getElementById('listaBuscar');
  const resBuscar = document.getElementById('resBuscar');
  let coincidencias = [], indiceSel = -1, temporizador = null;

  function buscarCoincidencias(texto) {
    const q = norm(texto);
    if (!q) { return []; }
    return LISTA_CANTONES.filter(function (c) {
      return c.claves.some(function (k) { return k.indexOf(q) >= 0; });
    }).sort(function (a, b) {
      const ea = a.claves.some(function (k) { return k.indexOf(q) === 0; }) ? 0 : 1;
      const eb = b.claves.some(function (k) { return k.indexOf(q) === 0; }) ? 0 : 1;
      return ea - eb || a.n.localeCompare(b.n);
    }).slice(0, 8);
  }
  function pintarLista() {
    listaBuscar.innerHTML = '';
    coincidencias.forEach(function (c, i) {
      const li = document.createElement('li');
      li.textContent = c.n;
      const p = document.createElement('small');
      p.textContent = c.p;
      li.appendChild(p);
      if (i === indiceSel) { li.className = 'sel'; }
      li.onmousedown = function (e) { e.preventDefault(); seleccionarCanton(c); };
      listaBuscar.appendChild(li);
    });
  }
  function limpiarSeleccion() {
    fuenteBuscar.entities.removeAll();
    resBuscar.textContent = '';
    viewer.scene.requestRender();
  }
  function mensaje(texto) { resBuscar.textContent = texto; }

  function seleccionarCanton(c) {
    fuenteBuscar.entities.removeAll();
    listaBuscar.innerHTML = '';
    coincidencias = [];
    txtBuscar.value = c.n;
    const cian = Cesium.Color.fromCssColorString('#22D3EE');
    c.r.forEach(function (anillo) {
      const pos = Cesium.Cartesian3.fromDegreesArray(anillo);
      fuenteBuscar.entities.add({ polygon: { hierarchy: new Cesium.PolygonHierarchy(pos), height: 0,
        material: cian.withAlpha(0.22), outline: false } });
      const cerrado = pos.concat([pos[0]]);
      fuenteBuscar.entities.add({ polyline: { positions: cerrado, width: 7, clampToGround: true,
        material: Cesium.Color.BLACK.withAlpha(0.5) } });
      fuenteBuscar.entities.add({ polyline: { positions: cerrado, width: 3.5, clampToGround: true, material: cian } });
    });
    fuenteBuscar.entities.add({
      name: c.n, position: Cesium.Cartesian3.fromDegrees(c.c[0], c.c[1]),
      point: { pixelSize: 11, color: cian, outlineColor: Cesium.Color.WHITE, outlineWidth: 2,
               disableDepthTestDistance: Number.POSITIVE_INFINITY },
      label: { text: c.n, font: '700 17px sans-serif', fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.BLACK,
               outlineWidth: 4, style: Cesium.LabelStyle.FILL_AND_OUTLINE, verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
               pixelOffset: new Cesium.Cartesian2(0, -14), scaleByDistance: new Cesium.NearFarScalar(120000, 1.0, 1500000, 0.55),
               disableDepthTestDistance: Number.POSITIVE_INFINITY },
      description: 'Distrito de ' + c.p
    });
    const b = c.b, mx = Math.max((b[2] - b[0]) * 0.25, 0.05), my = Math.max((b[3] - b[1]) * 0.25, 0.05);
    viewer.camera.flyTo({
      destination: Cesium.Rectangle.fromDegrees(b[0] - mx, b[1] - my, b[2] + mx, b[3] + my),
      orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 }, duration: 1.6
    });
    // Ficha del distrito (se arma con textContent: nada de HTML externo)
    resBuscar.textContent = '';
    const t = document.createElement('div');
    const strong = document.createElement('b');
    strong.textContent = c.n;
    t.appendChild(strong);
    t.appendChild(document.createTextNode(' · ' + c.p));
    resBuscar.appendChild(t);
    const r = RIESGOS[c.n];
    const d = document.createElement('div');
    if (r) {
      d.appendChild(document.createTextNode('Riesgo máximo: ' + Math.round(r.riesgo * 100) + ' %'));
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.style.background = COLORES[r.alerta] || '#6B7280';
      chip.textContent = r.alerta;
      d.appendChild(chip);
      const s2 = document.createElement('small');
      s2.textContent = 'Peor día en las fechas elegidas: ' + r.fecha + ' (' + r.horizonte + ')';
      d.appendChild(s2);
    } else {
      d.textContent = 'Sin alerta visible: el distrito no entra con las fechas y el índice de riesgo mínimo elegidos (baja el índice para ver más), ' +
        'o no es uno de los __N_MODELO__ distritos del modelo piloto. Ahora se muestran ' + Object.keys(RIESGOS).length + '.';
    }
    resBuscar.appendChild(d);
    viewer.scene.requestRender();
  }

  document.getElementById('btnBuscar').onclick = function () {
    const abrir = panelBuscar.style.display !== 'flex';
    panelBuscar.style.display = abrir ? 'flex' : 'none';
    if (abrir) { txtBuscar.focus(); }
  };
  document.getElementById('btnLimpiar').onclick = function () {
    txtBuscar.value = '';
    coincidencias = [];
    listaBuscar.innerHTML = '';
    limpiarSeleccion();
    txtBuscar.focus();
  };
  txtBuscar.addEventListener('input', function () {
    clearTimeout(temporizador);
    coincidencias = buscarCoincidencias(txtBuscar.value);
    indiceSel = -1;
    pintarLista();
    if (coincidencias.length) { resBuscar.textContent = ''; }   // quita el aviso o la ficha anteriores
    if (txtBuscar.value.trim() && !coincidencias.length) { mensaje('No se encontró «' + txtBuscar.value.trim() + '» entre los distritos del piloto (Tumbes, Piura, Lambayeque y La Libertad).'); }
    else if (!txtBuscar.value.trim()) { resBuscar.textContent = ''; }
    // Ubicación automática: si el texto coincide con un solo distrito, se marca solo (tras una breve pausa al escribir)
    if (coincidencias.length === 1 && norm(txtBuscar.value).length >= 4) {
      temporizador = setTimeout(function () { seleccionarCanton(coincidencias[0]); }, 700);
    }
  });
  txtBuscar.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown' && coincidencias.length) { indiceSel = (indiceSel + 1) % coincidencias.length; pintarLista(); e.preventDefault(); }
    else if (e.key === 'ArrowUp' && coincidencias.length) { indiceSel = (indiceSel - 1 + coincidencias.length) % coincidencias.length; pintarLista(); e.preventDefault(); }
    else if (e.key === 'Enter') {
      clearTimeout(temporizador);
      const elegido = indiceSel >= 0 ? coincidencias[indiceSel] : coincidencias[0];
      if (elegido) { seleccionarCanton(elegido); }
      e.preventDefault();
    } else if (e.key === 'Escape') { panelBuscar.style.display = 'none'; }
  });

  // ---- Estado inicial ----
  setBase('esri');
  irAInicio(0);
  // Capa predeterminada al entrar: susceptibilidad a inundación (sin mover la cámara: queda la vista inicial)
  selCapa.value = 'susc';
  aplicarCapaNoaa(true);
})();
</script>
</body>
</html>
"""


# Nombre del modelo de riesgo -> nombre en la lista de distritos del buscador (en Perú coinciden: ambos vienen del INEI)
ALIAS_MODELO_A_BUSCADOR = {}


def _anillo_plano(anillo):
    """El visor espera cada contorno como [lon, lat, lon, lat, ...]; si llega como pares [[lon, lat], ...] se aplana."""
    if anillo and isinstance(anillo[0], (list, tuple)):
        return [v for par in anillo for v in par]
    return anillo


@st.cache_resource
def _geometrias_visor():
    """Perfil de Perú y distritos del buscador con los contornos en el formato del visor (se calcula una sola vez)."""
    perfil = [_anillo_plano(a) for a in PERU_PERFIL]
    distritos = [{**d, "r": [_anillo_plano(a) for a in d["r"]]} for d in DISTRITOS_NORTE]
    return json.dumps(perfil, separators=(",", ":")), json.dumps(distritos, ensure_ascii=False, separators=(",", ":"))


def render_cesium(df, alto=560):
    riesgos = {
        ALIAS_MODELO_A_BUSCADOR.get(r["canton"], r["canton"]): {
            "riesgo": float(r["riesgo"]), "alerta": r["alerta"], "fecha": r["fecha"].isoformat(), "horizonte": r["horizonte"],
        }
        for _, r in df.iterrows()
    }
    puntos = [
        {
            "canton": r["canton"], "provincia": r["provincia"],
            "lat": float(r["lat"]), "lon": float(r["lon"]),
            "riesgo": float(r["riesgo"]), "alerta": r["alerta"],
            "lluvia_mm": float(r["lluvia_mm"]), "mar_idx": float(r["mar_idx"]), "cauce_idx": float(r["cauce_idx"]),
            "elevacion": float(r["elevacion"]), "fecha": r["fecha"].isoformat(),
            "horizonte": r["horizonte"],
        }
        for _, r in df.iterrows()
    ]
    perfil_json, distritos_json = _geometrias_visor()
    html = (
        CESIUM_HTML_TEMPLATE
        .replace("__CDN__", CESIUM_CDN)
        .replace("__PUNTOS__", json.dumps(puntos, ensure_ascii=False))
        .replace("__COLORES__", json.dumps(COLORES_ALERTA, ensure_ascii=False))
        .replace("__N_MODELO__", str(len(CANTONES)))
        .replace("__PERFIL__", perfil_json)
        .replace("__CANTONES__", distritos_json)
        .replace("__RIESGOS__", json.dumps(riesgos, ensure_ascii=False, separators=(",", ":")))
        .replace("__SUSC__", _susceptibilidad_uri())
    )
    components.html(html, height=alto, scrolling=False)


# ==========================================
# 2. CONFIGURACIÓN DE PÁGINA
# ==========================================
st.set_page_config(
    page_title="Monitoreo ENSO Perú - Piloto costa norte",
    layout="wide",
    page_icon="⛈️"
)

st.markdown(
    """
    <style>
    .stApp { background-color: #E5E7EB !important; }
    [data-testid="stSidebar"] { background-color: #1D3557 !important; color: #FFFFFF !important; }
    [data-testid="stSidebar"] p, [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3, [data-testid="stSidebar"] span { color: #FFFFFF !important; }
    [data-testid="stSidebar"] input { color: #000000 !important; font-weight: bold !important; }
    /* Campos de fecha: fondo blanco y texto negro (día, mes, año y separadores),
       sea cual sea el tema claro/oscuro del navegador */
    [data-testid="stSidebar"] [data-testid="stDateInputField"] {
        background-color: #FFFFFF !important;
        border-radius: 6px !important;
    }
    [data-testid="stSidebar"] [data-testid="stDateInputField"],
    [data-testid="stSidebar"] [data-testid="stDateInputField"] *,
    [data-testid="stSidebar"] [data-testid="stDateInputField"] span,
    [data-testid="stSidebar"] [data-testid="stDateInputField"] input {
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
        font-weight: bold !important;
    }
    [data-testid="stSidebar"] [data-testid="stDateInput"] svg { fill: #000000 !important; color: #000000 !important; }
    [data-testid="stSidebar"] p.titulo-lateral {
        font-size: 20px !important; font-weight: bold !important; line-height: 1.3 !important;
        color: #FFFFFF !important; margin-bottom: 12px !important;
    }
    /* Botón «Actualizar datos»: azul claro (antes blanco) con texto azul marino */
    [data-testid="stSidebar"] .stButton button, [data-testid="stSidebar"] [data-testid^="stBaseButton"] {
        background-color: #7DD3FC !important; border: 1px solid #38BDF8 !important; color: #1D3557 !important;
        font-weight: 700 !important;
    }
    [data-testid="stSidebar"] .stButton button:hover, [data-testid="stSidebar"] [data-testid^="stBaseButton"]:hover {
        background-color: #BAE6FD !important; border-color: #7DD3FC !important; color: #1D3557 !important;
    }
    [data-testid="stSidebar"] .stButton button p, [data-testid="stSidebar"] [data-testid^="stBaseButton"] p { color: #1D3557 !important; }
    [data-testid="stSidebar"] .panel-enso { font-size: 13px; line-height: 1.55; }
    [data-testid="stSidebar"] .panel-enso small { color: #CBD5E1; }
    /* Sin accesos a GitHub para los visitantes: se oculta la barra de acciones de Streamlit Cloud (Compartir, favorito,
       «editar», icono de GitHub), el botón Deploy y cualquier enlace al repositorio. Es solo visual: no protege el código. */
    [data-testid="stToolbarActions"], [data-testid="stAppDeployButton"], [data-testid="stToolbarActionButton"],
    [data-testid="stBaseButton-header"], [data-testid="stStatusWidget"],
    a[href*="github.com"], a[href*="githubusercontent.com"], [aria-label*="github" i], [aria-label="edit"], [aria-label="favorite"],
    [class*="viewerBadge"], [class*="ViewerBadge"], a[href*="streamlit.io/cloud"], a[href^="https://share.streamlit.io"] {
        display: none !important; visibility: hidden !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)


# ==========================================
# 3. DATOS EN VIVO (NOAA, ENFEN, CIIFEN) Y MODELO DE RIESGO
# ==========================================
@st.cache_data(show_spinner="Cargando los últimos datos guardados…")
def cargar_datos_reales(fuentes=()):
    """Lee los últimos datos guardados (SQLite o la semilla del repositorio): al entrar NO se descarga nada de internet.
    Se renuevan solo con el botón «Actualizar datos», que descarga de nuevo y vacía esta caché."""
    datos = fuentes_datos.cargar_todo(CANTONES)
    if all((datos.get(k) or {}).get("_estado") == "error" for k in ("gfs", "cfsv2")):
        raise RuntimeError("No se pudo descargar ningún pronóstico de lluvia (NOAA GFS / CFSv2).")
    return datos


@st.cache_resource
def _actualizador_diario():
    """Hilo en segundo plano que renueva los datos cuando cambia el día (solo mientras la app esté despierta). Se crea una sola
    vez por proceso; los módulos se recargan en cada ejecución, por eso se guarda aquí y no en el módulo."""
    return fuentes_datos.iniciar_actualizador_diario(CANTONES, al_actualizar=st.cache_data.clear)


def categoria_oni(valor):
    """Categorías de NOAA para el ONI."""
    if valor >= 2.0:
        return "El Niño muy fuerte"
    if valor >= 1.5:
        return "El Niño fuerte"
    if valor >= 1.0:
        return "El Niño moderado"
    if valor >= 0.5:
        return "El Niño débil"
    if valor <= -0.5:
        return "La Niña"
    return "neutral"


def _punto(fuente):
    estado = (fuente or {}).get("_estado")
    return {"ok": "🟢", "respaldo": "🟡"}.get(estado, "🔴")


def panel_enso_html(datos, indice_enso):
    """Resumen de las fuentes en vivo para la barra lateral (todo el texto externo va escapado)."""
    e = html.escape
    noaa, enfen, ciifen, senamhi = datos.get("noaa"), datos.get("enfen"), datos.get("ciifen"), datos.get("senamhi")
    lineas = []
    if (noaa or {}).get("_estado") != "error" and noaa:
        oni, sem = noaa["oni"], noaa["semanal"]
        lineas.append(
            f"{_punto(noaa)} <b>NOAA</b> · ONI {e(oni['trimestre'])}: <b>{oni['anomalia']:+.2f} °C</b> "
            f"({categoria_oni(oni['anomalia'])})<br>&nbsp;&nbsp;&nbsp;Niño 1+2: {sem['nino12']:+.1f} °C · "
            f"Niño 3.4: {sem['nino34']:+.1f} °C (semana del {sem['semana'].strftime('%d-%m-%Y')})")
    else:
        lineas.append("🔴 <b>NOAA</b> · ENSO no disponible")
    if enfen and enfen.get("_estado") != "error":
        txt = f"{_punto(enfen)} <b>ENFEN · Comunicado {e(str(enfen.get('comunicado')))}</b> ({e(str(enfen.get('fecha')))})"
        if enfen.get("estado"):
            txt += f"<br>&nbsp;&nbsp;&nbsp;Estado: <b>{e(enfen['estado'])}</b>"
        if enfen.get("magnitud_nino12"):
            txt += f"<br>&nbsp;&nbsp;&nbsp;Niño costero (1+2): magnitud {e(enfen['magnitud_nino12'])} hasta {e(str(enfen.get('hasta_nino12')))}"
        if enfen.get("rios_caudal_alto"):
            txt += f"<br>&nbsp;&nbsp;&nbsp;Caudales sobre lo normal: {e(', '.join(enfen['rios_caudal_alto']))}"
        if enfen.get("proximo"):
            txt += f"<br>&nbsp;&nbsp;&nbsp;Próximo comunicado: {e(enfen['proximo'])}"
        lineas.append(txt)
    else:
        lineas.append("🔴 <b>ENFEN</b> no disponible")
    if senamhi and senamhi.get("_estado") != "error":
        norte = fuentes_datos.avisos_costa_norte(senamhi, fecha_actual_peru())
        txt = f"{_punto(senamhi)} <b>SENAMHI</b> · avisos vigentes en la costa norte: <b>{len(norte)}</b>"
        for a in norte[:3]:
            txt += f"<br>&nbsp;&nbsp;&nbsp;N.° {a['numero']} · nivel {e(a['nivel'])} (hasta {a['fin'].strftime('%d-%m')})"
        lineas.append(txt)
    else:
        lineas.append("🔴 <b>SENAMHI</b> (avisos) no disponible")
    if ciifen and ciifen.get("_estado") != "error":
        lineas.append(f"{_punto(ciifen)} <b>CIIFEN</b> · boletín {e(ciifen['boletin'])}:<br>&nbsp;&nbsp;&nbsp;{e(ciifen['estado'])}")
    else:
        lineas.append("🔴 <b>CIIFEN</b> no disponible")
    lluvia = f"{_punto(datos.get('gfs'))} NOAA GFS (16 días) · {_punto(datos.get('cfsv2'))} CFSv2 (10 meses)"
    lineas.append(f"<b>Lluvia</b>: {lluvia}")
    lineas.append(f"<b>Índice ENSO del modelo</b>: {indice_enso:.2f} (refuerza la lluvia hasta +{ENSO_MAX_AMPLIFICACION * 100:.0f} %)")
    try:
        hora = datos["_cargado"].astimezone(ZoneInfo("America/Lima")).strftime("%d-%m-%Y %H:%M")
    except Exception:
        hora = "—"
    lineas.append(f"<small>Datos descargados: {hora} (hora de Perú). Se renuevan al pulsar «Actualizar datos».</small>")
    return '<div class="panel-enso">' + "<br>".join(lineas) + "</div>"


_actualizador_diario()
try:
    DATOS = cargar_datos_reales(fuentes_datos.FUENTES)
except Exception as exc:  # noqa: BLE001
    st.error(f"No se pudieron obtener los datos en vivo: {exc} Intenta de nuevo en unos minutos.")
    st.stop()

registros, INDICE_ENSO = construir_registros(DATOS, hoy=fecha_actual_peru())
if not registros:
    st.error("Las fuentes respondieron sin datos utilizables. Intenta de nuevo en unos minutos.")
    st.stop()
df_original = pd.DataFrame(registros)
try:                      # historial local (SQLite): una «foto» por descarga; nunca debe romper la app
    almacen.guardar_riesgo(DATOS["_cargado"], registros)
except Exception:  # noqa: BLE001
    pass

# ==========================================
# 4. INTERFAZ LATERAL (FILTROS)
# ==========================================
st.sidebar.markdown(
    '<p class="titulo-lateral">⛈️ Sistema de Alerta Temprana y Riesgo de Inundación (ENSO) Costa Norte del Perú · Piloto</p>',
    unsafe_allow_html=True,
)
st.sidebar.header("⚙️ Filtros por fechas de probable inundación")
def _recalcular_proyeccion():
    """Al cambiar la fecha inicial, la proyección vuelve a ser un mes después."""
    st.session_state["fecha_fin"] = sumar_un_mes(st.session_state["fecha_inicio"])


if "fecha_fin" not in st.session_state:
    st.session_state["fecha_fin"] = sumar_un_mes(fecha_actual_peru())

fecha_inicio = st.sidebar.date_input(
    "Fecha Inicial", fecha_actual_peru(), key="fecha_inicio", on_change=_recalcular_proyeccion
)
fecha_fin = st.sidebar.date_input(
    "Fecha de Proyección", key="fecha_fin",
    help="Se calcula sola: un mes después de la fecha inicial. También puedes cambiarla a mano.",
)
umbral_riesgo = st.sidebar.slider("Índice de Riesgo Mínimo", 0.0, 1.0, 0.40)

LOGO_LINKEDIN = (
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="#0A66C2" aria-hidden="true" '
    'style="vertical-align:middle;margin-right:7px;background:#FFFFFF;border-radius:4px;padding:1px;">'
    '<path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414'
    'v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 '
    '2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 '
    '1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>'
)
def foto_autor_base64():
    """Foto de perfil del autor (archivo del repositorio) en base64; None si no está."""
    try:
        return base64.b64encode(Path(__file__).with_name("foto_autor.jpg").read_bytes()).decode()
    except OSError:
        return None


URL_LINKEDIN = "https://www.linkedin.com/in/raul-acevedo-76360130/"
_foto = foto_autor_base64()
_avatar = (
    f'<a href="{URL_LINKEDIN}" target="_blank" rel="noopener noreferrer" title="Perfil de LinkedIn">'
    f'<img src="data:image/jpeg;base64,{_foto}" alt="Raúl Acevedo" '
    'style="width:44px;height:44px;border-radius:50%;object-fit:cover;border:2px solid #7DD3FC;display:block;"></a>'
    if _foto else ""
)
st.sidebar.markdown(
    '<div style="display:flex;align-items:center;gap:12px;margin-top:6px;">'
    + _avatar +
    '<div style="line-height:1.5;">Autor: <b>Raúl Acevedo</b><br>'
    f'<a href="{URL_LINKEDIN}" target="_blank" rel="noopener noreferrer" '
    'style="color:#7DD3FC !important;text-decoration:none;">' + LOGO_LINKEDIN + 'LinkedIn</a></div></div>',
    unsafe_allow_html=True,
)

st.sidebar.markdown("---")
st.sidebar.header("🌊 Estado ENSO en vivo")
st.sidebar.markdown(panel_enso_html(DATOS, INDICE_ENSO), unsafe_allow_html=True)
if st.sidebar.button("🔄 Actualizar datos", help="Descarga de nuevo NOAA, ENFEN y CIIFEN (tarda unos segundos). Al entrar a la aplicación no se descarga nada."):
    with st.spinner("Descargando datos de NOAA, ENFEN y CIIFEN…"):
        resultado = fuentes_datos.actualizar_todo(CANTONES)
    if resultado is None:
        st.sidebar.info("Otra persona ya está actualizando los datos; vuelve a cargar en unos segundos.")
    else:
        st.cache_data.clear()                 # la próxima lectura toma los datos recién guardados
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("🗺️ Visor Geográfico")
st.sidebar.caption("El cambio de mapa base (Esri / Topográfico / Base OSM / OSM transparente) y las capas NOAA (lluvia, viento) y satélite MODIS están dentro del visor.")
st.sidebar.caption(
    "**Fuentes y créditos**  \n"
    "Base: © colaboradores de OpenStreetMap  \n"
    "Topografía: © colaboradores de OpenStreetMap, SRTM | © OpenTopoMap (CC-BY-SA)  \n"
    "Imagen: © Esri, Maxar, Earthstar Geographics  \n"
    "Predicción: NOAA GFS (vía Open-Meteo) y NOAA nowCOAST  \n"
    "Susceptibilidad a inundación: Copernicus DEM GLO-30 © ESA (AWS Open Data)  \n"
    "ENSO: NOAA CPC (ONI, Niño) · ENFEN (comunicado oficial) · CIIFEN (boletín El Niño/La Niña)  \n"
    "Avisos meteorológicos: SENAMHI (tabla pública de avisos)  \n"
    "Pronóstico estacional: CCSR/IRI, Columbia Climate School (CFSv2 de NOAA/NCEP)  \n"
    "Satélite: NASA GIBS / MODIS  \n"
    "Perfil de Perú: geoBoundaries (CC0)  \n"
    "Distritos: INEI, IDE INEI (límite distrital 2023)  \n"
    "Visor: CesiumJS"
)

df_filtrado = df_original[
    (df_original['fecha'] >= fecha_inicio) &
    (df_original['fecha'] <= fecha_fin) &
    (df_original['riesgo'] >= umbral_riesgo)
].copy()

if df_filtrado.empty:
    st.info("No hay registros climáticos o alertas que coincidan con los filtros seleccionados.")
    st.stop()

# Para cada distrito se muestra el peor escenario (mayor riesgo) dentro de las fechas elegidas
df_mapa_reciente = (
    df_filtrado.sort_values(['riesgo', 'fecha'], ascending=[False, True])
    .groupby('canton', sort=False).first().reset_index()
)

# ==========================================
# 5. VISOR GEOGRÁFICO PRINCIPAL (CESIUM 2D/3D)
# ==========================================
render_cesium(df_mapa_reciente, alto=560)

NOMBRES_FUENTES = {"noaa": "NOAA (ONI y Niño)", "gfs": "NOAA GFS (lluvia diaria)", "cfsv2": "CFSv2 (lluvia mensual)",
                   "enfen": "ENFEN (comunicado oficial)", "senamhi": "SENAMHI (avisos meteorológicos)", "ciifen": "CIIFEN (boletín)"}
for clave, nombre in NOMBRES_FUENTES.items():
    estado = (DATOS.get(clave) or {}).get("_estado")
    if estado == "error":
        st.warning(f"Fuente no disponible: {nombre}. El modelo sigue con las demás, sin este dato.")
    elif estado == "respaldo":
        st.info(f"{nombre}: no respondió; se usa el último dato descargado.")
for aviso in fuentes_datos.avisos_costa_norte(DATOS.get("senamhi"), fecha_actual_peru()):
    texto_aviso = (f"**SENAMHI · Aviso N.° {aviso['numero']}:** {aviso['titulo'].capitalize()}. Nivel **{aviso['nivel']}**, "
                   f"del {aviso['inicio'].strftime('%d-%m')} al {aviso['fin'].strftime('%d-%m-%Y')}.")
    (st.error if aviso["nivel"] in ("ROJO", "NARANJA") else st.warning)(texto_aviso)
st.caption("El mapa y el gráfico muestran, para cada distrito, el **peor escenario** (mayor riesgo) dentro de las fechas elegidas.")

# ==========================================
# 6. INDICADORES, GRÁFICO Y TABLA
# ==========================================
c1, c2, c3 = st.columns(3)
c1.metric("Distritos en alerta", len(df_mapa_reciente))
c2.metric("Riesgo máximo", f"{int(df_mapa_reciente['riesgo'].max() * 100)}%")
c3.metric("Lluvia máx. (día o mes)", f"+{df_mapa_reciente['lluvia_mm'].max():.0f} mm")

df_barras = df_mapa_reciente.sort_values('riesgo', ascending=False)
fig = go.Figure(go.Bar(
    x=df_barras['canton'],
    y=df_barras['riesgo'] * 100,
    marker_color=[COLORES_ALERTA.get(a, "#FFA500") for a in df_barras['alerta']],
    text=[f"{int(v * 100)}%" for v in df_barras['riesgo']],
    textposition="outside",
))
fig.update_layout(
    title="Índice de riesgo por distrito (%)",
    yaxis=dict(range=[0, 110]),
    height=360,
    margin=dict(l=20, r=20, t=50, b=20),
    paper_bgcolor="#E5E7EB",
    plot_bgcolor="#F9FAFB",
)
st.plotly_chart(fig, use_container_width=True)

st.subheader("📋 Detalle de alertas")
df_tabla = (
    df_filtrado.sort_values(['fecha', 'riesgo'], ascending=[True, False])
    [['fecha', 'canton', 'provincia', 'lluvia_mm', 'mar_idx', 'alerta', 'horizonte']]
    .reset_index(drop=True)
)
st.dataframe(
    df_tabla.style.apply(estilo_filas_alertas, axis=1),
    use_container_width=True, hide_index=True,
    column_config={
        "fecha": "Fecha", "canton": "Distrito", "provincia": "Departamento",
        "lluvia_mm": st.column_config.NumberColumn("Lluvia (mm, día o mes)", format="%.1f"),
        "mar_idx": st.column_config.NumberColumn("Mar (índice 0-1)", format="%.1f"),
        "alerta": "Alerta", "horizonte": "Pronóstico",
    },
)

# ==========================================
# 7. HISTÓRICO (SQLite local)
# ==========================================
MES_CENTRAL = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6, "JJA": 7, "JAS": 8,
               "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}

with st.expander("📈 Histórico del ENSO y evolución del riesgo (almacén SQLite)"):
    try:
        fuentes_datos.asegurar_series_enso()      # si el almacén está vacío, descarga las series completas
        oni_hist = pd.DataFrame(almacen.leer_oni(1950))
        nino_hist = pd.DataFrame(almacen.leer_nino_semanal("1981-01-01"))
        evolucion = pd.DataFrame(almacen.leer_evolucion_riesgo())
        estado_db = almacen.resumen()
    except Exception as exc:  # noqa: BLE001
        oni_hist = nino_hist = evolucion = pd.DataFrame()
        estado_db = None
        st.warning(f"El almacén local no está disponible: {exc}")

    if not oni_hist.empty:
        oni_hist["fecha"] = [datetime.date(int(a), MES_CENTRAL.get(t, 6), 15) for a, t in zip(oni_hist["anio"], oni_hist["trimestre"])]
        fig_oni = go.Figure(go.Bar(
            x=oni_hist["fecha"], y=oni_hist["anomalia"],
            marker_color=["#DC2626" if v >= 0.5 else ("#2563EB" if v <= -0.5 else "#9CA3AF") for v in oni_hist["anomalia"]],
        ))
        fig_oni.add_hline(y=0.5, line_dash="dot", line_color="#DC2626")
        fig_oni.add_hline(y=-0.5, line_dash="dot", line_color="#2563EB")
        fig_oni.update_layout(title="Índice Oceánico de El Niño (ONI), 1950 – hoy (°C) · rojo: El Niño, azul: La Niña",
                              height=300, margin=dict(l=20, r=20, t=50, b=20), paper_bgcolor="#E5E7EB", plot_bgcolor="#F9FAFB")
        st.plotly_chart(fig_oni, use_container_width=True)
    if not nino_hist.empty:
        fig_n = go.Figure()
        fig_n.add_trace(go.Scatter(x=nino_hist["semana"], y=nino_hist["nino12"], name="Niño 1+2 (costa de Perú)", line=dict(color="#DC2626", width=1.5)))
        fig_n.add_trace(go.Scatter(x=nino_hist["semana"], y=nino_hist["nino34"], name="Niño 3.4 (Pacífico central)", line=dict(color="#1D3557", width=1.5)))
        fig_n.add_hline(y=0, line_color="#6B7280")
        fig_n.update_layout(title="Anomalía semanal de la temperatura del mar, 1981 – hoy (°C)", height=300,
                            margin=dict(l=20, r=20, t=50, b=20), paper_bgcolor="#E5E7EB", plot_bgcolor="#F9FAFB",
                            legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig_n, use_container_width=True)

    n_fotos = evolucion["descarga_utc"].nunique() if not evolucion.empty else 0
    if n_fotos >= 2:
        # Las fotos guardadas pueden tener formatos ISO distintos (con y sin microsegundos): se leen todas como ISO 8601
        evolucion["momento"] = pd.to_datetime(evolucion["descarga_utc"], format="ISO8601", utc=True).dt.tz_convert("America/Lima")
        fig_e = go.Figure()
        for canton_nombre, g in evolucion.groupby("canton"):
            fig_e.add_trace(go.Scatter(x=g["momento"], y=g["riesgo_max"] * 100, name=canton_nombre, mode="lines+markers"))
        fig_e.update_layout(title="Evolución del riesgo máximo por distrito en cada descarga (%)", height=320,
                            margin=dict(l=20, r=20, t=50, b=20), paper_bgcolor="#E5E7EB", plot_bgcolor="#F9FAFB")
        st.plotly_chart(fig_e, use_container_width=True)
    elif estado_db:
        st.info("La evolución del riesgo se dibuja cuando hay al menos dos descargas guardadas "
                f"(ahora hay {estado_db['fotos_riesgo']}). Se guarda una cada vez que se actualizan los datos con el botón «Actualizar datos».")

    if estado_db:
        st.caption(
            f"Almacén SQLite: {estado_db['oni']} trimestres del ONI · {estado_db['nino_semanal']} semanas de Niño · "
            f"{estado_db['fotos_riesgo']} descargas de riesgo guardadas ({estado_db['tamano_kb']:.0f} KB). "
            "En Streamlit Cloud los archivos locales se borran al reiniciar la app: las series del ENSO se vuelven a "
            "descargar completas, pero el historial del riesgo solo dura mientras el servidor siga encendido."
        )

with st.expander("ℹ️ Cómo se calcula el riesgo (modelo indicativo)"):
    wi, wd = PESOS["inundacion"], PESOS["deslave"]
    st.markdown(
        f"""
**El riesgo (0 a 100 %)** de cada distrito del piloto combina datos en vivo de varias fuentes:

`riesgo = peso_lluvia · lluvia × (1 + {ENSO_MAX_AMPLIFICACION:.2f} · índice ENSO) + peso_mar · mar + peso_exposición · exposición + peso_cauce · caudal`

| Amenaza | Lluvia | Mar | Exposición | Caudal del río |
|---|---|---|---|---|
| Inundación | {wi['lluvia']:.0%} | {wi['mar']:.0%} | {wi['expo']:.0%} (baja elevación) | {wi['cauce']:.0%} |
| Huaico | {wd['lluvia']:.0%} | {wd['mar']:.0%} | {wd['expo']:.0%} (pendiente supuesta) | {wd['cauce']:.0%} |

- **Lluvia:** NOAA GFS por día (16 días) y CFSv2 por mes (hasta 10 meses), comparadas con el **clima propio de cada
  distrito**: 0 = lluvia típica (mediana) y 1 = lluvia extrema (percentil 99 de los días lluviosos; para los meses, el doble del
  exceso que marca el percentil 95 de ese mes del año). Así Sechura (desértico) y Tumbes (más lluvioso) no se miden con la misma vara.
- **Mar:** en distritos costeros vale 0,5 si el último comunicado del ENFEN indica que el nivel del mar se mantendría por encima de lo
  normal. Es un indicador **cualitativo**: el ENFEN no publica una cifra única; está pendiente incorporar el nivel del mar observado.
- **Caudal del río:** vale 1 si el río del distrito figura entre los que el ENFEN prevé con caudales por encima de lo normal.
- **Índice ENSO:** promedio del ONI (NOAA CPC, saturado en 2 °C) y de la anomalía semanal de Niño 1+2 (saturada en 4 °C).
- **Alertas:** crítico desde {UMBRALES['critico']:.0%}, alto riesgo desde {UMBRALES['alto']:.0%}, alerta amarilla desde
  {UMBRALES['amarilla']:.0%}; en huaico, «medio» desde {UMBRALES['deslave_medio']:.0%}; por debajo, bajo. Sin lluvia, el mar,
  la exposición y los caudales nunca pasan de «bajo».

**Piloto para demostración:** los pesos y umbrales vienen de la calibración de Ecuador y **aún no se han validado con eventos de
Perú** (El Niño costero, emergencias del INDECI). Es un modelo sencillo y **no sustituye** los comunicados oficiales del ENFEN ni
los avisos del SENAMHI, el INDECI y el CENEPRED. La información del ENFEN se lee de su página web y la de CIIFEN de su boletín en PDF;
si cambian de formato, esa fuente puede quedar sin datos y la app lo avisará.

Fuentes: [NOAA CPC · ONI]({fuentes_datos.URL_ONI}) · [NOAA CPC · Niño semanal]({fuentes_datos.URL_NINO_SEMANAL}) ·
[ENFEN · comunicados oficiales]({fuentes_datos.URL_ENFEN}) · [SENAMHI · avisos meteorológicos]({fuentes_datos.URL_SENAMHI_AVISOS}) · [CIIFEN · boletines El Niño/La Niña]({fuentes_datos.URL_CIIFEN_LISTA}) ·
[NOAA GFS vía Open-Meteo](https://open-meteo.com/en/docs/gfs-api) · [CCSR/IRI · CFSv2]({fuentes_datos.URL_CFSV2})
"""
    )
