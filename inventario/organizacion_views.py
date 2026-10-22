from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from organizacion.forms import DepartamentoForm, UbicacionInventarioForm
from usuarios.utils import puede_gestionar_inventario


def crear_registro(request, *, form_class, titulo, descripcion, boton, mensaje):
    if not request.user.is_active or not puede_gestionar_inventario(request.user):
        return HttpResponseForbidden("No tienes permisos para administrar el inventario.")
    form = form_class(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        registro = form.save()
        messages.success(request, f"{mensaje}: {registro.nombre}.")
        return redirect("inventario:panel")
    return render(request, "inventario/organizacion_form.html", {
        "form": form, "titulo": titulo, "descripcion": descripcion, "boton": boton,
    })


@login_required
@require_http_methods(["GET", "POST"])
def crear_departamento(request):
    return crear_registro(
        request, form_class=DepartamentoForm, titulo="Crear departamento",
        descripcion="Registre un departamento para utilizarlo en los usuarios y requerimientos del sistema.",
        boton="Guardar departamento", mensaje="Departamento creado correctamente",
    )


@login_required
@require_http_methods(["GET", "POST"])
def crear_ubicacion(request):
    return crear_registro(
        request, form_class=UbicacionInventarioForm, titulo="Crear ubicación de inventario",
        descripcion="Registre el establecimiento o anexo donde se almacenan las existencias.",
        boton="Guardar ubicación", mensaje="Ubicación creada correctamente",
    )
