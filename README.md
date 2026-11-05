# Monitoreo ENSO Perú · Piloto costa norte

Sistema de alerta temprana y riesgo de inundación por El Niño para la **costa norte del Perú** (Tumbes, Piura, Lambayeque y La Libertad).
Proyecto GeoCiencias — Autor y mentor: **Raúl Acevedo**. Es la réplica para Perú del sistema «Monitoreo ENSO Ecuador»
y acompaña la *Propuesta técnica* del 5 de noviembre de 2026. **Piloto para demostración: indicativo, no sustituye los
comunicados oficiales del ENFEN ni los avisos del SENAMHI, el INDECI y el CENEPRED.**

## Qué hace
- **Visor geográfico 3D (CesiumJS)** con mapas base (Esri, OpenTopoMap, OSM), perfil de Perú, buscador de **199 distritos**
  (INEI) y capas con línea de tiempo: NOAA (GFS lluvia/viento, GOES, temperatura del mar), NASA MODIS, pronóstico estacional CCSR/IRI
  y una capa de **susceptibilidad del terreno a la inundación** (Copernicus DEM, ~185 m).
- **Modelo de riesgo para 30 distritos piloto**: lluvia frente al clima local + mar + exposición del terreno + caudal del río,
  con refuerzo por la intensidad del ENSO (hasta 15 %). Cinco niveles de alerta.
- **Datos en vivo**; al entrar a la aplicación **no se descarga nada** (carga rápida): se muestran los últimos datos guardados y se
  renuevan con el botón «Actualizar datos» o con la tarea diaria de GitHub Actions (06:00 hora de Perú).

| Fuente | Qué se usa | Archivo |
|---|---|---|
| ENFEN (IMARPE, DHN, IGP, SENAMHI, ANA, INDECI) | Último comunicado oficial: estado de alerta, magnitud de El Niño costero (Niño 1+2) y Niño 3.4, ríos con caudales sobre lo normal, nivel del mar | `fuentes_datos.py` |
| NOAA CPC | ONI y temperaturas del mar Niño 1+2, 3, 3.4 y 4 | `fuentes_datos.py` |
| NOAA GFS (vía Open-Meteo) | Lluvia diaria por distrito, 16 días | `fuentes_datos.py` |
| CCSR/IRI (CFSv2 de NOAA) | Lluvia mensual por distrito, 10 meses | `fuentes_datos.py` |
| CIIFEN | Boletín mensual «El Niño/La Niña en América Latina» | `fuentes_datos.py` |
| INEI (IDE) | Límites distritales (2023) | `distritos_norte.py` |
| Copernicus DEM GLO-30 (ESA) | Capa de susceptibilidad a inundación | `generar_susceptibilidad.py` |

## Limitaciones del piloto (honestas)
- Pesos y umbrales heredados de Ecuador, **sin validar con eventos de Perú** (siguiente fase: PISCO del SENAMHI y emergencias del INDECI).
- El **mar** es un indicador cualitativo (sí/no según el ENFEN); falta el nivel del mar observado (DHN / UHSLC).
- El **caudal** sale de la lista de ríos del comunicado del ENFEN, no de mediciones de la ANA.
- La climatología de lluvia usa GFS 2022-2025 y CFSv2 archivados; está pendiente contrastarla con las estaciones del SENAMHI.

## Archivos
- `app.py` — interfaz Streamlit y visor Cesium.
- `fuentes_datos.py` — lectores de las fuentes (con respaldo del último dato bueno si una fuente falla).
- `modelo_riesgo.py` — catálogo de distritos piloto, parámetros y cálculo del riesgo.
- `almacen.py` — almacén local en **SQLite** (caché, series del ENSO, historial del riesgo). En Streamlit Cloud los archivos locales se borran al reiniciar.
- `datos_semilla.json` + `actualizar_semilla.py` + `.github/workflows/actualizar_datos.yml` — instantánea diaria de datos para arrancar sin descargar.
- `climatologia.json` — estadísticas locales de lluvia (se regenera con `python generar_climatologia.py`, tarda ~10 min).
- `generar_distritos.py` — genera `distritos_norte.py` (buscador) y `peru_perfil.py` (contorno) desde las capas del INEI y geoBoundaries.
- `generar_susceptibilidad.py` — genera la capa `susceptibilidad_inundacion.png/.tif/.json`.
- `test_fuentes_modelo.py` — pruebas sin red: `python -m unittest test_fuentes_modelo -v`.

## Ejecutar
```bash
pip install -r requirements.txt
streamlit run app.py
```
