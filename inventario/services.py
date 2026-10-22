from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from .models import (
    Articulo,
    DetalleMovimiento,
    MovimientoInventario,
    StockAnexo,
    TipoMovimiento,
    RecursoRequerimiento
)


def validar_articulo_general(articulo):
    # Serializar con las regularizaciones para no reingresar saldos pendientes.
    Articulo.objects.select_for_update().get(pk=articulo.pk)
    if (Articulo.objects.filter(pk=articulo.pk, medicamento__isnull=False).exists()
            or Articulo.objects.filter(pk=articulo.pk, saldos_pendientes_farmacia__isnull=False).exists()):
        raise ValidationError(
            "Los medicamentos deben gestionarse desde Farmacia, indicando su lote."
        )


def obtener_tipo_movimiento(nombre):
    try:
        return TipoMovimiento.objects.get(
            nombre=nombre,
            activo=True,
        )
    except TipoMovimiento.DoesNotExist:
        raise ValidationError(
            f"El tipo de movimiento '{nombre}' no existe o está inactivo."
        )


@transaction.atomic
def registrar_entrada(*, usuario, anexo, articulo, cantidad, motivo=""):
    validar_articulo_general(articulo)

    if cantidad <= 0:
        raise ValidationError("La cantidad debe ser mayor a cero.")

    articulo = Articulo.objects.get(pk=articulo.pk)

    movimiento = MovimientoInventario.objects.create(
        tipo_movimiento=obtener_tipo_movimiento("Entrada"),
        anexo_destino=anexo,
        usuario=usuario,
        motivo=motivo,
    )

    DetalleMovimiento.objects.create(
        movimiento=movimiento,
        articulo=articulo,
        cantidad=cantidad,
    )

    stock, _ = StockAnexo.objects.select_for_update().get_or_create(
        articulo=articulo,
        anexo=anexo,
        defaults={"cantidad": 0},
    )

    StockAnexo.objects.filter(pk=stock.pk).update(
        cantidad=F("cantidad") + cantidad
    )

    stock.refresh_from_db()

    return movimiento


@transaction.atomic
def registrar_salida(*, usuario, anexo, articulo, cantidad, motivo=""):
    validar_articulo_general(articulo)

    if cantidad <= 0:
        raise ValidationError("La cantidad debe ser mayor a cero.")

    try:
        stock = StockAnexo.objects.select_for_update().get(
            articulo=articulo,
            anexo=anexo,
        )
    except StockAnexo.DoesNotExist:
        raise ValidationError(
            "No existe stock de este artículo en el anexo seleccionado."
        )

    if stock.cantidad < cantidad:
        raise ValidationError(
            f"Stock insuficiente. Disponible: {stock.cantidad}."
        )

    movimiento = MovimientoInventario.objects.create(
        tipo_movimiento=obtener_tipo_movimiento("Salida"),
        anexo_origen=anexo,
        usuario=usuario,
        motivo=motivo,
    )

    DetalleMovimiento.objects.create(
        movimiento=movimiento,
        articulo=articulo,
        cantidad=cantidad,
    )

    StockAnexo.objects.filter(pk=stock.pk).update(
        cantidad=F("cantidad") - cantidad
    )

    stock.refresh_from_db()

    return movimiento


@transaction.atomic
def registrar_transferencia(
    *,
    usuario,
    anexo_origen,
    anexo_destino,
    articulo,
    cantidad,
    motivo="",
):
    validar_articulo_general(articulo)

    if cantidad <= 0:
        raise ValidationError("La cantidad debe ser mayor a cero.")

    if anexo_origen.pk == anexo_destino.pk:
        raise ValidationError(
            "El anexo de origen y destino deben ser diferentes."
        )

    try:
        stock_origen = StockAnexo.objects.select_for_update().get(
            articulo=articulo,
            anexo=anexo_origen,
        )
    except StockAnexo.DoesNotExist:
        raise ValidationError(
            "No existe stock del artículo en el anexo de origen."
        )

    if stock_origen.cantidad < cantidad:
        raise ValidationError(
            f"Stock insuficiente. Disponible: {stock_origen.cantidad}."
        )

    stock_destino, _ = StockAnexo.objects.select_for_update().get_or_create(
        articulo=articulo,
        anexo=anexo_destino,
        defaults={"cantidad": 0},
    )

    movimiento = MovimientoInventario.objects.create(
        tipo_movimiento=obtener_tipo_movimiento("Transferencia"),
        anexo_origen=anexo_origen,
        anexo_destino=anexo_destino,
        usuario=usuario,
        motivo=motivo,
    )

    DetalleMovimiento.objects.create(
        movimiento=movimiento,
        articulo=articulo,
        cantidad=cantidad,
    )

    StockAnexo.objects.filter(pk=stock_origen.pk).update(
        cantidad=F("cantidad") - cantidad
    )

    StockAnexo.objects.filter(pk=stock_destino.pk).update(
        cantidad=F("cantidad") + cantidad
    )

    return movimiento

