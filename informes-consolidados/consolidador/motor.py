"""Cálculo de posiciones con precio promedio ponderado y operaciones del año."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

TOLERANCIA = 1e-9


@dataclass
class Movimiento:
    fecha: dt.date
    cuenta: str
    tipo: str  # "COMPRA" | "VENTA"
    instrumento: str
    cantidad: float
    precio: float
    monto: float | None = None
    origen: str = ""


@dataclass
class PosicionInicial:
    cuenta: str
    instrumento: str
    cantidad: float
    precio_compra: float


@dataclass
class Precio:
    instrumento: str
    precio: float
    moneda: str
    base: float | None = None  # 100 para bonos que cotizan cada 100 VN
    descripcion: str = ""


@dataclass
class Posicion:
    cuenta: str
    instrumento: str
    descripcion: str
    moneda: str
    base: float
    cantidad: float
    precio_compra: float
    precio_actual: float
    precio_estimado: bool = False  # True si no vino en la planilla de precios

    @property
    def costo(self) -> float:
        return self.cantidad * self.precio_compra / self.base

    @property
    def monto(self) -> float:
        return self.cantidad * self.precio_actual / self.base

    @property
    def rendimiento(self) -> float | None:
        return self.monto / self.costo - 1 if self.costo else None


@dataclass
class PosicionConsolidada(Posicion):
    cuentas: list[str] = field(default_factory=list)
    monto_reporte: float = 0.0
    ponderacion: float = 0.0


@dataclass
class Operacion:
    fecha: dt.date
    cuenta: str
    tipo: str
    instrumento: str
    moneda: str
    base: float
    cantidad: float
    precio_compra: float | None
    precio_venta: float | None
    monto: float
    resultado: float | None


@dataclass
class Resultado:
    posiciones_cuenta: list[Posicion]
    posiciones: list[PosicionConsolidada]
    operaciones: list[Operacion]
    moneda_reporte: str
    alertas: list[str]

    def totales_por_moneda(self) -> dict[str, dict[str, float]]:
        totales: dict[str, dict[str, float]] = defaultdict(
            lambda: {"costo": 0.0, "monto": 0.0, "resultado_realizado": 0.0, "compras": 0.0, "ventas": 0.0}
        )
        for p in self.posiciones:
            totales[p.moneda]["costo"] += p.costo
            totales[p.moneda]["monto"] += p.monto
        for o in self.operaciones:
            t = totales[o.moneda]
            if o.tipo == "VENTA":
                t["ventas"] += o.monto
                t["resultado_realizado"] += o.resultado or 0.0
            else:
                t["compras"] += o.monto
        return dict(sorted(totales.items()))

    @property
    def total_reporte(self) -> float:
        return sum(p.monto_reporte for p in self.posiciones)


def inferir_base(instrumento: str, precios: dict[str, Precio], movimientos: list[Movimiento]) -> float:
    """Base de cotización: la de la planilla de precios o, si no está, se deduce
    de los montos operados (bonos: monto ≈ cantidad × precio / 100)."""
    info = precios.get(instrumento)
    if info and info.base:
        return info.base
    for m in movimientos:
        if m.instrumento == instrumento and m.monto and m.cantidad and m.precio:
            ratio = m.cantidad * m.precio / m.monto
            if 90 <= ratio <= 110:
                return 100.0
            if 0.9 <= ratio <= 1.1:
                return 1.0
    return 1.0


def a_moneda_reporte(monto: float, moneda: str, moneda_reporte: str, tipos_cambio: dict[str, float]) -> float:
    """tipos_cambio = cotización en pesos de cada moneda (p.ej. {"USD": 1450})."""
    if moneda == moneda_reporte:
        return monto
    en_pesos = {"ARS": 1.0, **tipos_cambio}
    if moneda not in en_pesos or moneda_reporte not in en_pesos:
        faltante = moneda if moneda not in en_pesos else moneda_reporte
        raise ValueError(
            f"Hay posiciones en {moneda} y el reporte es en {moneda_reporte}: agregá la cotización "
            f"de {faltante} en 'tipos_cambio' del config.yaml."
        )
    return monto * en_pesos[moneda] / en_pesos[moneda_reporte]


def calcular(
    iniciales: list[PosicionInicial],
    movimientos: list[Movimiento],
    precios: dict[str, Precio],
    fecha_inicio: dt.date,
    fecha_corte: dt.date,
    moneda_reporte: str,
    tipos_cambio: dict[str, float] | None = None,
    alertas: list[str] | None = None,
) -> Resultado:
    alertas = alertas if alertas is not None else []
    tipos_cambio = tipos_cambio or {}

    en_periodo = [m for m in movimientos if fecha_inicio < m.fecha <= fecha_corte]
    fuera = len(movimientos) - len(en_periodo)
    if fuera:
        alertas.append(
            f"{fuera} movimiento(s) con fecha fuera del período {fecha_inicio:%d/%m/%Y}–{fecha_corte:%d/%m/%Y} "
            "se ignoraron (lo anterior ya está en las posiciones iniciales)."
        )
    # Mismo día: primero las compras, para no vender algo que entró ese día.
    en_periodo.sort(key=lambda m: (m.fecha, m.tipo != "COMPRA"))

    # (cuenta, instrumento) -> [cantidad, precio promedio]
    tenencia: dict[tuple[str, str], list[float]] = {}
    for p in iniciales:
        clave = (p.cuenta, p.instrumento)
        if clave in tenencia:  # misma especie repetida en la planilla: se promedia
            q, ppc = tenencia[clave]
            total = q + p.cantidad
            tenencia[clave] = [total, (q * ppc + p.cantidad * p.precio_compra) / total if total else 0.0]
        else:
            tenencia[clave] = [p.cantidad, p.precio_compra]

    bases: dict[str, float] = {}

    def base_de(instrumento: str) -> float:
        if instrumento not in bases:
            bases[instrumento] = inferir_base(instrumento, precios, movimientos)
        return bases[instrumento]

    def moneda_de(instrumento: str) -> str:
        info = precios.get(instrumento)
        return info.moneda if info else moneda_reporte

    ultimo_precio: dict[str, float] = {}
    operaciones: list[Operacion] = []
    for m in en_periodo:
        base = base_de(m.instrumento)
        q, ppc = tenencia.get((m.cuenta, m.instrumento), [0.0, 0.0])
        monto = m.monto if m.monto else m.cantidad * m.precio / base
        ultimo_precio[m.instrumento] = m.precio
        if m.tipo == "COMPRA":
            nueva = q + m.cantidad
            ppc = (q * ppc + m.cantidad * m.precio) / nueva
            tenencia[(m.cuenta, m.instrumento)] = [nueva, ppc]
            operaciones.append(Operacion(m.fecha, m.cuenta, "COMPRA", m.instrumento, moneda_de(m.instrumento),
                                         base, m.cantidad, m.precio, None, monto, None))
        else:
            if m.cantidad > q + TOLERANCIA:
                alertas.append(
                    f"{m.fecha:%d/%m/%Y} cuenta {m.cuenta}: venta de {m.cantidad:,.2f} {m.instrumento} pero la "
                    f"tenencia era {q:,.2f}. Revisar posiciones iniciales o movimientos faltantes."
                )
            vendida_con_costo = min(m.cantidad, q)
            # Lo vendido en exceso no tiene costo conocido: se toma resultado 0 sobre esa parte.
            resultado = vendida_con_costo * (m.precio - ppc) / base
            costo_unitario = ppc if q > TOLERANCIA else m.precio
            restante = max(q - m.cantidad, 0.0)
            tenencia[(m.cuenta, m.instrumento)] = [restante, ppc if restante > TOLERANCIA else 0.0]
            operaciones.append(Operacion(m.fecha, m.cuenta, "VENTA", m.instrumento, moneda_de(m.instrumento),
                                         base, m.cantidad, costo_unitario, m.precio, monto, resultado))

    posiciones_cuenta: list[Posicion] = []
    sin_precio: set[str] = set()
    for (cuenta, instrumento), (q, ppc) in sorted(tenencia.items()):
        if abs(q) <= TOLERANCIA:
            continue
        info = precios.get(instrumento)
        if info:
            precio, estimado = info.precio, False
        else:
            precio, estimado = ultimo_precio.get(instrumento, ppc), True
            sin_precio.add(instrumento)
        posiciones_cuenta.append(Posicion(
            cuenta, instrumento, info.descripcion if info else "", moneda_de(instrumento),
            base_de(instrumento), q, ppc, precio, estimado,
        ))
    for instrumento in sorted(sin_precio):
        alertas.append(
            f"{instrumento}: no está en la planilla de precios; se valuó al último precio operado o al "
            "precio de compra. Completalo y volvé a correr."
        )

    agrupadas: dict[str, list[Posicion]] = defaultdict(list)
    for p in posiciones_cuenta:
        agrupadas[p.instrumento].append(p)
    consolidadas: list[PosicionConsolidada] = []
    for instrumento, grupo in agrupadas.items():
        q = sum(p.cantidad for p in grupo)
        ppc = sum(p.cantidad * p.precio_compra for p in grupo) / q if q else 0.0
        primero = grupo[0]
        consolidadas.append(PosicionConsolidada(
            "", instrumento, primero.descripcion, primero.moneda, primero.base, q, ppc,
            primero.precio_actual, primero.precio_estimado, cuentas=sorted({p.cuenta for p in grupo}),
        ))
    for p in consolidadas:
        p.monto_reporte = a_moneda_reporte(p.monto, p.moneda, moneda_reporte, tipos_cambio)
    total = sum(p.monto_reporte for p in consolidadas)
    for p in consolidadas:
        p.ponderacion = p.monto_reporte / total if total else 0.0
    consolidadas.sort(key=lambda p: (p.moneda != moneda_reporte, p.moneda, -p.monto_reporte))

    return Resultado(posiciones_cuenta, consolidadas, operaciones, moneda_reporte, alertas)
