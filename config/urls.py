from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),

    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html"
        ),
        name="login",
    ),

    path(
        "logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),

    path("", include("core.urls")),

    path(
        "requerimientos/",
        include("requerimientos.urls"),
    ),

    path(
        "inventario/",
        include("inventario.urls"),
    ),

    path(
        "usuarios/",
    include("usuarios.urls")
    ),

    path(
        "farmacia/",
        include("farmacia.urls"),
    ),


]

handler403 = "core.errores.acceso_denegado"
handler404 = "core.errores.enlace_no_disponible"
handler500 = "core.errores.error_servidor"
