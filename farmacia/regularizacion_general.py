"""Retirar artículos vacíos y conservar en conciliación saldos de medicamentos sin lote."""
import hashlib
import json
import uuid

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from inventario.models import Articulo, StockAnexo, DetalleMovimiento, MovimientoInventario, TipoMovimiento
from usuarios.models import HistorialSeguridad
from usuarios.utils import es_administrador
from .models import StockPendienteFarmacia, StockLote


def plan_regularizacion_general():
    huerfanos = Articulo.objects.filter(categoria__nombre__iexact="Medicamentos", medicamento__isnull=True,
                                        saldos_pendientes_farmacia__isnull=True).order_by("pk")
    articulos = []
    for articulo in huerfanos:
        articulos.append({"datos": dict(Articulo.objects.filter(pk=articulo.pk).values().get()),
            "stocks": list(articulo.stocks.order_by("pk").values()),
            "movimientos": articulo.detalles_movimientos.count(), "recursos": articulo.usos_requerimientos.count()})
    saldos = list(StockAnexo.objects.filter(
        Q(articulo__medicamento__isnull=False) | Q(articulo_id__in=huerfanos.values("pk")),
        cantidad__gt=0, pendiente_farmacia__isnull=True,
    ).order_by("pk").values())
    ids = {a["datos"]["id"] for a in articulos} | {s["articulo_id"] for s in saldos}
    detalles = list(DetalleMovimiento.objects.filter(articulo_id__in=ids).order_by("pk").values())
    movimientos = list(MovimientoInventario.objects.filter(pk__in=[d["movimiento_id"] for d in detalles]).order_by("pk").values())
    plan = {"articulos": articulos, "saldos": saldos, "detalles_originales": detalles, "movimientos_originales": movimientos}
    plan = json.loads(json.dumps(plan, cls=DjangoJSONEncoder))
    plan["huella"] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return plan


@transaction.atomic
def regularizar_general(*, usuario, huella_esperada, respaldar):
    if not usuario.is_authenticated or not usuario.is_active or not es_administrador(usuario):
        raise ValidationError("Solo un administrador activo puede regularizar estos registros.")
    plan = plan_regularizacion_general()
    ids = {a["datos"]["id"] for a in plan["articulos"]} | {s["articulo_id"] for s in plan["saldos"]}
    list(Articulo.objects.select_for_update().filter(pk__in=ids).order_by("pk").values_list("pk", flat=True))
    list(StockAnexo.objects.select_for_update().filter(articulo_id__in=ids).order_by("pk").values_list("pk", flat=True))
    plan = plan_regularizacion_general()
    if plan["huella"] != huella_esperada:
        raise ValidationError("Los registros cambiaron; revise un plan nuevo antes de regularizarlos.")
    eliminables = [a for a in plan["articulos"] if not a["stocks"] and not a["movimientos"] and not a["recursos"]]
    con_saldo = {s["articulo_id"] for s in plan["saldos"]}
    if any(a not in eliminables and a["datos"]["id"] not in con_saldo for a in plan["articulos"]):
        raise ValidationError("Hay artículos con dependencias que requieren otra revisión. No se modificó ningún dato.")
    resultado = {"operacion": str(uuid.uuid4()), "eliminados": [], "pendientes": []}
    if not plan["articulos"] and not plan["saldos"]:
        return resultado
    farmacia_antes = list(StockLote.objects.order_by("pk").values_list("pk", "lote_id", "anexo_id", "cantidad"))
    respaldar({"fecha": timezone.now().isoformat(), "administrador_id": usuario.pk, "plan": plan,
               "operacion": resultado["operacion"], "saldos_farmacia_previos": farmacia_antes})
    tipo, _ = TipoMovimiento.objects.get_or_create(nombre="Regularización a conciliación de Farmacia", defaults={"activo": True})
    for saldo in plan["saldos"]:
        articulo = Articulo.objects.get(pk=saldo["articulo_id"])
        if hasattr(articulo, "medicamento"):
            motivo = "Saldo antiguo sin lote del inventario general. Conciliar contra las recepciones y existencias de Farmacia antes de decidir si es adicional o ya contabilizado. Verificar unidad, lote y vencimiento."
        else:
            motivo = "Artículo clasificado como medicamento sin ficha de Farmacia. Verificar identidad y correspondencia del código con los documentos originales; comprobar unidad, lote y vencimiento antes de incorporarlo a existencias disponibles."
        movimiento = MovimientoInventario.objects.create(tipo_movimiento=tipo, anexo_origen_id=saldo["anexo_id"],
            usuario=usuario, motivo=f"{motivo} Operación {resultado['operacion']}. Retiro administrativo a conciliación; no es consumo ni traslado físico.")
        DetalleMovimiento.objects.create(movimiento=movimiento, articulo=articulo, cantidad=saldo["cantidad"])
        pendiente = StockPendienteFarmacia.objects.create(stock_original_id=saldo["id"], articulo=articulo,
            anexo_id=saldo["anexo_id"], cantidad=saldo["cantidad"], usuario=usuario,
            movimiento_regularizacion=movimiento, motivo=motivo, respaldo={
                "stock_original": saldo, "articulo_original": json.loads(json.dumps(dict(Articulo.objects.filter(pk=articulo.pk).values().get()), cls=DjangoJSONEncoder)),
                "operacion": resultado["operacion"],
            })
        StockAnexo.objects.filter(pk=saldo["id"]).update(cantidad=0, updated_at=timezone.now())
        resultado["pendientes"].append({"id": pendiente.pk, "codigo": articulo.codigo, "nombre": articulo.nombre, "cantidad": saldo["cantidad"]})
    for articulo in eliminables:
        registro = Articulo.objects.get(pk=articulo["datos"]["id"])
        if registro.stocks.exists() or registro.detalles_movimientos.exists() or registro.usos_requerimientos.exists():
            raise ValidationError("Un artículo adquirió dependencias; se revirtió la regularización.")
        resultado["eliminados"].append({"id": registro.pk, "codigo": registro.codigo, "nombre": registro.nombre})
        registro.delete()
    if farmacia_antes != list(StockLote.objects.order_by("pk").values_list("pk", "lote_id", "anexo_id", "cantidad")):
        raise ValidationError("Las existencias por lote cambiaron; se revirtió la regularización.")
    HistorialSeguridad.objects.create(tipo_accion="OTRO", realizado_por=usuario, usuario_afectado=None, email_afectado=usuario.email,
        descripcion=f"Regularización de medicamentos en inventario general. Operación {resultado['operacion']}: {len(resultado['eliminados'])} artículos vacíos retirados y {len(resultado['pendientes'])} saldos conservados en conciliación. No se modificaron existencias por lote de Farmacia.")
    return resultado
