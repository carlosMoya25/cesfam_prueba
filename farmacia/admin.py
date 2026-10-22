from django.contrib import admin

from .models import (
    Medicamento,
    LoteMedicamento,
    StockLote,
    MovimientoFarmacia,
    DetalleMovimientoFarmacia,
)


@admin.register(Medicamento)
class MedicamentoAdmin(admin.ModelAdmin):
    list_display = (
        "articulo",
        "principio_activo",
        "concentracion",
        "forma_farmaceutica",
        "unidad_medida",
        "activo",
    )

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.unidad_medida and (
            obj.lotes.filter(stocks__cantidad__gt=0).exists()
            or obj.lotes.filter(movimientos_farmacia__isnull=False).exists()
        ):
            return ("unidad_medida",)
        return ()

    search_fields = (
        "articulo__nombre",
        "principio_activo",
    )

    list_filter = (
        "activo",
        "requiere_receta",
    )


@admin.register(LoteMedicamento)
class LoteMedicamentoAdmin(admin.ModelAdmin):
    list_display = (
        "medicamento",
        "numero_lote",
        "fecha_vencimiento",
        "activo",
    )

    search_fields = (
        "medicamento__articulo__nombre",
        "numero_lote",
    )


@admin.register(StockLote)
class StockLoteAdmin(admin.ModelAdmin):
    list_display = (
        "lote",
        "anexo",
        "cantidad",
    )

    readonly_fields = (
        "lote",
        "anexo",
        "cantidad",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DetalleMovimientoInline(admin.TabularInline):
    model = DetalleMovimientoFarmacia
    extra = 0
    can_delete = False
    readonly_fields = ("lote", "cantidad", "numero_lote_registrado", "vencimiento_registrado", "unidad_registrada", "codigo_registrado", "nombre_registrado")

    def has_add_permission(self, request, obj):
        return False


@admin.register(MovimientoFarmacia)
class MovimientoFarmaciaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tipo",
        "anexo_origen",
        "anexo_destino",
        "usuario",
        "fecha",
    )

    readonly_fields = (
        "tipo",
        "anexo_origen",
        "anexo_destino",
        "usuario",
        "motivo",
        "proveedor", "documento_recepcion", "fecha_recepcion", "importacion", "fila_importacion", "clave_recepcion", "documento_traslado", "clave_traslado",
        "fecha",
    )

    search_fields = ("proveedor", "documento_recepcion", "documento_traslado", "importacion__nombre_archivo", "detalles__lote__numero_lote")

    inlines = [DetalleMovimientoInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

from .models import ImportacionMedicamentos


@admin.register(ImportacionMedicamentos)
class ImportacionMedicamentosAdmin(admin.ModelAdmin):
    list_display = ("nombre_archivo", "usuario", "estado", "cantidad_importada", "cantidad_omitidas", "creada_at", "importada_at")
    list_filter = ("estado", "creada_at")
    search_fields = ("nombre_archivo", "usuario__email")
    readonly_fields = ("id", "usuario", "nombre_archivo", "archivo_sha256", "filas", "estado", "modalidad", "cantidad_importada", "cantidad_movimientos", "cantidad_lotes_creados", "cantidad_unidades", "creada_at", "importada_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from .models import ConfiguracionFarmacia


@admin.register(ConfiguracionFarmacia)
class ConfiguracionFarmaciaAdmin(admin.ModelAdmin):
    list_display = ("bodega_central",)
    readonly_fields = ("bodega_central",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


from .models import StockPendienteFarmacia


@admin.register(StockPendienteFarmacia)
class StockPendienteFarmaciaAdmin(admin.ModelAdmin):
    list_display = ("articulo", "anexo", "cantidad", "creado_at")
    readonly_fields = ("stock_original", "articulo", "anexo", "cantidad", "usuario", "movimiento_regularizacion", "motivo", "respaldo", "creado_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
