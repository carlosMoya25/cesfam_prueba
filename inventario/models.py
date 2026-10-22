from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class CategoriaArticulo(models.Model):
    nombre = models.CharField(max_length=150, unique=True)
    descripcion = models.CharField(max_length=255, blank=True)
    activo = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Categoría de artículo"
        verbose_name_plural = "Categorías de artículos"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Articulo(models.Model):
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True)

    categoria = models.ForeignKey(
        CategoriaArticulo,
        on_delete=models.PROTECT,
        related_name="articulos",
    )

    codigo = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
    )

    stock_minimo = models.PositiveIntegerField(default=0)
    activo = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Artículo"
        verbose_name_plural = "Artículos"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class StockAnexo(models.Model):
    articulo = models.ForeignKey(
        Articulo,
        on_delete=models.PROTECT,
        related_name="stocks",
    )

    anexo = models.ForeignKey(
        "organizacion.Anexo",
        on_delete=models.PROTECT,
        related_name="stocks_articulos",
    )

    cantidad = models.PositiveIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Stock por anexo"
        verbose_name_plural = "Stock por anexos"

        constraints = [
            models.UniqueConstraint(
                fields=["articulo", "anexo"],
                name="unique_stock_articulo_anexo",
            )
        ]

    @property
    def stock_bajo(self):
        return 0 < self.cantidad <= self.articulo.stock_minimo

    def __str__(self):
        return f"{self.articulo} - {self.anexo}: {self.cantidad}"


class TipoMovimiento(models.Model):
    nombre = models.CharField(max_length=50, unique=True)
    descripcion = models.CharField(max_length=255, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Tipo de movimiento"
        verbose_name_plural = "Tipos de movimiento"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class MovimientoInventario(models.Model):
    tipo_movimiento = models.ForeignKey(
        TipoMovimiento,
        on_delete=models.PROTECT,
        related_name="movimientos",
    )

    anexo_origen = models.ForeignKey(
        "organizacion.Anexo",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimientos_salida",
    )

    anexo_destino = models.ForeignKey(
        "organizacion.Anexo",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimientos_entrada",
    )

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="movimientos_inventario",
    )

    motivo = models.TextField(blank=True)

    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Movimiento de inventario"
        verbose_name_plural = "Movimientos de inventario"
        ordering = ["-fecha"]

    def __str__(self):
        return f"{self.tipo_movimiento} - {self.fecha:%d/%m/%Y %H:%M}"


class DetalleMovimiento(models.Model):
    movimiento = models.ForeignKey(
        MovimientoInventario,
        on_delete=models.CASCADE,
        related_name="detalles",
    )

    articulo = models.ForeignKey(
        Articulo,
        on_delete=models.PROTECT,
        related_name="detalles_movimientos",
    )

    cantidad = models.PositiveIntegerField(
        validators=[MinValueValidator(1)]
    )

    class Meta:
        verbose_name = "Detalle de movimiento"
        verbose_name_plural = "Detalles de movimientos"

    def __str__(self):
        return f"{self.articulo} x {self.cantidad}"


class RecursoRequerimiento(models.Model):
    requerimiento = models.ForeignKey(
        "requerimientos.Requerimiento",
        on_delete=models.CASCADE,
        related_name="recursos_utilizados",
    )

    articulo = models.ForeignKey(
        Articulo,
        on_delete=models.PROTECT,
        related_name="usos_requerimientos",
    )

    cantidad = models.PositiveIntegerField(
        validators=[MinValueValidator(1)]
    )

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recursos_registrados",
    )

    movimiento = models.ForeignKey(
        MovimientoInventario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recursos_requerimientos",
    )

    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Recurso utilizado"
        verbose_name_plural = "Recursos utilizados"
        ordering = ["-fecha"]

    def __str__(self):
        return f"{self.articulo} x {self.cantidad}"