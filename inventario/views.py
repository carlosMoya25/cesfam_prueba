from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from usuarios.utils import puede_gestionar_inventario
from .forms import (
    ArticuloForm, CategoriaArticuloForm, AjusteInventarioForm, EntradaInventarioForm,
    SalidaInventarioForm, TransferenciaInventarioForm,
)
from .models import Articulo, CategoriaArticulo, StockAnexo
from .filters import (
    FiltrosArticulosForm, consultar_existencias, consultar_movimientos,
    contexto_listado, filtrar_articulos,
)
from .services import registrar_entrada, registrar_salida, registrar_transferencia, registrar_ajuste


def validar_acceso_inventario(usuario):
    return puede_gestionar_inventario(usuario)


def contexto_stock(request):
    form, stocks = consultar_existencias(request.GET)
    contexto = contexto_listado(form, stocks, request.GET)
    estado_query = request.GET.copy()
    for campo in list(estado_query):
        if campo not in form.fields or campo in ("estado", "page") or not estado_query.get(campo):
            estado_query.pop(campo)
    contexto["filtros_estado_query"] = estado_query.urlencode()
    articulos = filtrar_articulos(form.cleaned_data) if form.is_valid() else Articulo.objects.none()
    comunes = request.GET.copy()
    for campo in list(comunes):
        if campo not in ("buscar", "anexo", "categoria") or not comunes.get(campo):
            comunes.pop(campo)
    catalogo = comunes.copy()
    catalogo.pop("anexo", None)
    contexto.update({
        "total_articulos": articulos.count(),
        "catalogo_query": catalogo.urlencode(),
        "navegacion_query": comunes.urlencode(),
        "filtros_expandidos": bool(form.errors or request.GET.get("categoria") or request.GET.get("estado")),
        "stocks": contexto["pagina"],
        "total_registros": stocks.count(),
        "total_unidades": stocks.aggregate(total=Sum("cantidad"))["total"] or 0,
        "total_stock_bajo": stocks.filter(cantidad__gt=0, cantidad__lte=F("articulo__stock_minimo")).count(),
        "total_agotados": stocks.filter(cantidad=0).count(),
    })
    return contexto


