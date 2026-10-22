from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden, JsonResponse
from django.test import TestCase, override_settings
from django.urls import path, reverse
from django.views.decorators.http import require_POST

from .errores import acceso_denegado, enlace_no_disponible, error_servidor


def prohibido(request):
    return HttpResponseForbidden("Detalle interno que no debe mostrarse")


def excepcion_permiso(request):
    raise PermissionDenied("Información interna de permisos")


def prohibido_json(request):
    return JsonResponse({"error": "sin_permiso"}, status=403)


@require_POST
def solo_post(request):
    return JsonResponse({"ok": True})


urlpatterns = [path("login/", prohibido, name="login"), path("prohibido/", prohibido), path("permiso/", excepcion_permiso),
               path("json/", prohibido_json), path("solo-post/", solo_post), path("", solo_post, name="dashboard")]
handler403 = acceso_denegado
handler404 = enlace_no_disponible
handler500 = error_servidor


class ErroresAccesoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_user(email="acceso-limitado@example.com")
        cls.admin = get_user_model().objects.create_superuser(email="admin-acceso@example.com")

    def setUp(self):
        self.client.force_login(self.usuario)

    def test_enlaces_protegidos_de_varios_modulos_muestran_pagina_sin_cambiar_permisos(self):
        for ruta in ("farmacia:dashboard", "inventario:panel", "inventario:crear_categoria",
                     "usuarios:administracion", "usuarios:lista_usuarios", "usuarios:crear_usuario", "panel_tecnico"):
            with self.subTest(ruta=ruta):
                response = self.client.get(reverse(ruta))
                self.assertContains(response, "Acceso limitado", status_code=403)
                self.assertContains(response, "Volver atrás", status_code=403)
                self.assertContains(response, "Ir al inicio", status_code=403)
                self.assertNotContains(response, "Traceback", status_code=403)

    def test_usuario_sin_staff_no_recibe_formulario_de_admin_al_forzar_el_enlace(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Acceso limitado", status_code=403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_recurso_inexistente_y_url_invalida_muestran_404_incluso_con_debug(self):
        self.client.force_login(self.admin)
        for debug in (True, False):
            with override_settings(DEBUG=debug):
                for url in ("/esta-ruta-no-existe/", reverse("farmacia:detalle_medicamento", args=[999999])):
                    response = self.client.get(url)
                    self.assertContains(response, "Enlace no disponible", status_code=404)
                    self.assertNotContains(response, "Using the URLconf", status_code=404)
                    self.assertContains(response, "Volver atrás", status_code=404)

    def test_volver_atras_acepta_solo_paginas_internas_y_evade_bucle(self):
        url = reverse("farmacia:dashboard")
        response = self.client.get(url, HTTP_REFERER="http://testserver/inventario/?buscar=mouse")
        self.assertContains(response, 'href="http://testserver/inventario/?buscar=mouse"', status_code=403)
        for anterior in ("https://externo.example/", "javascript:alert(1)", "//externo.example/", "http://testserver/farmacia/", "http://testserver/otra-ruta-inexistente/", "#ancla", ""):
            response = self.client.get(url, HTTP_REFERER=anterior)
            self.assertContains(response, 'href="/"', status_code=403)
            self.assertNotContains(response, "externo.example", status_code=403)
        response = self.client.get(url, secure=True, HTTP_REFERER="http://testserver/inventario/")
        self.assertNotContains(response, 'href="http://testserver/inventario/"', status_code=403)

    def test_usuario_sin_sesion_conserva_redireccion_a_login(self):
        self.client.logout()
        response = self.client.get(reverse("farmacia:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("login")))

    @override_settings(ROOT_URLCONF="core.test_errores")
    def test_respuestas_forbidden_y_excepciones_no_exponen_detalles(self):
        for debug in (True, False):
            with override_settings(DEBUG=debug):
                for url in ("/prohibido/", "/permiso/"):
                    response = self.client.get(url)
                    self.assertContains(response, "Acceso limitado", status_code=403)
                    self.assertNotContains(response, "Información interna", status_code=403)
                    self.assertNotContains(response, "Detalle interno", status_code=403)
                    self.assertIn("no-store", response["Cache-Control"])

    @override_settings(ROOT_URLCONF="core.test_errores")
    def test_json_se_conserva_y_405_mantiene_metodos_permitidos(self):
        response = self.client.get("/json/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "sin_permiso"})
        response = self.client.get("/solo-post/")
        self.assertContains(response, "Acción no disponible", status_code=405)
        self.assertEqual(response["Allow"], "POST")

    @override_settings(ROOT_URLCONF="core.test_errores")
    def test_pagina_500_no_necesita_base_de_datos_ni_muestra_excepcion(self):
        from django.test import RequestFactory
        request = RequestFactory().get("/fallo/")
        with self.assertNumQueries(0):
            response = error_servidor(request)
        self.assertEqual(response.status_code, 500)
        self.assertIn("No pudimos completar".encode(), response.content)

    def test_post_sin_permiso_muestra_aviso_y_no_crea_registros(self):
        from inventario.models import CategoriaArticulo
        response = self.client.post(reverse("inventario:crear_categoria"), {"nombre": "NO-DEBE-GUARDARSE"})
        self.assertContains(response, "Acceso limitado", status_code=403)
        self.assertFalse(CategoriaArticulo.objects.filter(nombre="NO-DEBE-GUARDARSE").exists())
