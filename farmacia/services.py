
from django.utils import timezone

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from organizacion.models import Anexo
from usuarios.utils import puede_gestionar_farmacia
from .trazabilidad import clave_de_recepcion as generar_clave_recepcion
from .bodega import exigir_bodega_central

from .models import (
    Medicamento,
    LoteMedicamento,
    StockLote,
    MovimientoFarmacia,
    DetalleMovimientoFarmacia,
)


@transaction.atomic
def registrar_entrada_medicamento(
    *,
    medicamento,
    anexo,
    numero_lote,
    fecha_vencimiento,
    cantidad,
    usuario,
    motivo="",
    proveedor="",
    documento_recepcion="",
    fecha_recepcion=None,
    importacion=None,
    fila_importacion=None,
):
    if not usuario.is_authenticated:
        raise ValidationError(
            "El usuario debe iniciar sesión."
        )

    if not puede_gestionar_farmacia(usuario):
        raise ValidationError("No tienes permisos para gestionar Farmacia.")

    if not isinstance(cantidad, int) or isinstance(cantidad, bool) or cantidad <= 0:
        raise ValidationError(
            "La cantidad debe ser un entero positivo."
        )

    if fecha_vencimiento < timezone.localdate():
        raise ValidationError(
            "No se pueden ingresar medicamentos vencidos."
        )

    if not isinstance(numero_lote, str) or not numero_lote.strip():
        raise ValidationError(
            "Debe ingresar un número de lote."
        )

    numero_lote = numero_lote.strip()

    try:
        medicamento = Medicamento.objects.select_for_update().select_related("articulo").get(
            pk=medicamento.pk, activo=True, articulo__activo=True,
        )
    except Medicamento.DoesNotExist:
        raise ValidationError("El medicamento no está activo.")

    if not Anexo.objects.filter(
        pk=anexo.pk,
        activo=True,
    ).exists():
        raise ValidationError(
            "La ubicación no está activa."
        )

    if anexo.pk != exigir_bodega_central().pk:
        raise ValidationError("Las recepciones deben ingresar a Bodega Central. Para enviar unidades a un anexo, registre un traslado.")

    if any((proveedor, documento_recepcion, fecha_recepcion)):
        if not (proveedor.strip() and documento_recepcion.strip() and fecha_recepcion):
            raise ValidationError("La recepción requiere proveedor, documento y fecha completos.")
        if fecha_recepcion > timezone.localdate():
            raise ValidationError("La recepción no puede tener una fecha futura.")
        if len(proveedor) > 150 or len(documento_recepcion) > 100:
            raise ValidationError("El proveedor o documento de recepción supera el largo permitido.")

    # Buscar o registrar el lote.
    lote, creado = LoteMedicamento.objects.select_for_update().get_or_create(
        medicamento=medicamento,
        numero_lote=numero_lote,
        defaults={
            "fecha_vencimiento": fecha_vencimiento,
            "activo": True,
        },
    )

    if not lote.activo:
        raise ValidationError(
            "El lote se encuentra inactivo."
        )

    if lote.fecha_vencimiento != fecha_vencimiento:
        raise ValidationError(
            "El lote ya existe con otra fecha de vencimiento."
        )

    clave_recepcion = None
    if proveedor and documento_recepcion and fecha_recepcion:
        clave_recepcion = generar_clave_recepcion(
            proveedor=proveedor, documento=documento_recepcion,
            medicamento_id=medicamento.pk, lote_id=lote.pk, ubicacion_id=anexo.pk,
        )
        if MovimientoFarmacia.objects.filter(clave_recepcion=clave_recepcion).exists():
            raise ValidationError("Esta recepción ya fue registrada para el proveedor, documento, medicamento, lote y ubicación indicados.")

    # Crear el registro de stock si todavía no existe.
    stock, _ = StockLote.objects.select_for_update().get_or_create(
        lote=lote,
        anexo=anexo,
        defaults={"cantidad": 0},
    )

    if stock.cantidad + cantidad > 2147483647:
        raise ValidationError("La recepción supera el máximo de unidades permitido para este lote y ubicación.")

    # Incremento atómico para evitar perder actualizaciones.
    StockLote.objects.filter(pk=stock.pk).update(
        cantidad=F("cantidad") + cantidad
    )

    # Registrar movimiento de farmacia.
    movimiento = MovimientoFarmacia.objects.create(
        tipo="ENTRADA",
        anexo_origen=None,
        anexo_destino=anexo,
        usuario=usuario,
        motivo=motivo or "Ingreso de medicamentos",
        proveedor=proveedor,
        documento_recepcion=documento_recepcion,
        fecha_recepcion=fecha_recepcion,
        importacion=importacion,
        fila_importacion=fila_importacion,
        clave_recepcion=clave_recepcion,
    )

    DetalleMovimientoFarmacia.objects.create(
        movimiento=movimiento,
        lote=lote,
        cantidad=cantidad,
        numero_lote_registrado=lote.numero_lote,
        vencimiento_registrado=lote.fecha_vencimiento,
        unidad_registrada=medicamento.unidad_medida,
        codigo_registrado=medicamento.articulo.codigo or "",
        nombre_registrado=medicamento.articulo.nombre,
    )

    return movimiento


