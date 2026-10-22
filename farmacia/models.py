
import uuid

from django.db import models
from django.core.validators import MinValueValidator


class Medicamento(models.Model):

    articulo = models.OneToOneField(
        "inventario.Articulo",
        on_delete=models.PROTECT,
        related_name="medicamento",
    )

    principio_activo = models.CharField(
        max_length=150
    )

    concentracion = models.CharField(
        max_length=100
    )

    forma_farmaceutica = models.CharField(
        max_length=100
    )

    presentacion = models.CharField(
        max_length=150,
        blank=True
    )

    requiere_receta = models.BooleanField(
        default=True
    )

    unidad_medida = models.CharField(max_length=50, blank=True)

    activo = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        verbose_name = "Medicamento"
        verbose_name_plural = "Medicamentos"
        ordering = ["articulo__nombre"]

    def __str__(self):
        return self.articulo.nombre


class LoteMedicamento(models.Model):

    medicamento = models.ForeignKey(
        Medicamento,
        on_delete=models.PROTECT,
        related_name="lotes",
    )

    numero_lote = models.CharField(
        max_length=100
    )

    fecha_vencimiento = models.DateField()

    activo = models.BooleanField(
        default=True
    )

    class Meta:
        verbose_name = "Lote de medicamento"
        verbose_name_plural = "Lotes de medicamentos"

        constraints = [
            models.UniqueConstraint(
                fields=["medicamento", "numero_lote"],
                name="unique_lote_medicamento",
            )
        ]

    def __str__(self):
        return (
            f"{self.medicamento} - "
            f"Lote {self.numero_lote}"
        )


class StockLote(models.Model):

    lote = models.ForeignKey(
        LoteMedicamento,
        on_delete=models.PROTECT,
        related_name="stocks",
    )

    anexo = models.ForeignKey(
        "organizacion.Anexo",
        on_delete=models.PROTECT,
        related_name="stocks_lotes",
    )

    cantidad = models.PositiveIntegerField(
        default=0
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        verbose_name = "Stock por lote"
        verbose_name_plural = "Stock por lotes"

        constraints = [
            models.UniqueConstraint(
                fields=["lote", "anexo"],
                name="unique_stock_lote_anexo",
            )
        ]

    def __str__(self):
        return (
            f"{self.lote} - "
            f"{self.anexo}: {self.cantidad}"
        )


from django.conf import settings


class MovimientoFarmacia(models.Model):

    TIPOS_MOVIMIENTO = [
        ("ENTRADA", "Entrada"),
        ("SALIDA", "Salida"),
        ("TRANSFERENCIA", "Transferencia"),
        ("ENTREGA", "Entrega a paciente"),
        ("AJUSTE", "Ajuste de inventario"),
    ]

    tipo = models.CharField(
        max_length=20,
        choices=TIPOS_MOVIMIENTO,
    )

    anexo_origen = models.ForeignKey(
        "organizacion.Anexo",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimientos_farmacia_salida",
    )

    anexo_destino = models.ForeignKey(
        "organizacion.Anexo",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimientos_farmacia_entrada",
    )

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="movimientos_farmacia",
    )

    motivo = models.TextField(blank=True)

    proveedor = models.CharField(max_length=150, blank=True)
    documento_recepcion = models.CharField(max_length=100, blank=True)
    fecha_recepcion = models.DateField(null=True, blank=True)
    importacion = models.ForeignKey(
        "farmacia.ImportacionMedicamentos", on_delete=models.PROTECT,
        null=True, blank=True, related_name="movimientos",
    )
    fila_importacion = models.PositiveIntegerField(null=True, blank=True)
    clave_recepcion = models.CharField(max_length=64, null=True, blank=True, unique=True)

    documento_traslado = models.CharField(max_length=100, blank=True)
    clave_traslado = models.UUIDField(null=True, blank=True, unique=True)

    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Movimiento de farmacia"
        verbose_name_plural = "Movimientos de farmacia"
        ordering = ["-fecha"]
        constraints = [models.UniqueConstraint(fields=["importacion", "fila_importacion"], name="unique_farmacia_importacion_fila")]

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.fecha:%d/%m/%Y %H:%M}"


