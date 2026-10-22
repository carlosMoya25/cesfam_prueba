from django.urls import path

from . import views, clave_views


app_name = "usuarios"


urlpatterns = [
    path("mi-cuenta/clave/", clave_views.cambiar_clave, name="cambiar_clave"),
    path("administracion/usuarios/<int:pk>/clave/", clave_views.restablecer_clave, name="restablecer_clave"),

    # ======================================================
    # ADMINISTRACIÓN
    # ======================================================

    path(
        "administracion/",
        views.panel_administracion,
        name="administracion",
    ),

    path(
        "administracion/usuarios/",
        views.lista_usuarios,
        name="lista_usuarios",
    ),

    path(
        "administracion/usuarios/nuevo/",
        views.crear_usuario,
        name="crear_usuario",
    ),

    path(
        "administracion/usuarios/<int:pk>/",
        views.detalle_usuario,
        name="detalle_usuario",
    ),

    path(
        "administracion/usuarios/<int:pk>/editar/",
        views.editar_usuario,
        name="editar_usuario",
    ),

    path(
        "administracion/usuarios/<int:pk>/estado/",
        views.cambiar_estado_usuario,
        name="cambiar_estado_usuario",
    ),


    # ======================================================
    # SEGURIDAD
    # ======================================================

    path(
        "seguridad/",
        views.panel_seguridad,
        name="seguridad",
    ),

    path(
        "seguridad/desbloquear/<int:pk>/",
        views.desbloquear_usuario,
        name="desbloquear_usuario",
    ),

    path(
        "administracion/historial/",
        views.historial_administracion,
        name="historial_administracion",
    ),

]