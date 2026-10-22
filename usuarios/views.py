from config import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q, Sum, Max
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import HistorialAdministracion

# pyrefly: ignore [missing-import]
from .forms import UsuarioAdminForm
from .claves import registrar_evento_clave
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods


from .models import HistorialSeguridad

from axes.models import AccessAttempt
from axes.utils import reset

from .utils import es_administrador


Usuario = get_user_model()


def panel_seguridad(request):
    """
    Panel de seguridad de acceso.

    Solo puede ser utilizado por administradores.
    Muestra usuarios que poseen intentos fallidos registrados
    por django-axes.
    """

    if not es_administrador(request.user):
        return HttpResponseForbidden(
            "No tienes permisos para acceder al panel de seguridad."
        )

    # Intentos registrados por Axes agrupados por username.
    intentos_axes = (
        AccessAttempt.objects
        .values("username")
        .annotate(
            intentos=Sum("failures_since_start"),
            ultimo_intento=Max("attempt_time"),
        )
        .order_by("-ultimo_intento")
    )

    # Convertimos los registros en información útil para la plantilla.
    incidencias = []

    for intento in intentos_axes:

        username = intento["username"]
        cantidad = intento["intentos"] or 0

        if not username:
            continue

        usuario = Usuario.objects.filter(
            email__iexact=username
        ).first()

        incidencias.append({
            "usuario": usuario,
            "username": username,
            "intentos": cantidad,
            "ultimo_intento": intento["ultimo_intento"],

            # Nuestro límite actual de Axes es 5.
            "bloqueado": cantidad >= settings.AXES_FAILURE_LIMIT,
        })

    historial = (
        HistorialSeguridad.objects.select_related("usuario_afectado", "realizado_por").all()[:20]
    )


    contexto = {
        "incidencias": incidencias,
        "total_incidencias": len(incidencias),
        "total_bloqueados": sum(
            1 for item in incidencias if item["bloqueado"]
        ),
        "historial": historial,
    }

    return render(
        request,
        "usuarios/seguridad.html",
        contexto
    )


@require_POST
def desbloquear_usuario(request, pk):

    if not es_administrador(request.user):
        return HttpResponseForbidden(
            "No tienes permisos para realizar esta acción."
        )

    usuario = get_object_or_404(
        Usuario,
        pk=pk
    )

    # ---------------------------------------------------------
    # Determinar si estaba bloqueado o solamente tenía intentos
    # ---------------------------------------------------------

    intento = (
        AccessAttempt.objects
        .filter(username__iexact=usuario.email)
        .aggregate(
            total=Sum("failures_since_start")
        )
    )

    cantidad_intentos = intento["total"] or 0

    estaba_bloqueado = (
        cantidad_intentos >= settings.AXES_FAILURE_LIMIT
    )

    # ---------------------------------------------------------
    # Reiniciar Axes
    # ---------------------------------------------------------

    reset(username=usuario.email)

    # ---------------------------------------------------------
    # Registrar auditoría
    # ---------------------------------------------------------

    if estaba_bloqueado:

        tipo_accion = "DESBLOQUEO_USUARIO"

        descripcion = (
            f"Se desbloqueó manualmente al usuario "
            f"{usuario.email}, quien registraba "
            f"{cantidad_intentos} intentos fallidos."
        )

    else:

        tipo_accion = "REINICIO_INTENTOS"

        descripcion = (
            f"Se reiniciaron manualmente los intentos fallidos "
            f"del usuario {usuario.email}. "
            f"Intentos registrados antes del reinicio: "
            f"{cantidad_intentos}."
        )

    HistorialSeguridad.objects.create(
        tipo_accion=tipo_accion,
        usuario_afectado=usuario,
        email_afectado=usuario.email,
        realizado_por=request.user,
        descripcion=descripcion,
    )

    # ---------------------------------------------------------
    # Mensaje
    # ---------------------------------------------------------

    if estaba_bloqueado:

        messages.success(
            request,
            f"El usuario {usuario.email} fue desbloqueado correctamente."
        )

    else:

        messages.success(
            request,
            f"Los intentos fallidos de {usuario.email} fueron reiniciados."
        )

    return redirect("usuarios:seguridad")

