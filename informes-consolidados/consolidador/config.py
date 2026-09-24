"""Carga de la configuración de un cliente (config.yaml)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Encabezados posibles de cada campo. Se comparan sin mayúsculas, acentos ni
# signos, así que "Fecha Concertación" == "fecha concertacion". Si el export de
# InvBroker usa otro nombre, se agrega en el config.yaml del cliente.
COLUMNAS_MOVIMIENTOS = {
    "fecha": ["Fecha", "Fecha Concertacion", "Fecha Operacion", "Fecha Conc."],
    "cuenta": ["Cuenta", "Comitente", "Nro Comitente", "Nro. Comitente", "Cuenta Comitente"],
    "tipo": ["Tipo", "Operacion", "Tipo Operacion", "Tipo de Operacion", "Movimiento"],
    "instrumento": ["Instrumento", "Especie", "Ticker", "Simbolo", "Codigo Especie"],
    "cantidad": ["Cantidad", "Cant.", "Nominales", "Cantidad VN", "Cant"],
    "precio": ["Precio", "Precio Operacion", "Cotizacion"],
    "monto": ["Monto", "Importe", "Importe Bruto", "Monto Bruto", "Total"],
}

COLUMNAS_POSICIONES = {
    "cuenta": COLUMNAS_MOVIMIENTOS["cuenta"],
    "instrumento": COLUMNAS_MOVIMIENTOS["instrumento"],
    "cantidad": COLUMNAS_MOVIMIENTOS["cantidad"],
    "precio_compra": ["Precio Compra", "Precio de Compra", "PPC", "Precio Promedio", "Costo Unitario"],
}

COLUMNAS_PRECIOS = {
    "instrumento": COLUMNAS_MOVIMIENTOS["instrumento"],
    "precio": ["Precio", "Precio Actual", "Ultimo", "Ultimo Precio", "Cierre", "Cotizacion"],
    "moneda": ["Moneda"],
    "base": ["Base", "Base Precio", "Cada"],
    "descripcion": ["Descripcion", "Nombre"],
}

TIPOS_COMPRA = ["Compra", "Cpra", "Suscripcion", "Licitacion", "Compra Contado", "Canje Ingreso"]
TIPOS_VENTA = ["Venta", "Vta", "Rescate", "Venta Contado", "Canje Egreso"]


@dataclass
class Config:
    carpeta: Path
    cliente: str
    fecha_corte: dt.date
    fecha_inicio: dt.date
    moneda_reporte: str
    tipos_cambio: dict[str, float]
    posiciones_iniciales: Path | None
    movimientos: list[Path]
    precios: Path
    salida: Path
    columnas_movimientos: dict[str, list[str]] = field(default_factory=dict)
    columnas_posiciones: dict[str, list[str]] = field(default_factory=dict)
    columnas_precios: dict[str, list[str]] = field(default_factory=dict)
    tipos_compra: list[str] = field(default_factory=list)
    tipos_venta: list[str] = field(default_factory=list)


def _a_fecha(valor, nombre: str) -> dt.date:
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    try:
        return dt.date.fromisoformat(str(valor))
    except ValueError as e:
        raise ValueError(f"'{nombre}' debe tener formato AAAA-MM-DD (vino {valor!r})") from e


def _fusionar(base: dict[str, list[str]], extra: dict | None) -> dict[str, list[str]]:
    """Los alias del config se agregan adelante de los default."""
    res = {k: list(v) for k, v in base.items()}
    for campo, alias in (extra or {}).items():
        if campo not in res:
            raise ValueError(f"Campo desconocido en el mapeo de columnas: {campo!r}")
        alias = [alias] if isinstance(alias, str) else list(alias)
        res[campo] = alias + res[campo]
    return res


def _archivos_movimientos(carpeta: Path, valor) -> list[Path]:
    entradas = [valor] if isinstance(valor, str) else list(valor or [])
    archivos: list[Path] = []
    for entrada in entradas:
        ruta = carpeta / entrada
        if ruta.is_dir():
            archivos += sorted(
                p for p in ruta.iterdir()
                if p.suffix.lower() in {".xlsx", ".xls", ".csv"} and not p.name.startswith(("~$", "."))
            )
        elif ruta.exists():
            archivos.append(ruta)
        else:
            raise FileNotFoundError(f"No existe el archivo/carpeta de movimientos: {ruta}")
    return archivos


def cargar_config(ruta: str | Path, fecha_corte: str | None = None) -> Config:
    ruta = Path(ruta)
    if ruta.is_dir():
        ruta = ruta / "config.yaml"
    carpeta = ruta.parent.resolve()
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}

    corte = _a_fecha(fecha_corte or datos.get("fecha_corte"), "fecha_corte")
    inicio = _a_fecha(datos.get("fecha_inicio", dt.date(corte.year - 1, 12, 31)), "fecha_inicio")
    archivos = datos.get("archivos", {})

    iniciales = archivos.get("posiciones_iniciales")
    if iniciales and not (carpeta / iniciales).exists():
        raise FileNotFoundError(f"No existe el archivo de posiciones iniciales: {carpeta / iniciales}")
    precios = carpeta / archivos.get("precios", "precios.xlsx")
    if not precios.exists():
        raise FileNotFoundError(f"No existe el archivo de precios: {precios}")

    return Config(
        carpeta=carpeta,
        cliente=str(datos.get("cliente", carpeta.name)),
        fecha_corte=corte,
        fecha_inicio=inicio,
        moneda_reporte=str(datos.get("moneda_reporte", "ARS")).upper(),
        tipos_cambio={str(k).upper(): float(v) for k, v in (datos.get("tipos_cambio") or {}).items()},
        posiciones_iniciales=carpeta / iniciales if iniciales else None,
        movimientos=_archivos_movimientos(carpeta, archivos.get("movimientos", "movimientos")),
        precios=precios,
        salida=carpeta / archivos.get("salida", "salida"),
        columnas_movimientos=_fusionar(COLUMNAS_MOVIMIENTOS, datos.get("columnas_movimientos")),
        columnas_posiciones=_fusionar(COLUMNAS_POSICIONES, datos.get("columnas_posiciones")),
        columnas_precios=_fusionar(COLUMNAS_PRECIOS, datos.get("columnas_precios")),
        tipos_compra=list(datos.get("tipos_compra", [])) + TIPOS_COMPRA,
        tipos_venta=list(datos.get("tipos_venta", [])) + TIPOS_VENTA,
    )
