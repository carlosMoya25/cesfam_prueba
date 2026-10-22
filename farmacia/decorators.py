from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden

from usuarios.utils import puede_gestionar_farmacia


def acceso_farmacia(view):
    @login_required
    @wraps(view)
    def protegida(request, *args, **kwargs):
        if not puede_gestionar_farmacia(request.user):
            return HttpResponseForbidden("No tienes permisos para gestionar Farmacia.")
        return view(request, *args, **kwargs)

    return protegida