# ==========================================================
# ADMINISTRACIÓN DEL SISTEMA
# ==========================================================


def panel_administracion(request):

    if not es_administrador(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    usuarios = Usuario.objects.all()

    contexto = {
        "total_usuarios": usuarios.count(),

        "usuarios_activos": usuarios.filter(
            is_active=True
        ).count(),

        "usuarios_inactivos": usuarios.filter(
            is_active=False
        ).count(),

        "total_administradores": usuarios.filter(
            groups__name="Administrador"
        ).distinct().count(),

        "ultimos_usuarios": (
            usuarios
            .prefetch_related("groups")
            .select_related("anexo", "departamento")
            .order_by("-date_joined")[:5]
        ),
    }

    return render(
        request,
        "usuarios/administracion/panel.html",
        contexto,
    )


# ==========================================================
# LISTADO DE USUARIOS
# ==========================================================


def lista_usuarios(request):

    if not es_administrador(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    usuarios = (
        Usuario.objects
        .select_related(
            "anexo",
            "departamento",
        )
        .prefetch_related(
            "groups",
        )
        .order_by(
            "apellido_paterno",
            "nombres",
        )
    )

    busqueda = request.GET.get(
        "q",
        "",
    ).strip()

    estado = request.GET.get(
        "estado",
        "",
    )

    rol = request.GET.get(
        "rol",
        "",
    )


    # BUSCADOR

    if busqueda:

        usuarios = usuarios.filter(

            Q(nombres__icontains=busqueda)

            | Q(apellido_paterno__icontains=busqueda)

            | Q(apellido_materno__icontains=busqueda)

            | Q(email__icontains=busqueda)

        )


    # FILTRO ESTADO

    if estado == "activo":

        usuarios = usuarios.filter(
            is_active=True
        )

    elif estado == "inactivo":

        usuarios = usuarios.filter(
            is_active=False
        )


    # FILTRO ROL

    if rol:

        usuarios = usuarios.filter(
            groups__name=rol
        ).distinct()


    from django.contrib.auth.models import Group

    roles = Group.objects.order_by("name")


    contexto = {

        "usuarios": usuarios,

        "roles": roles,

        "busqueda": busqueda,

        "estado_actual": estado,

        "rol_actual": rol,

    }

    return render(
        request,
        "usuarios/administracion/lista.html",
        contexto,
    )


# ==========================================================
# CREAR USUARIO
# ==========================================================




    if not es_administrador(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    if request.method == "POST":

        form = UsuarioAdminForm(
            request.POST
        )

        if form.is_valid():

            usuario = form.save()

            messages.success(
                request,
                f"El usuario {usuario.nombres} "
                f"{usuario.apellido_paterno} fue creado correctamente."
            )

            return redirect(
                "usuarios:lista_usuarios"
            )

    else:

        form = UsuarioAdminForm()


    return render(
        request,
        "usuarios/administracion/formulario.html",
        {
            "form": form,
            "titulo": "Nuevo usuario",
            "subtitulo": (
                "Registra un usuario autorizado "
                "para acceder a SIGRI."
            ),
            "texto_boton": "Crear usuario",
        },
    )

# ==========================================================
# CREAR USUARIO
# ==========================================================

@sensitive_post_parameters()
@never_cache
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def crear_usuario(request):

    if not es_administrador(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    if request.method == "POST":

        form = UsuarioAdminForm(request.POST, administrador=request.user)

        if form.is_valid():

            usuario = form.save()
            if usuario.has_usable_password():
                registrar_evento_clave(usuario=usuario, realizado_por=request.user,
                    accion="Se habilitó el acceso con contraseña temporal; el usuario deberá cambiarla al ingresar.",
                    advertencias=form.advertencias_clave)

            # --------------------------------------------------
            # HISTORIAL ADMINISTRATIVO
            # --------------------------------------------------

            roles = ", ".join(
                usuario.groups.values_list("name", flat=True)
            ) or "Sin roles"

            HistorialAdministracion.objects.create(
                accion="CREAR_USUARIO",
                administrador=request.user,
                usuario_afectado=usuario,
                descripcion=(
                    f"Se creó el usuario "
                    f"{usuario.nombres} {usuario.apellido_paterno} "
                    f"({usuario.email}). "
                    f"Roles asignados: {roles}."
                ),
            )

            messages.success(
                request,
                f"El usuario {usuario.nombres} "
                f"{usuario.apellido_paterno} fue creado correctamente."
            )

            return redirect(
                "usuarios:lista_usuarios"
            )

    else:

        form = UsuarioAdminForm()

    return render(
        request,
        "usuarios/administracion/formulario.html",
        {
            "form": form,
            "titulo": "Nuevo usuario",
            "subtitulo": (
                "Registra un usuario autorizado "
                "para acceder a SIGRI."
            ),
            "texto_boton": "Crear usuario",
        },
    )

# ==========================================================
# DETALLE USUARIO
# ==========================================================


def detalle_usuario(request, pk):

    if not es_administrador(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    usuario = get_object_or_404(
        Usuario.objects
        .select_related(
            "anexo",
            "departamento",
        )
        .prefetch_related(
            "groups",
        ),
        pk=pk,
    )

    return render(
        request,
        "usuarios/administracion/detalle.html",
        {
            "usuario_detalle": usuario,
        },
    )


# ==========================================================
# EDITAR USUARIO
# ==========================================================

# ==========================================================
# EDITAR USUARIO
# ==========================================================

def editar_usuario(request, pk):

    if not es_administrador(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    usuario = get_object_or_404(
        Usuario,
        pk=pk,
    )

    if request.method == "POST":

        # --------------------------------------------------
        # GUARDAR ROLES ANTERIORES
        # --------------------------------------------------

        roles_anteriores = set(
            usuario.groups.values_list(
                "name",
                flat=True,
            )
        )

        form = UsuarioAdminForm(
            request.POST,
            instance=usuario,
        )

        if form.is_valid():

            # Guardamos qué campos fueron modificados
            campos_modificados = list(
                form.changed_data
            )

            usuario = form.save()

            # --------------------------------------------------
            # ROLES NUEVOS
            # --------------------------------------------------

            roles_nuevos = set(
                usuario.groups.values_list(
                    "name",
                    flat=True,
                )
            )

            # --------------------------------------------------
            # REGISTRAR CAMBIO DE ROLES
            # --------------------------------------------------

            if roles_anteriores != roles_nuevos:

                antes = ", ".join(
                    sorted(roles_anteriores)
                ) or "Sin roles"

                despues = ", ".join(
                    sorted(roles_nuevos)
                ) or "Sin roles"

                HistorialAdministracion.objects.create(
                    accion="CAMBIAR_ROLES",
                    administrador=request.user,
                    usuario_afectado=usuario,
                    descripcion=(
                        f"Se modificaron los roles del usuario "
                        f"{usuario.nombres} {usuario.apellido_paterno}. "
                        f"Roles anteriores: [{antes}]. "
                        f"Roles nuevos: [{despues}]."
                    ),
                )

            # --------------------------------------------------
            # REGISTRAR CAMBIO DE DATOS
            # --------------------------------------------------

            campos_sin_roles = [
                campo
                for campo in campos_modificados
                if campo != "roles"
            ]

            if campos_sin_roles:

                nombres_campos = {
                    "nombres": "Nombres",
                    "apellido_paterno": "Apellido paterno",
                    "apellido_materno": "Apellido materno",
                    "email": "Correo electrónico",
                    "telefono": "Teléfono",
                    "anexo": "Anexo",
                    "departamento": "Departamento",
                    "is_active": "Estado de la cuenta",
                }

                campos_legibles = [
                    nombres_campos.get(campo, campo)
                    for campo in campos_sin_roles
                ]

                HistorialAdministracion.objects.create(
                    accion="EDITAR_USUARIO",
                    administrador=request.user,
                    usuario_afectado=usuario,
                    descripcion=(
                        f"Se modificaron datos del usuario "
                        f"{usuario.nombres} {usuario.apellido_paterno}. "
                        f"Campos modificados: "
                        f"{', '.join(campos_legibles)}."
                    ),
                )

            messages.success(
                request,
                f"Los datos de {usuario.nombres} "
                f"{usuario.apellido_paterno} fueron actualizados."
            )

            return redirect(
                "usuarios:detalle_usuario",
                pk=usuario.pk,
            )

    else:

        form = UsuarioAdminForm(
            instance=usuario
        )

    return render(
        request,
        "usuarios/administracion/formulario.html",
        {
            "form": form,
            "titulo": "Editar usuario",
            "subtitulo": (
                "Modifica la información, roles "
                "y permisos de acceso."
            ),
            "texto_boton": "Guardar cambios",
            "usuario_editado": usuario,
        },
    )

# ==========================================================
# ACTIVAR / DESACTIVAR
# ==========================================================


# ==========================================================
# ACTIVAR / DESACTIVAR USUARIO
# ==========================================================

@require_POST
def cambiar_estado_usuario(request, pk):

    if not es_administrador(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    usuario = get_object_or_404(
        Usuario,
        pk=pk,
    )

    # ------------------------------------------------------
    # EVITAR QUE UN ADMINISTRADOR SE DESACTIVE A SÍ MISMO
    # ------------------------------------------------------

    if usuario.pk == request.user.pk:

        messages.error(
            request,
            "No puedes desactivar tu propia cuenta."
        )

        return redirect(
            "usuarios:detalle_usuario",
            pk=usuario.pk,
        )

    # ------------------------------------------------------
    # CAMBIAR ESTADO
    # ------------------------------------------------------

    usuario.is_active = not usuario.is_active

    usuario.save(
        update_fields=["is_active"]
    )

    # ------------------------------------------------------
    # REGISTRAR EN HISTORIAL ADMINISTRATIVO
    # ------------------------------------------------------

    if usuario.is_active:

        HistorialAdministracion.objects.create(
            accion="ACTIVAR_USUARIO",
            administrador=request.user,
            usuario_afectado=usuario,
            descripcion=(
                f"Se activó la cuenta del usuario "
                f"{usuario.nombres} {usuario.apellido_paterno} "
                f"({usuario.email})."
            ),
        )

        messages.success(
            request,
            f"El usuario {usuario.email} fue activado."
        )

    else:

        HistorialAdministracion.objects.create(
            accion="DESACTIVAR_USUARIO",
            administrador=request.user,
            usuario_afectado=usuario,
            descripcion=(
                f"Se desactivó la cuenta del usuario "
                f"{usuario.nombres} {usuario.apellido_paterno} "
                f"({usuario.email})."
            ),
        )

        messages.success(
            request,
            f"El usuario {usuario.email} fue desactivado."
        )

    return redirect(
        "usuarios:detalle_usuario",
        pk=usuario.pk,
    )

    if not es_administrador(request.user):
        return HttpResponseForbidden(
            "No tienes permisos para realizar esta acción."
        )

    usuario = get_object_or_404(
        Usuario,
        pk=pk,
    )


    # Evitar que un administrador se desactive a sí mismo.

    if usuario.pk == request.user.pk:

        messages.error(
            request,
            "No puedes desactivar tu propia cuenta."
        )

        return redirect(
            "usuarios:detalle_usuario",
            pk=usuario.pk,
        )


    usuario.is_active = not usuario.is_active

    usuario.save(
        update_fields=["is_active"]
    )


    if usuario.is_active:

        messages.success(
            request,
            f"El usuario {usuario.email} fue activado."
        )

    else:

        messages.success(
            request,
            f"El usuario {usuario.email} fue desactivado."
        )


    return redirect(
        "usuarios:detalle_usuario",
        pk=usuario.pk,
    )


# ==========================================================
# HISTORIAL ADMINISTRATIVO
# ==========================================================

def historial_administracion(request):

    if not es_administrador(request.user):
        return HttpResponseForbidden("No tienes permisos para acceder a esta sección.")

    historial = (
        HistorialAdministracion.objects
        .select_related(
            "administrador",
            "usuario_afectado",
        )
        .order_by("-fecha")[:100]
    )

    return render(
        request,
        "usuarios/administracion/historial.html",
        {
            "historial": historial,
        },
    )