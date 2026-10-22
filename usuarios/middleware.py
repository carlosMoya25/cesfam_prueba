from django.shortcuts import redirect
from django.utils.deprecation import MiddlewareMixin


class CambioClaveObligatorioMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        usuario = request.user
        if usuario.is_authenticated and usuario.debe_cambiar_clave:
            permitidas = {"usuarios:cambiar_clave", "logout", "account_logout"}
            if request.resolver_match.view_name not in permitidas:
                return redirect("usuarios:cambiar_clave")
