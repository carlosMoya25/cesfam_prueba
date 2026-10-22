from .utils import puede_gestionar_farmacia, puede_gestionar_inventario


def permisos_modulos(request):
    return {
        "puede_gestionar_farmacia": puede_gestionar_farmacia(request.user),
        "puede_gestionar_inventario": puede_gestionar_inventario(request.user),
    }
