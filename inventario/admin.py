from django.contrib import admin
from .forms import ArticuloForm

from .models import (
    CategoriaArticulo,
    Articulo,
    StockAnexo,
    TipoMovimiento,
    MovimientoInventario,
    DetalleMovimiento,
    RecursoRequerimiento,
)


@admin.register(CategoriaArticulo)
class CategoriaArticuloAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activo")
    search_fields = ("nombre",)
    list_filter = ("activo",)


@admin.register(Articulo)
class ArticuloAdmin(admin.ModelAdmin):
    form = ArticuloForm

    def get_queryset(self, request):
        return super().get_queryset(request).filter(medicamento__isnull=True, saldos_pendientes_farmacia__isnull=True)

    list_display = (
        "nombre",
        "codigo",
        "categoria",
        "stock_minimo",
        "activo",
    )
    search_fields = ("nombre", "codigo")
    list_filter = ("categoria", "activo")


@admin.register(StockAnexo)
class StockAnexoAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).filter(articulo__medicamento__isnull=True, articulo__saldos_pendientes_farmacia__isnull=True)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "articulo":
            kwargs["queryset"] = Articulo.objects.filter(medicamento__isnull=True, saldos_pendientes_farmacia__isnull=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    list_display = (
        "articulo",
        "anexo",
        "cantidad",
        "updated_at",
    )
    search_fields = (
        "articulo__nombre",
        "articulo__codigo",
        "anexo__nombre",
    )
    list_filter = ("anexo",)


@admin.register(TipoMovimiento)
class TipoMovimientoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activo")


class DetalleMovimientoInline(admin.TabularInline):
    model = DetalleMovimiento
    extra = 0
    readonly_fields = ("articulo", "cantidad")
    can_delete = False


@admin.register(MovimientoInventario)
class MovimientoInventarioAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tipo_movimiento",
        "anexo_origen",
        "anexo_destino",
        "usuario",
        "fecha",
    )

    list_filter = (
        "tipo_movimiento",
        "anexo_origen",
        "anexo_destino",
    )

    search_fields = (
        "usuario__email",
        "motivo",
    )

    readonly_fields = (
        "tipo_movimiento",
        "anexo_origen",
        "anexo_destino",
        "usuario",
        "motivo",
        "fecha",
    )

    inlines = [DetalleMovimientoInline]

    def has_add_permission(self, request):
        return False


@admin.register(RecursoRequerimiento)
class RecursoRequerimientoAdmin(admin.ModelAdmin):
    list_display = (
        "requerimiento",
        "articulo",
        "cantidad",
        "usuario",
        "fecha",
    )

    search_fields = (
        "articulo__nombre",
        "usuario__email",
    )

    readonly_fields = (
        "requerimiento",
        "articulo",
        "cantidad",
        "usuario",
        "movimiento",
        "fecha",
    )

    def has_add_permission(self, request):
        return False
