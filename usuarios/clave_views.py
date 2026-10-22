from django import forms
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from .claves import campos_clave_temporal, registrar_evento_clave, validar_clave_temporal
from .models import Usuario
from .utils import es_administrador


class ClaveTemporalForm(forms.Form):
    def __init__(self, *args, usuario, administrador, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        self.administrador = administrador
        self.advertencias_clave = []
        self.token_autorizacion = ""
        self.fields.update(campos_clave_temporal())

    def clean(self):
        datos = super().clean()
        validar_clave_temporal(self, usuario=self.usuario, administrador=self.administrador)
        return datos


class CambioClaveForm(PasswordChangeForm):
    def clean(self):
        datos = super().clean()
        if datos.get("new_password1") and self.user.check_password(datos["new_password1"]):
            self.add_error("new_password2", "La nueva contraseña debe ser distinta de la actual.")
        return datos


@sensitive_post_parameters()
@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def cambiar_clave(request):
    usuario = request.user
    form = CambioClaveForm(usuario) if usuario.has_usable_password() else None
    if request.method == "POST" and form is not None:
        with transaction.atomic():
            usuario = Usuario.objects.select_for_update().get(pk=usuario.pk)
            form = CambioClaveForm(usuario, request.POST)
            if form.is_valid():
                form.save()
                usuario.debe_cambiar_clave = False
                usuario.save(update_fields=["debe_cambiar_clave"])
                registrar_evento_clave(usuario=usuario, realizado_por=usuario,
                                       accion="El usuario cambió su contraseña de acceso.")
                update_session_auth_hash(request, usuario)
                messages.success(request, "Contraseña actualizada correctamente.")
                return redirect("dashboard")
    return render(request, "usuarios/cambiar_clave.html", {
        "form": form, "titulo": "Cambiar contraseña",
        "cambio_obligatorio": usuario.debe_cambiar_clave,
    })


@sensitive_post_parameters()
@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def restablecer_clave(request, pk):
    if not request.user.is_active or not es_administrador(request.user):
        return HttpResponseForbidden("Solo un administrador puede restablecer contraseñas temporales.")
    usuario = get_object_or_404(Usuario, pk=pk)
    if usuario.pk == request.user.pk:
        return redirect("usuarios:cambiar_clave")
    if request.method == "POST":
        with transaction.atomic():
            usuario = Usuario.objects.select_for_update().get(pk=pk)
            form = ClaveTemporalForm(request.POST, usuario=usuario, administrador=request.user)
            if form.is_valid():
                usuario.set_password(form.cleaned_data["clave1"])
                usuario.debe_cambiar_clave = True
                usuario.save(update_fields=["password", "debe_cambiar_clave"])
                registrar_evento_clave(usuario=usuario, realizado_por=request.user,
                    accion="Se restableció una contraseña temporal; el usuario deberá cambiarla al ingresar.",
                    advertencias=form.advertencias_clave)
                messages.success(request, "Contraseña temporal restablecida. El usuario deberá cambiarla al ingresar.")
                return redirect("usuarios:detalle_usuario", pk=usuario.pk)
    else:
        form = ClaveTemporalForm(usuario=usuario, administrador=request.user)
    return render(request, "usuarios/restablecer_clave.html", {
        "form": form, "usuario_afectado": usuario, "titulo": "Restablecer contraseña temporal",
    })
