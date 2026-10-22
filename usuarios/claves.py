"""Contraseñas temporales y autorización explícita de excepciones."""
import json

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.utils.crypto import salted_hmac

from .models import HistorialSeguridad
from .utils import es_administrador


def campos_clave_temporal():
    return {
        "clave1": forms.CharField(label="Contraseña temporal", required=False, strip=False,
                                  max_length=128, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})),
        "clave2": forms.CharField(label="Confirmar contraseña temporal", required=False, strip=False,
                                  max_length=128, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})),
        "autorizar_clave_debil": forms.BooleanField(required=False,
            label="Autorizo crear esta contraseña temporal aunque no cumpla los requisitos indicados."),
        "autorizacion_clave": forms.CharField(required=False, widget=forms.HiddenInput),
    }


def validar_clave_temporal(form, *, usuario, administrador):
    form.advertencias_clave = []
    form.token_autorizacion = ""
    clave1, clave2 = form.cleaned_data.get("clave1"), form.cleaned_data.get("clave2")
    for campo in ("clave1", "clave2"):
        if not form.cleaned_data.get(campo) and campo not in form.errors:
            form.add_error(campo, "Este campo es obligatorio para habilitar el acceso con contraseña.")
    if not clave1 or not clave2:
        return
    if clave1 != clave2:
        form.add_error("clave2", "Las contraseñas no coinciden.")
        return
    try:
        validate_password(clave1, user=usuario)
    except ValidationError as error:
        form.advertencias_clave = error.messages
        # El token no contiene la clave ni un resumen que permita adivinarla sin el secreto del servidor.
        contexto = [str(getattr(administrador, "pk", "")), str(usuario.pk), usuario.email,
                    usuario.nombres, usuario.apellido_paterno, usuario.apellido_materno, usuario.password, clave1]
        huella = salted_hmac("usuarios.clave-temporal", json.dumps(contexto, ensure_ascii=False), algorithm="sha256").hexdigest()
        form.token_autorizacion = signing.dumps(huella, salt="usuarios.autorizacion-clave")
        try:
            revisada = signing.loads(form.cleaned_data.get("autorizacion_clave", ""),
                                     salt="usuarios.autorizacion-clave", max_age=600)
        except signing.BadSignature:
            revisada = None
        autorizado = (administrador and administrador.is_active and es_administrador(administrador)
                       and form.cleaned_data.get("autorizar_clave_debil") and revisada == huella)
        if not autorizado:
            form.add_error("clave2", "Revise los requisitos incumplidos. Para autorizar la excepción, vuelva a escribir ambas contraseñas y marque la confirmación.")


def registrar_evento_clave(*, usuario, realizado_por, accion, advertencias=()):
    descripcion = accion
    if advertencias:
        descripcion += " El administrador autorizó explícitamente una contraseña temporal débil. Requisitos incumplidos: " + " ".join(advertencias)
    HistorialSeguridad.objects.create(
        tipo_accion="OTRO", usuario_afectado=usuario, email_afectado=usuario.email,
        realizado_por=realizado_por, descripcion=descripcion,
    )
