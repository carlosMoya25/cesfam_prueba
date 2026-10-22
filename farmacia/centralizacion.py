"""Mantenimiento administrativo para centralizar existencias ya recepcionadas."""
import hashlib
import json
import uuid
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from usuarios.utils import es_administrador
from .bodega import exigir_bodega_central
from .models import Medicamento, LoteMedicamento, StockLote, MovimientoFarmacia, DetalleMovimientoFarmacia


def plan_centralizacion():
    central = exigir_bodega_central()
    filas = list(StockLote.objects.order_by("pk").values(
        "pk", "lote_id", "anexo_id", "cantidad", "anexo__nombre", "anexo__activo",
        "lote__numero_lote", "lote__fecha_vencimiento", "lote__activo",
        "lote__medicamento_id", "lote__medicamento__activo", "lote__medicamento__unidad_medida",
        "lote__medicamento__articulo__codigo", "lote__medicamento__articulo__nombre",
        "lote__medicamento__articulo__activo",
    ))
    for fila in filas:
        fila["lote__fecha_vencimiento"] = fila["lote__fecha_vencimiento"].isoformat()
    plan = {"bodega_id": central.pk, "bodega_nombre": central.nombre, "existencias": filas}
    plan["huella"] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return plan


def filas_por_centralizar(plan):
    return [fila for fila in plan["existencias"] if fila["anexo_id"] != plan["bodega_id"] and fila["cantidad"] > 0]


@transaction.atomic
def centralizar_existencias(*, usuario, huella_esperada, respaldar):
    if not usuario.is_authenticated or not usuario.is_active or not es_administrador(usuario):
        raise ValidationError("Solo un administrador activo puede centralizar existencias mediante mantenimiento.")
    # Mismo orden de bloqueo que las operaciones habituales de Farmacia.
    list(Medicamento.objects.select_for_update().order_by("pk").values_list("pk", flat=True))
    list(LoteMedicamento.objects.select_for_update().order_by("pk").values_list("pk", flat=True))
    list(StockLote.objects.select_for_update().order_by("lote_id", "anexo_id", "pk").values_list("pk", flat=True))
    plan = plan_centralizacion()
    if plan["huella"] != huella_esperada:
        raise ValidationError("Los saldos o sus datos cambiaron. Genere y revise otro plan antes de aplicar la centralización.")
    origenes = filas_por_centralizar(plan)
    totales_antes = defaultdict(int)
    for fila in plan["existencias"]:
        totales_antes[fila["lote_id"]] += fila["cantidad"]
    for fila in origenes:
        if (not fila["anexo__activo"] or not fila["lote__activo"]
                or not fila["lote__medicamento__activo"] or not fila["lote__medicamento__articulo__activo"]
                or fila["lote__fecha_vencimiento"] < timezone.localdate().isoformat()):
            raise ValidationError(f"El stock #{fila['pk']} requiere revisión por estado o vencimiento. No se centralizó ningún registro.")
        if totales_antes[fila["lote_id"]] > 2147483647:
            raise ValidationError("El saldo consolidado de un lote supera el máximo permitido. No se centralizó ningún registro.")
    operacion = uuid.uuid4()
    referencia = f"CENTRALIZACION-{timezone.localdate():%Y%m%d}-{str(operacion)[:8]}"
    resultado = {"operacion": str(operacion), "documento": referencia, "bodega_id": plan["bodega_id"],
                 "registros_trasladados": 0, "medicamentos": 0, "cantidad_registrada": 0,
                 "movimientos": [], "revisar_unidad": []}
    if not origenes:
        return resultado
    # Si no es posible guardar el respaldo, se aborta antes de cambiar saldos.
    respaldar({"operacion": str(operacion), "documento": referencia,
               "fecha": timezone.now().isoformat(), "administrador_id": usuario.pk,
               "estado": "SALDOS_PREVIOS", "plan": plan})
    for fila in origenes:
        lote = LoteMedicamento.objects.get(pk=fila["lote_id"])
        destino, _ = StockLote.objects.get_or_create(lote=lote, anexo_id=plan["bodega_id"], defaults={"cantidad": 0})
        cantidad = fila["cantidad"]
        StockLote.objects.filter(pk=fila["pk"]).update(cantidad=0, updated_at=timezone.now())
        StockLote.objects.filter(pk=destino.pk).update(cantidad=destino.cantidad + cantidad, updated_at=timezone.now())
        unidad = fila["lote__medicamento__unidad_medida"]
        motivo = "Centralización administrativa de existencias ya recepcionadas, solicitada por el administrador. Se conserva el lote, su vencimiento y el respaldo de recepción original."
        if not unidad:
            motivo += " Unidad de medida no definida en la ficha original: pendiente de verificar; no se realizó conversión."
            resultado["revisar_unidad"].append({
                "medicamento_id": fila["lote__medicamento_id"],
                "codigo": fila["lote__medicamento__articulo__codigo"],
                "nombre": fila["lote__medicamento__articulo__nombre"],
                "lote": fila["lote__numero_lote"], "cantidad": cantidad,
            })
        movimiento = MovimientoFarmacia.objects.create(
            tipo="TRANSFERENCIA", anexo_origen_id=fila["anexo_id"], anexo_destino_id=plan["bodega_id"],
            usuario=usuario, motivo=motivo, documento_traslado=referencia,
            clave_traslado=uuid.uuid5(operacion, str(fila["pk"])),
        )
        DetalleMovimientoFarmacia.objects.create(
            movimiento=movimiento, lote=lote, cantidad=cantidad,
            numero_lote_registrado=lote.numero_lote, vencimiento_registrado=lote.fecha_vencimiento,
            unidad_registrada=unidad, codigo_registrado=fila["lote__medicamento__articulo__codigo"] or "",
            nombre_registrado=fila["lote__medicamento__articulo__nombre"],
        )
        resultado["movimientos"].append(movimiento.pk)
        resultado["cantidad_registrada"] += cantidad
    totales_despues = defaultdict(int)
    for lote_id, cantidad in StockLote.objects.values_list("lote_id", "cantidad"):
        totales_despues[lote_id] += cantidad
    if dict(totales_antes) != dict(totales_despues):
        raise ValidationError("La verificación por lote no coincide. Se revirtió toda la centralización.")
    if StockLote.objects.exclude(anexo_id=plan["bodega_id"]).filter(cantidad__gt=0).exists():
        raise ValidationError("Aún hay existencias externas. Se revirtió toda la centralización.")
    resultado["registros_trasladados"] = len(origenes)
    resultado["medicamentos"] = len({fila["lote__medicamento_id"] for fila in origenes})
    return resultado
