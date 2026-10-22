from datetime import timedelta

from django import forms
from django.db.models import Q
from django.utils import timezone

from organizacion.models import Anexo
from .models import Medicamento, StockLote, MovimientoFarmacia


class FiltrosFarmaciaForm(forms.Form):
    q = forms.CharField(
        required=False, label="Nombre, principio activo o código",
        max_length=150,
        widget=forms.TextInput(attrs={"type": "search", "placeholder": "Buscar medicamento, principio activo o código…"}),
    )
    ubicacion = forms.ModelChoiceField(
        queryset=Anexo.objects.all(), required=False,
        label="Ubicación", empty_label="Todas las ubicaciones",
    )
    estado = forms.ChoiceField(required=False, label="Estado del medicamento", choices=[
        ("", "Todos"), ("activo", "Activo"), ("inactivo", "Inactivo"),
    ])
    vencimiento = forms.ChoiceField(required=False, label="Vencimiento del lote", choices=[
        ("", "Todos"), ("vigente", "Sin vencer"),
        ("proximo", "Vence en los próximos 30 días"), ("vencido", "Vencido"),
    ])
    existencias = forms.ChoiceField(required=False, label="Existencias", choices=[
        ("", "Todas"), ("con_stock", "Con stock"), ("sin_stock", "Sin stock"),
    ])


class FiltrosHistorialFarmaciaForm(FiltrosFarmaciaForm):
    importacion = forms.UUIDField(required=False, widget=forms.HiddenInput)
    tipo = forms.ChoiceField(required=False, label="Tipo de movimiento", choices=[
        ("", "Todos los tipos"), *MovimientoFarmacia.TIPOS_MOVIMIENTO,
    ])
    desde = forms.DateField(required=False, label="Desde", widget=forms.DateInput(attrs={"type": "date"}))
    hasta = forms.DateField(required=False, label="Hasta", widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        datos = super().clean()
        if datos.get("desde") and datos.get("hasta") and datos["desde"] > datos["hasta"]:
            self.add_error("hasta", "La fecha final debe ser igual o posterior a la inicial.")
        return datos


def consultar_farmacia(params, form_class=FiltrosFarmaciaForm):
    """Filtra existencias sobre la misma fila de lote y ubicación."""
    form = form_class(params)
    medicamentos = Medicamento.objects.select_related("articulo")
    stocks = StockLote.objects.select_related("anexo", "lote__medicamento__articulo")
    if not form.is_valid():
        return form, medicamentos.none(), stocks.none()

    datos = form.cleaned_data
    if datos["q"]:
        medicamentos = medicamentos.filter(
            Q(articulo__nombre__icontains=datos["q"])
            | Q(principio_activo__icontains=datos["q"])
            | Q(articulo__codigo__icontains=datos["q"])
        )
    if datos["estado"] == "activo":
        medicamentos = medicamentos.filter(activo=True, articulo__activo=True)
    elif datos["estado"] == "inactivo":
        medicamentos = medicamentos.filter(Q(activo=False) | Q(articulo__activo=False))

    stocks = stocks.filter(lote__medicamento__in=medicamentos)
    if datos["ubicacion"]:
        stocks = stocks.filter(anexo=datos["ubicacion"])
    hoy = timezone.localdate()
    if datos["vencimiento"] == "vigente":
        stocks = stocks.filter(lote__fecha_vencimiento__gte=hoy)
    elif datos["vencimiento"] == "proximo":
        stocks = stocks.filter(lote__fecha_vencimiento__range=(hoy, hoy + timedelta(days=30)))
    elif datos["vencimiento"] == "vencido":
        stocks = stocks.filter(lote__fecha_vencimiento__lt=hoy)

    # El stock se evalúa dentro de la ubicación y vencimiento elegidos.
    con_stock = stocks.filter(cantidad__gt=0).values("lote__medicamento_id")
    if datos["existencias"] == "con_stock":
        medicamentos = medicamentos.filter(pk__in=con_stock)
        stocks = stocks.filter(cantidad__gt=0)
    elif datos["existencias"] == "sin_stock":
        medicamentos = medicamentos.exclude(pk__in=con_stock)
        stocks = stocks.filter(lote__medicamento__in=medicamentos, cantidad=0)
    elif datos["ubicacion"] or datos["vencimiento"]:
        medicamentos = medicamentos.filter(pk__in=stocks.values("lote__medicamento_id"))
    return form, medicamentos, stocks


def contexto_filtros(form):
    from .bodega import ubicaciones_recepcion
    # Solo conservar campos conocidos y omitir la página al cambiar de pantalla.
    params = form.data.copy()
    for key in list(params):
        if key not in form.fields or not params.get(key):
            params.pop(key)
    navegacion = params.copy()
    for key in list(navegacion):
        if key not in FiltrosFarmaciaForm.base_fields:
            navegacion.pop(key)
    vencimiento = navegacion.copy()
    vencimiento.pop("vencimiento", None)
    return {
        "bodega_central_id": ubicaciones_recepcion().values_list("pk", flat=True).first(),
        "filtros_form": form, "filtros_query": params.urlencode(), "hay_filtros": bool(params),
        "navegacion_query": navegacion.urlencode(), "vencimiento_query": vencimiento.urlencode(),
        "filtros_expandidos": bool(form.errors or any(params.get(key) for key in form.fields if key not in ("q", "ubicacion"))),
    }
