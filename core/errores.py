from urllib.parse import urlsplit

from django.contrib import admin
from django.core.exceptions import DisallowedHost
from django.http import HttpResponse
from django.template.loader import get_template
from django.urls import reverse, resolve, Resolver404
from django.utils.cache import patch_cache_control
from django.utils.http import url_has_allowed_host_and_scheme


MENSAJES = {
    403: ("Acceso limitado", "Tu cuenta no tiene permiso para acceder a esta sección o realizar esta acción.",
          "Si necesitas acceder, solicita al administrador que revise tus roles y permisos."),
    404: ("Enlace no disponible", "La página o el registro que buscas no existe o ya no está disponible.",
          "Comprueba el enlace o vuelve a una sección disponible para tu cuenta."),
    405: ("Acción no disponible", "Este enlace no permite realizar la acción de esta manera.",
          "Vuelve a la pantalla anterior y utiliza los botones del sistema."),
    500: ("No pudimos completar la solicitud", "Ocurrió un problema al procesar la solicitud.",
          "Vuelve al inicio. Si el problema continúa, informa al administrador."),
}


def pagina_error(request, codigo):
    inicio = reverse("dashboard")
    volver = inicio
    anterior = request.META.get("HTTP_REFERER", "")
    try:
        valida = url_has_allowed_host_and_scheme(anterior, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    except DisallowedHost:
        valida = False
    ruta_anterior = urlsplit(anterior).path if valida else ""
    if ruta_anterior and ruta_anterior != request.path:
        try:
            resolve(ruta_anterior)
        except Resolver404:
            pass
        else:
            volver = anterior
    titulo, mensaje, ayuda = MENSAJES[codigo]
    # Sin procesadores de contexto ni consultas a BD: también sirve cuando falla otra sección.
    html = get_template("core/acceso_limitado.html").render({
        "codigo": codigo, "titulo": titulo, "mensaje": mensaje, "ayuda": ayuda,
        "volver_url": volver, "inicio_url": inicio,
    })
    respuesta = HttpResponse(html, status=codigo)
    respuesta.pagina_error_amigable = True
    patch_cache_control(respuesta, no_store=True, private=True)
    return respuesta


def acceso_denegado(request, exception=None):
    return pagina_error(request, 403)


def enlace_no_disponible(request, exception=None):
    return pagina_error(request, 404)


def error_servidor(request):
    return pagina_error(request, 500)


class PaginasAccesoMiddleware:
    """Unificar respuestas HTML sin cambiar permisos, códigos HTTP ni respuestas JSON."""
    def __init__(self, get_response):
        self.get_response = get_response

    def process_view(self, request, view_func, view_args, view_kwargs):
        coincidencia = request.resolver_match
        if (coincidencia and "admin" in coincidencia.namespaces
                and request.user.is_authenticated and not admin.site.has_permission(request)):
            return pagina_error(request, 403)

    def __call__(self, request):
        respuesta = self.get_response(request)
        if respuesta.status_code not in (403, 404, 405) or getattr(respuesta, "pagina_error_amigable", False):
            return respuesta
        acepta = request.headers.get("Accept", "")
        if (respuesta.streaming or "text/html" not in respuesta.get("Content-Type", "")
                or ("application/json" in acepta and "text/html" not in acepta)):
            return respuesta
        nueva = pagina_error(request, respuesta.status_code)
        respuesta.content = nueva.content
        respuesta["Content-Type"] = nueva["Content-Type"]
        if respuesta.has_header("Content-Length"):
            del respuesta["Content-Length"]
        patch_cache_control(respuesta, no_store=True, private=True)
        return respuesta
