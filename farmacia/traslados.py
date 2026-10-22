import uuid

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Q, OuterRef, Subquery, IntegerField
from django.db.models.functions import Coalesce
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from organizacion.models import Anexo
from .bodega import exigir_bodega_central, ubicaciones_recepcion
from .decorators import acceso_farmacia
from .models import LoteMedicamento, StockLote, MovimientoFarmacia
from .services import registrar_traslado_bodega


class TrasladoFarmaciaForm(forms.Form):
    lote = forms.ModelChoiceField(queryset=LoteMedicamento.objects.none(), label="Medicamento y lote",
                                  empty_label="Seleccione el medicamento y lote en Bodega Central")
    destino = forms.ModelChoiceField(queryset=Anexo.objects.none(), label="Anexo de destino",
                                     empty_label="Seleccione un destino")
    cantidad = forms.IntegerField(label="Cantidad a trasladar", min_value=1, max_value=2147483647)
    documento_traslado = forms.CharField(label="Documento o referencia del traslado", max_length=100)
    motivo = forms.CharField(label="Observaciones", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    solicitud_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lote"].queryset = LoteMedicamento.objects.filter(
            medicamento__activo=True, medicamento__articulo__activo=True,
            activo=True, fecha_vencimiento__gte=timezone.localdate(),
            stocks__anexo__in=ubicaciones_recepcion(), stocks__cantidad__gt=0,
        ).select_related("medicamento__articulo").order_by("medicamento__articulo__nombre", "fecha_vencimiento", "numero_lote")
        if self.is_bound and usuario:
            try:
                identificador = uuid.UUID(str(self.data.get("solicitud_id", "")))
            except (ValueError, TypeError, AttributeError):
                identificador = None
            previo = MovimientoFarmacia.objects.filter(clave_traslado=identificador, usuario=usuario).first() if identificador else None
            if previo:
                # Un reintento idéntico puede consultar su lote aun si quedó sin saldo.
                elegibles = self.fields["lote"].queryset.values("pk")
                self.fields["lote"].queryset = LoteMedicamento.objects.filter(
                    Q(pk__in=elegibles) | Q(movimientos_farmacia__movimiento=previo)
                ).select_related("medicamento__articulo").distinct()
        saldo_central = StockLote.objects.filter(
            lote_id=OuterRef("pk"), anexo__in=ubicaciones_recepcion(),
        ).values("cantidad")[:1]
        self.fields["lote"].queryset = self.fields["lote"].queryset.annotate(
            stock_bodega=Coalesce(Subquery(saldo_central, output_field=IntegerField()), 0),
        )
        self.fields["lote"].label_from_instance = lambda lote: (
            f"{lote.medicamento.articulo.nombre} · {lote.medicamento.articulo.codigo or 'Sin código'} · {lote.medicamento.concentracion} · "
            f"Lote {lote.numero_lote} · Vence {lote.fecha_vencimiento:%d/%m/%Y} · Stock: {lote.stock_bodega} {lote.medicamento.unidad_medida or '(unidad sin definir)'}"
        )
        self.fields["destino"].queryset = Anexo.objects.filter(activo=True).exclude(pk__in=ubicaciones_recepcion())
        self.fields["documento_traslado"].help_text = "Referencia de la guía, acta o documento interno que respalda la distribución."

    def clean(self):
        datos = super().clean()
        try:
            exigir_bodega_central()
        except ValidationError as error:
            raise forms.ValidationError(error.messages)
        return datos


@acceso_farmacia
@require_http_methods(["GET", "POST"])
def trasladar_a_anexo(request):
    inicial = {}
    if request.method == "GET":
        try:
            lote_id = int(request.GET.get("lote", ""))
            if 0 < lote_id <= 9223372036854775807:
                inicial["lote"] = lote_id
        except (ValueError, TypeError):
            pass
    form = TrasladoFarmaciaForm(request.POST if request.method == "POST" else None, initial=inicial, usuario=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            movimiento = registrar_traslado_bodega(
                lote=form.cleaned_data["lote"], destino=form.cleaned_data["destino"],
                cantidad=form.cleaned_data["cantidad"], usuario=request.user,
                documento_traslado=form.cleaned_data["documento_traslado"],
                solicitud_id=form.cleaned_data["solicitud_id"], motivo=form.cleaned_data["motivo"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        except IntegrityError:
            form.add_error(None, "El traslado ya existe o los datos cambiaron. No se guardó otro movimiento; revise el historial antes de reintentar.")
        else:
            messages.success(request, f"Traslado registrado. Movimiento #{movimiento.pk}: Bodega Central → {movimiento.anexo_destino.nombre}.")
            return redirect("farmacia:historial")
    lotes = form.fields["lote"].queryset
    stocks = list(StockLote.objects.filter(lote__in=lotes, anexo__in=ubicaciones_recepcion()).values(
        "lote_id", "cantidad", "lote__medicamento__unidad_medida", "lote__numero_lote",
        "lote__fecha_vencimiento", "lote__medicamento__concentracion",
        "lote__medicamento__articulo__nombre", "lote__medicamento__articulo__codigo",
    ))
    stocks_anexos = list(StockLote.objects.filter(
        lote__in=lotes, anexo__in=form.fields["destino"].queryset,
    ).values("lote_id", "anexo_id", "cantidad"))
    return render(request, "farmacia/trasladar.html", {
        "form": form, "titulo": "Trasladar a anexo", "stocks_traslado": stocks, "stocks_anexos": stocks_anexos,
    })
