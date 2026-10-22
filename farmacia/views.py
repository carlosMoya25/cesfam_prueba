
from farmacia.models import MovimientoFarmacia
from datetime import datetime, time, timedelta
from django.utils import timezone
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect
from .decorators import acceso_farmacia
from .forms import IngresoMedicamentoForm
from .services import registrar_entrada_lote
from .models import Medicamento, LoteMedicamento, StockLote
from .forms import MedicamentoForm
from .registro import RegistroMedicamentoForm
from django.views.decorators.http import require_POST
from django.db.models import Sum, Q, OuterRef, Subquery, IntegerField
from django.db import IntegrityError
from django.db.models.functions import Coalesce
from django.core.paginator import Paginator
from .filters import consultar_farmacia, contexto_filtros, FiltrosHistorialFarmaciaForm
from django.shortcuts import get_object_or_404



def movimientos_filtrados(form, medicamentos, stocks):
    if not form.is_valid():
        return MovimientoFarmacia.objects.none()
    datos = form.cleaned_data
    lotes = LoteMedicamento.objects.filter(medicamento__in=medicamentos).values("pk")
    if datos["vencimiento"] or datos["existencias"]:
        lotes = stocks.values("lote_id")
    movimientos = MovimientoFarmacia.objects.filter(detalles__lote__in=lotes)
    if datos["ubicacion"]:
        movimientos = movimientos.filter(Q(anexo_origen=datos["ubicacion"]) | Q(anexo_destino=datos["ubicacion"]))
    if datos.get("importacion"):
        movimientos = movimientos.filter(importacion_id=datos["importacion"])
    if datos.get("tipo"):
        movimientos = movimientos.filter(tipo=datos["tipo"])
    if datos.get("desde"):
        movimientos = movimientos.filter(fecha__gte=timezone.make_aware(datetime.combine(datos["desde"], time.min)))
    if datos.get("hasta"):
        # Usar el último instante del día evita desbordar date.max.
        movimientos = movimientos.filter(fecha__lte=timezone.make_aware(datetime.combine(datos["hasta"], time.max)))
    return movimientos.distinct().select_related("usuario", "anexo_origen", "anexo_destino", "importacion").prefetch_related("detalles__lote__medicamento__articulo").order_by("-fecha", "-pk")


@acceso_farmacia
def dashboard(request):
    form, medicamentos, stocks = consultar_farmacia(request.GET)
    hoy = timezone.localdate()
    fecha_limite = hoy + timedelta(days=30)
    movimientos = movimientos_filtrados(form, medicamentos, stocks)
    utilizables = stocks.filter(
        lote__activo=True, lote__medicamento__activo=True,
        lote__medicamento__articulo__activo=True, anexo__activo=True,
        lote__fecha_vencimiento__gte=hoy,
    )
    pagina = Paginator(stocks.order_by("lote__fecha_vencimiento", "lote__medicamento__articulo__nombre", "pk"), 20).get_page(request.GET.get("page"))
    contexto = {
        **contexto_filtros(form),
        "total_medicamentos": medicamentos.count(),
        "total_medicamentos_activos": medicamentos.filter(activo=True, articulo__activo=True).count(),
        "unidades_utilizables": utilizables.aggregate(total=Sum("cantidad"))["total"] or 0,
        "total_stock": stocks.aggregate(total=Sum("cantidad"))["total"] or 0,
        "total_proximos": stocks.filter(
            cantidad__gt=0, lote__activo=True,
            lote__fecha_vencimiento__range=(hoy, fecha_limite),
        ).values("lote_id").distinct().count(),
        "lotes_vencidos": stocks.filter(
            cantidad__gt=0, lote__fecha_vencimiento__lt=hoy,
        ).values("lote_id").distinct().count(),
        "movimientos_recientes": movimientos.order_by("-fecha", "-pk")[:10],
        "total_movimientos": movimientos.count(),
        "pagina": pagina,
        "stocks": pagina,
        "hoy": hoy,
        "fecha_limite": fecha_limite,
    }
    return render(request, "farmacia/dashboard.html", contexto)


@acceso_farmacia
def historial(request):
    form, medicamentos, stocks = consultar_farmacia(request.GET, FiltrosHistorialFarmaciaForm)
    movimientos = movimientos_filtrados(form, medicamentos, stocks)
    pagina = Paginator(movimientos, 20).get_page(request.GET.get("page"))
    return render(request, "farmacia/historial.html", {
        **contexto_filtros(form), "pagina": pagina, "movimientos": pagina,
    })


@acceso_farmacia
def lista_medicamentos(request):
    form, medicamentos, stocks = consultar_farmacia(request.GET)
    saldos = stocks.filter(lote__medicamento_id=OuterRef("pk")).order_by().values(
        "lote__medicamento_id"
    ).annotate(total=Sum("cantidad")).values("total")
    medicamentos = medicamentos.annotate(
        stock_total=Coalesce(Subquery(saldos, output_field=IntegerField()), 0)
    ).order_by("articulo__nombre", "pk")
    pagina = Paginator(medicamentos, 20).get_page(request.GET.get("page"))
    return render(request, "farmacia/medicamentos_lista.html", {
        **contexto_filtros(form), "medicamentos": pagina, "pagina": pagina,
    })


