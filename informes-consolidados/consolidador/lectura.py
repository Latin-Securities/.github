"""Lectura de exports (Excel/CSV) con encabezados variables."""
from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata
from pathlib import Path

import pandas as pd

from .config import Config
from .motor import Movimiento, PosicionInicial, Precio


def normalizar(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", texto.lower())


def a_numero(valor) -> float | None:
    """Acepta números o textos en formato argentino ("1.234,56") o inglés ("1,234.56")."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return None if isinstance(valor, float) and math.isnan(valor) else float(valor)
    texto = re.sub(r"[^\d,.\-()]", "", str(valor))
    if not texto or texto in {"-", "."}:
        return None
    negativo = texto.startswith("(") and texto.endswith(")") or texto.startswith("-")
    texto = texto.strip("()-")
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".") if texto.count(",") == 1 else texto.replace(",", "")
    elif texto.count(".") > 1:
        texto = texto.replace(".", "")
    try:
        numero = float(texto)
    except ValueError:
        return None
    return -numero if negativo else numero


def a_fecha(valor) -> dt.date | None:
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    fecha = pd.to_datetime(str(valor).strip(), dayfirst=True, errors="coerce")
    return None if pd.isna(fecha) else fecha.date()


def _leer_crudo(ruta: Path) -> pd.DataFrame:
    if ruta.suffix.lower() == ".csv":
        for encoding in ("utf-8-sig", "latin-1"):
            try:
                return pd.read_csv(ruta, header=None, sep=None, engine="python", encoding=encoding, dtype=str)
            except UnicodeDecodeError:
                continue
    return pd.read_excel(ruta, header=None)


def leer_tabla(ruta: Path, mapeo: dict[str, list[str]], obligatorios: list[str]) -> pd.DataFrame:
    """Busca la fila de encabezados (los exports suelen traer títulos arriba) y
    devuelve un DataFrame con las columnas renombradas a los nombres internos."""
    crudo = _leer_crudo(ruta)
    alias = {campo: [normalizar(a) for a in lista] for campo, lista in mapeo.items()}

    for fila in range(min(len(crudo), 30)):
        encabezados = [normalizar(v) for v in crudo.iloc[fila].tolist()]
        columnas: dict[str, int] = {}
        for campo, opciones in alias.items():
            for opcion in opciones:  # respeta el orden de preferencia de los alias
                if opcion in encabezados:
                    columnas[campo] = encabezados.index(opcion)
                    break
        if all(c in columnas for c in obligatorios):
            datos = crudo.iloc[fila + 1:, list(columnas.values())]
            datos.columns = list(columnas.keys())
            datos = datos.dropna(how="all")
            return datos.reset_index(drop=True)

    faltan = ", ".join(f"{c} ({' / '.join(mapeo[c][:3])}...)" for c in obligatorios)
    raise ValueError(
        f"{ruta.name}: no encontré una fila de encabezados con las columnas {faltan}. "
        "Agregá el nombre que usa el archivo en el config.yaml del cliente."
    )


def _texto(valor) -> str:
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)  # números de cuenta que Excel guarda como 12345.0
    return str(valor).strip()


def clasificar_tipo(texto: str, cfg: Config) -> str | None:
    t = normalizar(texto)
    if any(t.startswith(normalizar(x)) for x in cfg.tipos_venta):
        return "VENTA"
    if any(t.startswith(normalizar(x)) for x in cfg.tipos_compra):
        return "COMPRA"
    return None


def leer_movimientos(cfg: Config, alertas: list[str]) -> list[Movimiento]:
    movimientos: list[Movimiento] = []
    vistos: set[tuple] = set()
    for ruta in cfg.movimientos:
        obligatorios = ["fecha", "tipo", "instrumento", "cantidad", "precio"]
        tabla = leer_tabla(ruta, cfg.columnas_movimientos, obligatorios)
        ignorados: dict[str, int] = {}
        for i, fila in tabla.iterrows():
            fecha = a_fecha(fila["fecha"])
            instrumento = _texto(fila["instrumento"]).upper()
            if fecha is None or not instrumento:
                continue  # filas de totales o notas al pie
            tipo_original = _texto(fila["tipo"])
            tipo = clasificar_tipo(tipo_original, cfg)
            if tipo is None:
                ignorados[tipo_original] = ignorados.get(tipo_original, 0) + 1
                continue
            cantidad = a_numero(fila["cantidad"])
            precio = a_numero(fila["precio"])
            if not cantidad or precio is None:
                alertas.append(f"{ruta.name} fila {i + 1}: {instrumento} sin cantidad o precio; se ignoró.")
                continue
            # Sin columna de cuenta (un archivo por cuenta) se usa el nombre del archivo.
            cuenta = _texto(fila["cuenta"]) if "cuenta" in tabla.columns else ""
            cuenta = cuenta or ruta.stem
            monto = a_numero(fila["monto"]) if "monto" in tabla.columns else None
            mov = Movimiento(fecha, cuenta, tipo, instrumento, abs(cantidad), precio,
                             abs(monto) if monto else None, ruta.name)
            clave = (mov.fecha, mov.cuenta, mov.tipo, mov.instrumento, mov.cantidad, mov.precio)
            if clave in vistos:
                alertas.append(
                    f"{ruta.name}: operación repetida en otro archivo ({mov.fecha:%d/%m/%Y} {mov.tipo} "
                    f"{mov.cantidad:g} {mov.instrumento}, cuenta {mov.cuenta}); se contó una sola vez."
                )
                continue
            vistos.add(clave)
            movimientos.append(mov)
        for tipo_original, n in ignorados.items():
            alertas.append(
                f"{ruta.name}: {n} movimiento(s) de tipo '{tipo_original}' no son compra ni venta y se "
                "ignoraron (si corresponde, agregalo a tipos_compra/tipos_venta en el config)."
            )
    return movimientos


def leer_posiciones_iniciales(cfg: Config) -> list[PosicionInicial]:
    if cfg.posiciones_iniciales is None:
        return []
    ruta = cfg.posiciones_iniciales
    tabla = leer_tabla(ruta, cfg.columnas_posiciones, ["instrumento", "cantidad", "precio_compra"])
    posiciones = []
    for _, fila in tabla.iterrows():
        instrumento = _texto(fila["instrumento"]).upper()
        cantidad = a_numero(fila["cantidad"])
        if not instrumento or not cantidad or instrumento.startswith("TOTAL"):
            continue
        cuenta = (_texto(fila["cuenta"]) if "cuenta" in tabla.columns else "") or ruta.stem
        posiciones.append(PosicionInicial(cuenta, instrumento, cantidad, a_numero(fila["precio_compra"]) or 0.0))
    return posiciones


def leer_precios(cfg: Config) -> dict[str, Precio]:
    tabla = leer_tabla(cfg.precios, cfg.columnas_precios, ["instrumento", "precio"])
    precios: dict[str, Precio] = {}
    for _, fila in tabla.iterrows():
        instrumento = _texto(fila["instrumento"]).upper()
        precio = a_numero(fila["precio"])
        if not instrumento or precio is None:
            continue
        moneda = _texto(fila["moneda"]).upper() if "moneda" in tabla.columns else ""
        base = a_numero(fila["base"]) if "base" in tabla.columns else None
        descripcion = _texto(fila["descripcion"]) if "descripcion" in tabla.columns else ""
        precios[instrumento] = Precio(instrumento, precio, moneda or cfg.moneda_reporte, base, descripcion)
    return precios