class DetalleMovimientoFarmacia(models.Model):

    movimiento = models.ForeignKey(
        MovimientoFarmacia,
        on_delete=models.PROTECT,
        related_name="detalles",
    )

    lote = models.ForeignKey(
        "farmacia.LoteMedicamento",
        on_delete=models.PROTECT,
        related_name="movimientos_farmacia",
    )

    cantidad = models.PositiveIntegerField(
        validators=[MinValueValidator(1)]
    )

    numero_lote_registrado = models.CharField(max_length=100, blank=True)
    vencimiento_registrado = models.DateField(null=True, blank=True)
    unidad_registrada = models.CharField(max_length=50, blank=True)
    codigo_registrado = models.CharField(max_length=50, blank=True)
    nombre_registrado = models.CharField(max_length=150, blank=True)

    class Meta:
        verbose_name = "Detalle de movimiento de farmacia"
        verbose_name_plural = "Detalles de movimientos de farmacia"

    def __str__(self):
        return f"{self.lote} x {self.cantidad}"






class ImportacionMedicamentos(models.Model):
    """Vista previa y auditoría del catálogo y sus recepciones."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                related_name="importaciones_medicamentos")
    nombre_archivo = models.CharField(max_length=255)
    archivo_sha256 = models.CharField(max_length=64)
    filas = models.JSONField(default=list)
    estado = models.CharField(max_length=20, default="PENDIENTE", choices=[
        ("PENDIENTE", "Pendiente de confirmación"), ("IMPORTADA", "Importada"),
    ])
    modalidad = models.CharField(max_length=20, default="CATALOGO", choices=[
        ("CATALOGO", "Solo catálogo (anterior)"), ("RECEPCION", "Medicamentos, lotes y recepción"),
    ])
    cantidad_movimientos = models.PositiveIntegerField(default=0)
    cantidad_lotes_creados = models.PositiveIntegerField(default=0)
    cantidad_unidades = models.PositiveBigIntegerField(default=0)
    cantidad_importada = models.PositiveIntegerField(default=0)
    creada_at = models.DateTimeField(auto_now_add=True)
    importada_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creada_at"]
        verbose_name = "Importación de medicamentos"
        verbose_name_plural = "Importaciones de medicamentos"

    @property
    def total_filas(self):
        return len(self.filas)

    @property
    def filas_con_error(self):
        return sum(bool(fila["errores"]) for fila in self.filas)

    @property
    def cantidad_omitidas(self):
        return sum(fila.get("resultado") == "OMITIDA" for fila in self.filas)

    @property
    def filas_validas(self):
        return self.total_filas - self.filas_con_error

    @property
    def caducada(self):
        from datetime import timedelta
        from django.utils import timezone
        return self.estado == "PENDIENTE" and timezone.now() > self.creada_at + timedelta(minutes=30)

    @property
    def puede_confirmar(self):
        return self.estado == "PENDIENTE" and bool(self.filas) and not self.filas_con_error and not self.caducada

    def __str__(self):
        return f"{self.nombre_archivo} · {self.get_estado_display()}"


class ConfiguracionFarmacia(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    bodega_central = models.OneToOneField(
        "organizacion.Anexo", on_delete=models.PROTECT, related_name="configuracion_farmacia",
    )

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="farmacia_configuracion_unica")]
        verbose_name = "Configuración de Farmacia"

    def __str__(self):
        return f"Bodega central: {self.bodega_central}"


class StockPendienteFarmacia(models.Model):
    """Saldos retirados del inventario general que requieren identidad/lote verificados."""
    stock_original = models.OneToOneField("inventario.StockAnexo", on_delete=models.PROTECT,
                                         related_name="pendiente_farmacia")
    articulo = models.ForeignKey("inventario.Articulo", on_delete=models.PROTECT,
                                  related_name="saldos_pendientes_farmacia")
    anexo = models.ForeignKey("organizacion.Anexo", on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField()
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    movimiento_regularizacion = models.OneToOneField("inventario.MovimientoInventario", on_delete=models.PROTECT)
    motivo = models.TextField()
    respaldo = models.JSONField(default=dict)
    creado_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Saldo de Farmacia pendiente de conciliación"
        verbose_name_plural = "Saldos de Farmacia pendientes de conciliación"
        ordering = ["articulo__nombre", "pk"]