@transaction.atomic
def registrar_entrada_lote(*, medicamento, lote, anexo, cantidad, usuario, motivo="",
                           proveedor="", documento_recepcion="", fecha_recepcion=None):
    """Recibir unidades de un lote existente sin cambiar sus datos."""
    if not puede_gestionar_farmacia(usuario):
        raise ValidationError("No tienes permisos para gestionar Farmacia.")
    try:
        lote = LoteMedicamento.objects.get(
            pk=lote.pk, medicamento=medicamento,
        )
    except LoteMedicamento.DoesNotExist:
        raise ValidationError("El lote seleccionado no corresponde al medicamento.")
    return registrar_entrada_medicamento(
        medicamento=medicamento, anexo=anexo,
        numero_lote=lote.numero_lote, fecha_vencimiento=lote.fecha_vencimiento,
        cantidad=cantidad, usuario=usuario, motivo=motivo,
        proveedor=proveedor, documento_recepcion=documento_recepcion, fecha_recepcion=fecha_recepcion,
    )


@transaction.atomic
def registrar_traslado_bodega(*, lote, destino, cantidad, usuario, documento_traslado,
                              solicitud_id, motivo=""):
    """Descontar y acreditar el mismo lote una sola vez, conservando su trazabilidad."""
    import uuid
    if not puede_gestionar_farmacia(usuario):
        raise ValidationError("No tienes permisos para gestionar Farmacia.")
    if not isinstance(cantidad, int) or isinstance(cantidad, bool) or not 0 < cantidad <= 2147483647:
        raise ValidationError("La cantidad debe ser un entero positivo dentro del máximo permitido.")
    documento_traslado = str(documento_traslado).strip()
    if not documento_traslado or len(documento_traslado) > 100:
        raise ValidationError("Indique un documento o referencia de traslado de hasta 100 caracteres.")
    try:
        solicitud_id = uuid.UUID(str(solicitud_id))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("La solicitud de traslado no es válida. Actualice el formulario.")
    motivo = str(motivo).strip()

    def traslado_previo():
        previo = MovimientoFarmacia.objects.filter(clave_traslado=solicitud_id).first()
        if previo:
            detalle = previo.detalles.first()
            if (previo.usuario_id != usuario.pk or previo.anexo_destino_id != destino.pk
                    or previo.documento_traslado != documento_traslado or previo.motivo != motivo
                    or not detalle or detalle.lote_id != lote.pk or detalle.cantidad != cantidad):
                raise ValidationError("Esta solicitud ya se utilizó con otros datos. Revise el historial y abra un formulario nuevo.")
        return previo

    previo = traslado_previo()
    if previo:
        return previo
    # Mantener el orden de bloqueo de las recepciones: medicamento, lote, existencias.
    medicamento = Medicamento.objects.select_for_update().select_related("articulo").get(pk=lote.medicamento_id)
    previo = traslado_previo()
    if previo:
        return previo
    if not medicamento.activo or not medicamento.articulo.activo or not medicamento.unidad_medida:
        raise ValidationError("El medicamento debe estar activo y tener una unidad de stock definida.")
    lote = LoteMedicamento.objects.select_for_update().get(pk=lote.pk, medicamento=medicamento)
    if not lote.activo or lote.fecha_vencimiento < timezone.localdate():
        raise ValidationError("No se pueden trasladar lotes inactivos o vencidos.")
    origen = exigir_bodega_central()
    if origen.pk == destino.pk:
        raise ValidationError("El destino debe ser distinto de Bodega Central.")
    if not Anexo.objects.filter(pk=destino.pk, activo=True).exists():
        raise ValidationError("La ubicación de destino no está activa.")
    stock_origen = StockLote.objects.select_for_update().filter(lote=lote, anexo=origen).first()
    if not stock_origen or stock_origen.cantidad < cantidad:
        raise ValidationError("Stock insuficiente en Bodega Central. Revise la cantidad disponible antes de trasladar.")
    stock_destino, _ = StockLote.objects.select_for_update().get_or_create(
        lote=lote, anexo=destino, defaults={"cantidad": 0},
    )
    if stock_destino.cantidad + cantidad > 2147483647:
        raise ValidationError("El traslado supera el máximo de unidades permitido en el destino.")
    StockLote.objects.filter(pk=stock_origen.pk).update(cantidad=F("cantidad") - cantidad)
    StockLote.objects.filter(pk=stock_destino.pk).update(cantidad=F("cantidad") + cantidad)
    movimiento = MovimientoFarmacia.objects.create(
        tipo="TRANSFERENCIA", anexo_origen=origen, anexo_destino=destino,
        usuario=usuario, motivo=motivo, documento_traslado=documento_traslado,
        clave_traslado=solicitud_id,
    )
    DetalleMovimientoFarmacia.objects.create(
        movimiento=movimiento, lote=lote, cantidad=cantidad,
        numero_lote_registrado=lote.numero_lote, vencimiento_registrado=lote.fecha_vencimiento,
        unidad_registrada=medicamento.unidad_medida, codigo_registrado=medicamento.articulo.codigo or "",
        nombre_registrado=medicamento.articulo.nombre,
    )
    return movimiento