@transaction.atomic
def registrar_ajuste(
    *,
    usuario,
    anexo,
    articulo,
    cantidad,
    tipo_ajuste,
    motivo,
):
    validar_articulo_general(articulo)

    if cantidad <= 0:
        raise ValidationError(
            "La cantidad debe ser mayor a cero."
        )

    if tipo_ajuste not in ["positivo", "negativo"]:
        raise ValidationError(
            "El tipo de ajuste no es válido."
        )

    if not motivo.strip():
        raise ValidationError(
            "Debe indicar el motivo del ajuste."
        )

    stock, _ = StockAnexo.objects.select_for_update().get_or_create(
        articulo=articulo,
        anexo=anexo,
        defaults={"cantidad": 0},
    )

    if tipo_ajuste == "negativo" and stock.cantidad < cantidad:
        raise ValidationError(
            f"Stock insuficiente. Disponible: {stock.cantidad}."
        )

    nombre_tipo = (
        "Ajuste positivo"
        if tipo_ajuste == "positivo"
        else "Ajuste negativo"
    )

    movimiento = MovimientoInventario.objects.create(
        tipo_movimiento=obtener_tipo_movimiento(nombre_tipo),
        anexo_destino=anexo if tipo_ajuste == "positivo" else None,
        anexo_origen=anexo if tipo_ajuste == "negativo" else None,
        usuario=usuario,
        motivo=motivo,
    )

    DetalleMovimiento.objects.create(
        movimiento=movimiento,
        articulo=articulo,
        cantidad=cantidad,
    )

    if tipo_ajuste == "positivo":
        StockAnexo.objects.filter(pk=stock.pk).update(
            cantidad=F("cantidad") + cantidad
        )
    else:
        StockAnexo.objects.filter(pk=stock.pk).update(
            cantidad=F("cantidad") - cantidad
        )

    stock.refresh_from_db()

    return movimiento

@transaction.atomic
def registrar_recurso_requerimiento(
    *,
    requerimiento,
    articulo,
    cantidad,
    usuario,
):
    validar_articulo_general(articulo)

    if cantidad <= 0:
        raise ValidationError(
            "La cantidad debe ser mayor a cero."
        )

    if not requerimiento.anexo:
        raise ValidationError(
            "El requerimiento no tiene un anexo asociado."
        )

    if requerimiento.responsable_id != usuario.id:
        raise ValidationError(
            "Solo el técnico responsable puede utilizar recursos."
        )

    estados_permitidos = [
        "Asignado",
        "En proceso",
    ]

    if requerimiento.estado.nombre not in estados_permitidos:
        raise ValidationError(
            "No se pueden agregar recursos en el estado actual."
        )

    try:
        stock = StockAnexo.objects.select_for_update().get(
            articulo=articulo,
            anexo=requerimiento.anexo,
        )
    except StockAnexo.DoesNotExist:
        raise ValidationError(
            "No existe stock de este artículo en el anexo "
            "del requerimiento."
        )

    if stock.cantidad < cantidad:
        raise ValidationError(
            f"Stock insuficiente. Disponible: {stock.cantidad}."
        )

    movimiento = MovimientoInventario.objects.create(
        tipo_movimiento=obtener_tipo_movimiento("Salida"),
        anexo_origen=requerimiento.anexo,
        usuario=usuario,
        motivo=(
            f"Recurso utilizado en requerimiento "
            f"#{requerimiento.id}"
        ),
    )

    DetalleMovimiento.objects.create(
        movimiento=movimiento,
        articulo=articulo,
        cantidad=cantidad,
    )

    recurso = RecursoRequerimiento.objects.create(
        requerimiento=requerimiento,
        articulo=articulo,
        cantidad=cantidad,
        usuario=usuario,
        movimiento=movimiento,
    )

    StockAnexo.objects.filter(pk=stock.pk).update(
        cantidad=F("cantidad") - cantidad
    )

    return recurso