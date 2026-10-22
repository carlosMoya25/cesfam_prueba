def tiene_rol(usuario, rol):
    if not usuario.is_authenticated:
        return False

    return usuario.groups.filter(name=rol).exists()


def es_tecnico(usuario):
    return tiene_rol(usuario, "Técnico TI")


def es_administrador(usuario):
    return tiene_rol(usuario, "Administrador") or usuario.is_superuser

def es_encargado_inventario(usuario):
    return tiene_rol(usuario, "Encargado de Inventario")


def puede_gestionar_inventario(usuario):
    return es_encargado_inventario(usuario) or es_administrador(usuario)


def es_encargado_farmacia(usuario):
    return tiene_rol(usuario, "Encargado de Farmacia")


def puede_gestionar_farmacia(usuario):
    return (
        usuario.is_authenticated
        and usuario.is_active
        and (es_administrador(usuario) or es_encargado_farmacia(usuario))
    )
