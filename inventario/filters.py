from datetime import datetime, time, timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.utils import timezone

from organizacion.models import Anexo
from .models import Articulo, CategoriaArticulo, MovimientoInventario, StockAnexo, TipoMovimiento


class FiltrosStockForm(forms.Form):
    buscar = forms.CharField(required=False, label="Nombre o código", max_length=150,
                             widget=forms.TextInput(attrs={"type": "search", "placeholder": "Buscar por nombre o código…"}))
    anexo = forms.ModelChoiceField(queryset=Anexo.objects.all(), required=False,
                                  label="Ubicación", empty_label="Todas las ubicaciones")
    categoria = forms.ModelChoiceField(queryset=CategoriaArticulo.objects.all(), required=False,
                                      label="Categoría", empty_label="Todas las categorías")
    estado = forms.ChoiceField(required=False, label="Existencias", choices=[
        ("", "Todas"), ("disponible", "Disponible"), ("bajo", "Stock bajo"), ("agotado", "Agotado"),
    ])


class FiltrosArticulosForm(FiltrosStockForm):
    anexo = None
    estado = forms.ChoiceField(required=False, label="Estado", choices=[
        ("", "Todos"), ("activo", "Activo"), ("inactivo", "Inactivo"),
    ])


class FiltrosHistorialForm(forms.Form):
    categoria = forms.ModelChoiceField(queryset=CategoriaArticulo.objects.all(), required=False,
                                      label="Categoría", empty_label="Todas las categorías")
    buscar = forms.CharField(required=False, label="Artículo o código", max_length=150, widget=forms.TextInput(attrs={"type": "search", "placeholder": "Buscar por nombre o código…"}))
    anexo = forms.ModelChoiceField(queryset=Anexo.objects.all(), required=False,
                                  label="Ubicación de origen o destino", empty_label="Todas las ubicaciones")
    tipo = forms.ModelChoiceField(queryset=TipoMovimiento.objects.all(), required=False,
                                 label="Tipo de movimiento", empty_label="Todos los tipos")
    responsable = forms.ModelChoiceField(
        queryset=get_user_model().objects.filter(movimientos_inventario__isnull=False).distinct(),
        required=False, label="Registrado por", empty_label="Todos los responsables")
    desde = forms.DateField(required=False, label="Desde", widget=forms.DateInput(attrs={"type": "date"}))
    hasta = forms.DateField(required=False, label="Hasta", widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        datos = super().clean()
        if datos.get("desde") and datos.get("hasta") and datos["desde"] > datos["hasta"]:
            self.add_error("hasta", "La fecha final debe ser igual o posterior a la inicial.")
        return datos


def filtrar_articulos(datos):
    articulos = Articulo.objects.filter(medicamento__isnull=True, saldos_pendientes_farmacia__isnull=True).select_related("categoria")
    if datos.get("buscar"):
        articulos = articulos.filter(Q(nombre__icontains=datos["buscar"]) | Q(codigo__icontains=datos["buscar"]))
    if datos.get("categoria"):
        articulos = articulos.filter(categoria=datos["categoria"])
    return articulos


def consultar_existencias(params):
    form = FiltrosStockForm(params)
    stocks = StockAnexo.objects.select_related("articulo__categoria", "anexo")
    if not form.is_valid():
        return form, stocks.none()
    datos = form.cleaned_data
    stocks = stocks.filter(articulo__in=filtrar_articulos(datos))
    if datos["anexo"]:
        stocks = stocks.filter(anexo=datos["anexo"])
    if datos["estado"] == "agotado":
        stocks = stocks.filter(cantidad=0)
    elif datos["estado"] == "bajo":
        stocks = stocks.filter(cantidad__gt=0, cantidad__lte=F("articulo__stock_minimo"))
    elif datos["estado"] == "disponible":
        stocks = stocks.filter(cantidad__gt=F("articulo__stock_minimo"))
    return form, stocks.order_by("articulo__nombre", "anexo__nombre", "pk")


def consultar_movimientos(params):
    form = FiltrosHistorialForm(params)
    movimientos = MovimientoInventario.objects.select_related(
        "tipo_movimiento", "anexo_origen", "anexo_destino", "usuario",
    ).prefetch_related("detalles__articulo")
    if not form.is_valid():
        return form, movimientos.none()
    datos = form.cleaned_data
    movimientos = movimientos.filter(detalles__articulo__in=filtrar_articulos(datos))
    if datos["anexo"]:
        movimientos = movimientos.filter(Q(anexo_origen=datos["anexo"]) | Q(anexo_destino=datos["anexo"]))
    if datos["tipo"]:
        movimientos = movimientos.filter(tipo_movimiento=datos["tipo"])
    if datos["responsable"]:
        movimientos = movimientos.filter(usuario=datos["responsable"])
    if datos["desde"]:
        movimientos = movimientos.filter(fecha__gte=timezone.make_aware(datetime.combine(datos["desde"], time.min)))
    if datos["hasta"]:
        movimientos = movimientos.filter(fecha__lt=timezone.make_aware(datetime.combine(datos["hasta"] + timedelta(days=1), time.min)))
    return form, movimientos.distinct().order_by("-fecha", "-pk")


def contexto_listado(form, queryset, params):
    filtros = params.copy()
    for campo in list(filtros):
        if campo not in form.fields or not filtros.get(campo):
            filtros.pop(campo)
    pagina = Paginator(queryset, 20).get_page(params.get("page"))
    return {"filtros_form": form, "filtros_query": filtros.urlencode(), "pagina": pagina}
