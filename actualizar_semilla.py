"""Renueva ``datos_semilla.json`` descargando las fuentes (NOAA, ENFEN, CIIFEN…). Lo ejecuta cada día la tarea de GitHub
Actions (.github/workflows/actualizar_datos.yml); también se puede correr a mano:

    python actualizar_semilla.py

Parte de la semilla actual para que, si una fuente falla, se conserve su último dato bueno. Solo reescribe el archivo si al
menos la lluvia (GFS o CFSv2) se descargó bien; si no, termina con error y NO toca la semilla.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["ENSO_DB"] = str(Path(tempfile.mkdtemp()) / "semilla.sqlite")      # base limpia: no depende de nada previo

import almacen          # noqa: E402  (después de fijar ENSO_DB)
import fuentes_datos    # noqa: E402
import modelo_riesgo    # noqa: E402


def main():
    almacen.importar_semilla()                                                 # último dato bueno de cada fuente
    resultado = fuentes_datos.actualizar_todo(modelo_riesgo.CANTONES)
    estados = {k: (v or {}).get("_estado") for k, v in resultado.items() if not k.startswith("_")}
    print("Estado de las fuentes:", estados)
    if "ok" not in (estados.get("gfs"), estados.get("cfsv2")):
        print("No se pudo descargar la lluvia: la semilla NO se modifica.", file=sys.stderr)
        return 1
    almacen.exportar_semilla()
    print("Semilla actualizada:", almacen.SEMILLA)
    return 0


if __name__ == "__main__":
    sys.exit(main())