@acceso_farmacia
def crear_medicamento(request):
    form = RegistroMedicamentoForm(
        request.POST if request.method == "POST" else None
    )

    if request.method == "POST" and form.is_valid():
        try:
            medicamento, movimiento = form.guardar_recepcion(usuario=request.user)
        except ValidationError as error:
            form.add_error(None, error)
        except IntegrityError:
            form.add_error(None, "El código o la recepción ya existen, o los datos cambiaron. No se guardó ningún cambio. Revise el catálogo y el historial antes de intentarlo nuevamente.")
        else:
            messages.success(request, f"Medicamento, lote y recepción registrados correctamente. Movimiento #{movimiento.pk}.")
            return redirect("farmacia:detalle_medicamento", pk=medicamento.pk)

    return render(
        request,
        "farmacia/medicamento_form.html",
        {
            "form": form,
            "titulo": "Registrar medicamento", "registro_con_recepcion": True,
        },
    )


@acceso_farmacia
def editar_medicamento(request, pk):
    medicamento = get_object_or_404(
        Medicamento,
        pk=pk,
    )

    form = MedicamentoForm(
        request.POST if request.method == "POST" else None,
        medicamento=medicamento,
    )

    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Medicamento actualizado correctamente.")
            return redirect("farmacia:detalle_medicamento", pk=medicamento.pk)

    return render(
        request,
        "farmacia/medicamento_form.html",
        {
            "form": form,
            "titulo": "Editar medicamento",
        },
    )


@acceso_farmacia
def detalle_medicamento(request, pk):
    medicamento = get_object_or_404(
        Medicamento.objects.select_related("articulo"),
        pk=pk,
    )

    form, _, stocks = consultar_farmacia(request.GET)
    stocks = stocks.filter(lote__medicamento=medicamento).order_by(
        "anexo__nombre", "lote__fecha_vencimiento", "pk",
    )
    pagina = Paginator(stocks, 20).get_page(request.GET.get("page"))
    return render(request, "farmacia/medicamento_detalle.html", {
        **contexto_filtros(form),
        "medicamento": medicamento,
        "stocks": pagina, "pagina": pagina,
        "stock_total": stocks.aggregate(total=Sum("cantidad"))["total"] or 0,
        "hoy": timezone.localdate(),
        "fecha_limite": timezone.localdate() + timedelta(days=30),
    })


@acceso_farmacia
@require_POST
def cambiar_estado_medicamento(request, pk):
    medicamento = get_object_or_404(
        Medicamento,
        pk=pk,
    )

    medicamento.activo = not medicamento.activo
    medicamento.save(update_fields=["activo", "updated_at"])

    messages.success(
        request,
        "Estado del medicamento actualizado."
    )

    return redirect("farmacia:lista_medicamentos")



@acceso_farmacia
def registrar_entrada(request):

    if request.method == "POST":
        form = IngresoMedicamentoForm(request.POST)

        if form.is_valid():
            datos = form.cleaned_data

            try:
                movimiento = registrar_entrada_lote(
                    medicamento=datos["medicamento"],
                    anexo=datos["anexo"],
                    lote=datos["lote"],
                    cantidad=datos["cantidad"],
                    usuario=request.user,
                    motivo=datos["motivo"],
                    proveedor=datos["proveedor"], documento_recepcion=datos["documento_recepcion"],
                    fecha_recepcion=datos["fecha_recepcion"],
                )

                messages.success(
                    request,
                    f"Ingreso registrado correctamente. "
                    f"Movimiento #{movimiento.pk}",
                )

                return redirect("farmacia:dashboard")

            except ValidationError as error:
                form.add_error(None, error)
            except IntegrityError:
                form.add_error(None, "La recepción ya fue registrada o sus datos cambiaron. Revise el historial antes de volver a intentar.")

    else:
        inicial = {}
        for campo in ("medicamento", "anexo", "lote"):
            try:
                valor = int(request.GET.get(campo, ""))
                if 0 < valor <= 9223372036854775807:
                    inicial[campo] = valor
            except (TypeError, ValueError):
                pass
        form = IngresoMedicamentoForm(initial=inicial)

    return render(
        request,
        "farmacia/registrar_entrada.html",
        {
            "form": form, "titulo": "Registrar entrada",
            "unidades_medicamentos": dict(Medicamento.objects.filter(activo=True, articulo__activo=True).values_list("pk", "unidad_medida")),
            "stocks_entrada": list(StockLote.objects.filter(
                lote__activo=True, lote__medicamento__activo=True,
                lote__medicamento__articulo__activo=True, anexo__activo=True,
                lote__fecha_vencimiento__gte=timezone.localdate(),
            ).values("lote_id", "anexo_id", "cantidad")),
            "lotes_disponibles": list(LoteMedicamento.objects.filter(
                medicamento__activo=True,
                medicamento__articulo__activo=True,
                activo=True,
                fecha_vencimiento__gte=timezone.localdate(),
            ).order_by("fecha_vencimiento", "numero_lote", "pk").values(
                "id", "medicamento_id", "numero_lote",
            )),
        },
    )




@acceso_farmacia
def pendientes_conciliacion(request):
    from .models import StockPendienteFarmacia
    pendientes = StockPendienteFarmacia.objects.select_related("articulo", "anexo", "usuario", "movimiento_regularizacion")
    pagina = Paginator(pendientes, 20).get_page(request.GET.get("page"))
    return render(request, "farmacia/pendientes_conciliacion.html", {
        "titulo": "Pendientes de conciliación", "pagina": pagina, "pendientes": pagina,
    })