@login_required
def panel_inventario(request):
    if not validar_acceso_inventario(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder al inventario.")
    return render(request, "inventario/panel.html", contexto_stock(request))


@login_required
def consultar_stock(request):
    if not validar_acceso_inventario(request.user):
        return HttpResponseForbidden("No tienes permisos para consultar el inventario.")
    return render(request, "inventario/consultar_stock.html", contexto_stock(request))


def registrar_movimiento(request, clase_form, servicio, operacion, titulo, boton):
    if not validar_acceso_inventario(request.user):
        return HttpResponseForbidden("No tienes permisos para registrar movimientos.")
    inicial = {campo: request.GET[campo] for campo in ("articulo", "anexo", "anexo_origen") if campo in request.GET}
    form = clase_form(request.POST if request.method == "POST" else None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        try:
            with transaction.atomic():
                servicio(usuario=request.user, **datos)
                origen = datos.get("anexo_origen") or datos.get("anexo")
                saldo = StockAnexo.objects.get(articulo=datos["articulo"], anexo=origen).cantidad
                nombre = datos["articulo"].nombre
                cantidad = datos["cantidad"]
                if operacion == "transferencia":
                    destino = datos["anexo_destino"]
                    saldo_destino = StockAnexo.objects.get(articulo=datos["articulo"], anexo=destino).cantidad
                    mensaje = f"Se transfirieron {cantidad} unidades de {nombre}. Quedan {saldo} en {origen.nombre} y {saldo_destino} en {destino.nombre}."
                else:
                    suma = operacion == "entrada" or datos.get("tipo_ajuste") == "positivo"
                    verbo = "agregaron" if suma else "descontaron"
                    mensaje = f"Se {verbo} {cantidad} unidades de {nombre} en {origen.nombre}. Saldo: {saldo}."
            messages.success(request, mensaje)
            return redirect("inventario:panel")
        except ValidationError as error:
            form.add_error(None, error)
    return render(request, "inventario/movimiento_form.html", {
        "form": form, "titulo": titulo, "boton": boton, "operacion": operacion,
        "articulos_movimiento": list(Articulo.objects.filter(
            activo=True, medicamento__isnull=True, saldos_pendientes_farmacia__isnull=True,
        ).order_by("nombre", "pk").values("id", "nombre", "codigo")),
        "stocks_movimiento": list(StockAnexo.objects.filter(
            articulo__medicamento__isnull=True, articulo__saldos_pendientes_farmacia__isnull=True, articulo__activo=True, anexo__activo=True,
        ).values("articulo_id", "anexo_id", "cantidad")),
    })


@login_required
def entrada_inventario(request):
    return registrar_movimiento(request, EntradaInventarioForm, registrar_entrada, "entrada", "Registrar entrada", "Registrar entrada")


@login_required
def salida_inventario(request):
    return registrar_movimiento(request, SalidaInventarioForm, registrar_salida, "salida", "Registrar salida", "Registrar salida")


@login_required
def transferencia_inventario(request):
    return registrar_movimiento(request, TransferenciaInventarioForm, registrar_transferencia, "transferencia", "Transferir artículos", "Confirmar transferencia")


@login_required
def ajuste_inventario(request):
    return registrar_movimiento(request, AjusteInventarioForm, registrar_ajuste, "ajuste", "Ajustar existencias", "Confirmar ajuste")


@login_required
def historial_movimientos(request):
    if not validar_acceso_inventario(request.user):
        return HttpResponseForbidden("No tienes permisos para consultar el historial.")
    form, movimientos = consultar_movimientos(request.GET)
    contexto = contexto_listado(form, movimientos, request.GET)
    comunes = request.GET.copy()
    for campo in list(comunes):
        if campo not in ("buscar", "anexo", "categoria") or not comunes.get(campo):
            comunes.pop(campo)
    contexto["navegacion_query"] = comunes.urlencode()
    contexto["es_historial"] = True
    contexto["filtros_expandidos"] = bool(form.errors or any(request.GET.get(campo) for campo in ("categoria", "tipo", "responsable", "desde", "hasta")))
    return render(request, "inventario/historial.html", {**contexto, "movimientos": contexto["pagina"]})


@login_required
def categorias(request):
    if not puede_gestionar_inventario(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    categorias = CategoriaArticulo.objects.all().order_by("nombre")

    return render(
        request,
        "inventario/categorias.html",
        {"categorias": categorias},
    )


@login_required
def crear_categoria(request):
    if not puede_gestionar_inventario(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    if request.method == "POST":
        form = CategoriaArticuloForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Categoría creada correctamente.")
            return redirect("inventario:categorias")
    else:
        form = CategoriaArticuloForm()

    return render(
        request,
        "inventario/categoria_form.html",
        {
            "form": form,
            "titulo": "Nueva categoría",
        },
    )


@login_required
def editar_categoria(request, pk):
    if not puede_gestionar_inventario(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    categoria = get_object_or_404(CategoriaArticulo, pk=pk)

    if request.method == "POST":
        form = CategoriaArticuloForm(request.POST, instance=categoria)

        if form.is_valid():
            form.save()
            messages.success(request, "Categoría actualizada correctamente.")
            return redirect("inventario:categorias")
    else:
        form = CategoriaArticuloForm(instance=categoria)

    return render(
        request,
        "inventario/categoria_form.html",
        {
            "form": form,
            "titulo": "Editar categoría",
            "categoria": categoria,
        },
    )


@login_required
def articulos(request):
    if not puede_gestionar_inventario(request.user):
        return HttpResponseForbidden("No tienes permisos para administrar el inventario.")
    form = FiltrosArticulosForm(request.GET)
    articulos = filtrar_articulos(form.cleaned_data) if form.is_valid() else Articulo.objects.none()
    if form.is_valid() and form.cleaned_data["estado"]:
        articulos = articulos.filter(activo=form.cleaned_data["estado"] == "activo")
    contexto = contexto_listado(form, articulos.order_by("nombre", "pk"), request.GET)
    return render(request, "inventario/articulos.html", {**contexto, "articulos": contexto["pagina"]})


@login_required
def crear_articulo(request):
    if not puede_gestionar_inventario(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    if request.method == "POST":
        form = ArticuloForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Artículo creado correctamente.")
            return redirect("inventario:articulos")
    else:
        form = ArticuloForm()

    return render(
        request,
        "inventario/articulo_form.html",
        {
            "form": form,
            "titulo": "Nuevo artículo",
        },
    )


@login_required
def editar_articulo(request, pk):
    if not puede_gestionar_inventario(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    articulo = get_object_or_404(Articulo, pk=pk, medicamento__isnull=True, saldos_pendientes_farmacia__isnull=True)

    if request.method == "POST":
        form = ArticuloForm(request.POST, instance=articulo)

        if form.is_valid():
            form.save()
            messages.success(request, "Artículo actualizado correctamente.")
            return redirect("inventario:articulos")
    else:
        form = ArticuloForm(instance=articulo)

    return render(
        request,
        "inventario/articulo_form.html",
        {
            "form": form,
            "titulo": "Editar artículo",
            "articulo": articulo,
        },
    )