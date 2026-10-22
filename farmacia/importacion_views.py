import csv
import io

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from .decorators import acceso_farmacia
from .importacion import ArchivoMedicamentosForm, COLUMNAS, COLUMNAS_OPCIONALES, preparar_importacion, confirmar_importacion, token_revision, que_revisar
from .models import ImportacionMedicamentos
from .bodega import ubicaciones_recepcion


@acceso_farmacia
@require_GET
def plantilla_csv(request):
    salida = io.StringIO(newline="")
    escritor = csv.writer(salida, delimiter=";", lineterminator="\r\n")
    escritor.writerow([*COLUMNAS, *COLUMNAS_OPCIONALES])
    response = HttpResponse("\ufeff" + salida.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="plantilla_medicamentos.csv"'
    return response


@acceso_farmacia
@require_http_methods(["GET", "POST"])
def importar_csv(request):
    form = ArchivoMedicamentosForm(request.POST if request.method == "POST" else None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            carga = preparar_importacion(archivo=form.cleaned_data["archivo"], usuario=request.user)
        except ValidationError as error:
            form.add_error("archivo", error)
        else:
            return redirect("farmacia:revisar_importacion", pk=carga.pk)
    return render(request, "farmacia/importar_csv.html", {
        "titulo": "Importar medicamentos y recepciones", "form": form,
        "ubicaciones": ubicaciones_recepcion(),
        "importaciones": ImportacionMedicamentos.objects.filter(usuario=request.user, estado="IMPORTADA")[:5],
    })


@acceso_farmacia
@require_http_methods(["GET", "POST"])
def revisar_importacion(request, pk):
    carga = get_object_or_404(ImportacionMedicamentos, pk=pk, usuario=request.user)
    if request.method == "POST":
        try:
            if request.POST.get("accion", "completa") not in ("completa", "parcial"):
                raise ValidationError("Seleccione una opción de importación válida.")
            parcial = request.POST.get("accion") == "parcial"
            if parcial and request.POST.get("aceptar_omisiones") != "si":
                raise ValidationError("Confirme que revisó las filas que se omitirán.")
            if not request.POST.get("revision"):
                raise ValidationError("Actualice la vista previa y confirme el resumen antes de importar.")
            carga = confirmar_importacion(importacion_id=carga.pk, usuario=request.user,
                                         omitir_errores=parcial, revision=request.POST["revision"])
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        except IntegrityError:
            messages.error(request, "Los datos cambiaron durante la confirmación o esta recepción ya existe. No se guardó ningún cambio de esta carga. Revise el historial y vuelva a cargar el archivo.")
        else:
            if carga.estado == "IMPORTADA":
                messages.success(request, f"Carga completada: {carga.cantidad_importada} medicamentos nuevos, {carga.cantidad_lotes_creados} lotes nuevos y {carga.cantidad_movimientos} recepciones registradas y {carga.cantidad_omitidas} filas omitidas.")
            else:
                messages.error(request, "La validación requiere una nueva revisión. No se guardaron medicamentos, lotes ni existencias. Revise el resumen actualizado y las filas con errores antes de confirmar nuevamente.")
        return redirect("farmacia:revisar_importacion", pk=carga.pk)
    solo_errores = request.GET.get("filas") == "errores"
    filas = [fila for fila in carga.filas if fila["errores"]] if solo_errores else carga.filas
    pagina = Paginator(filas, 20).get_page(request.GET.get("page"))
    pagina.object_list = [dict(fila, que_revisar=que_revisar(fila)) for fila in pagina.object_list]
    return render(request, "farmacia/revisar_importacion.html", {
        "titulo": "Importación completada" if carga.estado == "IMPORTADA" else "Revisar importación",
        "carga": carga, "pagina": pagina, "revision": token_revision(carga),
        "solo_errores": solo_errores, "filtros_query": "filas=errores" if solo_errores else "",
    })


def celda_segura(valor):
    """Evita ejecutar fórmulas al abrir el reporte CSV en una planilla."""
    texto = str(valor)
    if texto.lstrip().startswith(("=", "+", "-", "@")) or texto.startswith(("\t", "\r", "\n")):
        return "'" + texto
    return texto


@acceso_farmacia
@require_GET
def reporte_importacion(request, pk):
    carga = get_object_or_404(ImportacionMedicamentos, pk=pk, usuario=request.user)
    salida = io.StringIO(newline="")
    escritor = csv.writer(salida, delimiter=";", lineterminator="\r\n")
    columnas = [*COLUMNAS, *COLUMNAS_OPCIONALES]
    escritor.writerow(["fila_original", "resultado", "motivos", "que_revisar", "movimiento_id", *columnas])
    for fila in carga.filas:
        resultado = fila.get("resultado") or ("CON ERRORES" if fila["errores"] else "VALIDA")
        if carga.estado == "IMPORTADA" and not fila.get("resultado"):
            resultado = "IMPORTADA"
        valores = [fila["numero"], resultado, " | ".join(fila["errores"]), que_revisar(fila),
                   fila.get("movimiento_id", ""), *[fila["datos"].get(campo, "") for campo in columnas]]
        escritor.writerow([celda_segura(valor) for valor in valores])
    response = HttpResponse("\ufeff" + salida.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="reporte_importacion_{carga.pk}.csv"'
    response["Cache-Control"] = "private, no-store"
    return response
