from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from inventario.models import CategoriaArticulo
from .models import HistorialSeguridad

Usuario = get_user_model()
FUERTE = "Ladera!Bosque-67-Acceso"
NUEVA = "Horizonte!Ventana-89"
DEBIL = "1234"


class ClavesUsuarioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_superuser(email="admin-claves@example.com", password=FUERTE)
        cls.rol, _ = Group.objects.get_or_create(name="Funcionario")
        cls.usuario = Usuario.objects.create_user(email="funcionario-claves@example.com", password=FUERTE,
                                                  nombres="Andrea", apellido_paterno="Rojas")

    def setUp(self):
        self.client.force_login(self.admin)

    def datos_alta(self, **cambios):
        return {"email": "nuevo-claves@example.com", "nombres": "Juan", "apellido_paterno": "Pérez",
                "roles": [self.rol.pk], "is_active": "on", "modo_acceso": "clave",
                "clave1": FUERTE, "clave2": FUERTE} | cambios

    def test_alta_con_clave_fuerte_hash_y_cambio_obligatorio(self):
        response = self.client.post(reverse("usuarios:crear_usuario"), self.datos_alta())
        self.assertEqual(response.status_code, 302)
        usuario = Usuario.objects.get(email="nuevo-claves@example.com")
        self.assertTrue(usuario.check_password(FUERTE))
        self.assertNotEqual(usuario.password, FUERTE)
        self.assertTrue(usuario.debe_cambiar_clave)
        self.assertEqual(list(usuario.groups.all()), [self.rol])
        self.assertTrue(HistorialSeguridad.objects.filter(usuario_afectado=usuario).exists())

    def test_alta_google_conserva_clave_inutilizable(self):
        response = self.client.post(reverse("usuarios:crear_usuario"), self.datos_alta(modo_acceso="google", clave1="", clave2=""))
        self.assertEqual(response.status_code, 302)
        usuario = Usuario.objects.get(email="nuevo-claves@example.com")
        self.assertFalse(usuario.has_usable_password())
        self.assertFalse(usuario.debe_cambiar_clave)

    def test_debil_requiere_revision_y_confirmacion_sin_mostrar_clave(self):
        url = reverse("usuarios:crear_usuario")
        datos = self.datos_alta(clave1=DEBIL, clave2=DEBIL)
        response = self.client.post(url, datos)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Usuario.objects.filter(email=datos["email"]).exists())
        form = response.context["form"]
        self.assertTrue(form.advertencias_clave)
        self.assertTrue(form.token_autorizacion)
        self.assertNotContains(response, 'value="1234"')
        # Marcar la casilla en el primer envío no sustituye revisar los avisos.
        self.client.post(url, datos | {"autorizar_clave_debil": "on"})
        self.assertFalse(Usuario.objects.filter(email=datos["email"]).exists())
        response = self.client.post(url, datos | {"autorizar_clave_debil": "on",
                                                 "autorizacion_clave": form.token_autorizacion})
        self.assertEqual(response.status_code, 302)
        usuario = Usuario.objects.get(email=datos["email"])
        self.assertTrue(usuario.check_password(DEBIL))
        self.assertTrue(usuario.debe_cambiar_clave)
        evento = HistorialSeguridad.objects.get(usuario_afectado=usuario)
        self.assertIn("autorizó explícitamente", evento.descripcion)
        self.assertNotIn(DEBIL, evento.descripcion)
        self.assertEqual(evento.realizado_por, self.admin)

    def test_token_no_sirve_para_otra_clave_usuario_o_administrador(self):
        url = reverse("usuarios:crear_usuario")
        datos = self.datos_alta(clave1=DEBIL, clave2=DEBIL)
        response = self.client.post(url, datos)
        token = response.context["form"].token_autorizacion
        for cambios in ({"clave1": "12345", "clave2": "12345"}, {"email": "otro-nuevo@example.com"}):
            response = self.client.post(url, datos | {"autorizar_clave_debil": "on", "autorizacion_clave": token} | cambios)
            self.assertEqual(response.status_code, 200)
        otro = Usuario.objects.create_superuser(email="otro-admin@example.com", password=FUERTE)
        self.client.force_login(otro)
        self.assertEqual(self.client.post(url, datos | {"autorizar_clave_debil": "on", "autorizacion_clave": token}).status_code, 200)
        self.assertFalse(Usuario.objects.filter(email=datos["email"]).exists())

    def test_no_autoriza_clave_vacia_o_confirmacion_distinta(self):
        url = reverse("usuarios:crear_usuario")
        for cambios in ({"clave1": "", "clave2": ""}, {"clave2": "Diferente!"}):
            response = self.client.post(url, self.datos_alta(**cambios, autorizar_clave_debil="on"))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertFalse(Usuario.objects.filter(email="nuevo-claves@example.com").exists())

    def test_no_administrador_no_crea_ni_restablece(self):
        self.client.force_login(self.usuario)
        self.client.post(reverse("usuarios:crear_usuario"), self.datos_alta())
        self.assertFalse(Usuario.objects.filter(email="nuevo-claves@example.com").exists())
        url = reverse("usuarios:restablecer_clave", args=[self.admin.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"clave1": FUERTE, "clave2": FUERTE}).status_code, 403)

    def test_forzado_bloquea_modulos_post_y_rutas_alternativas(self):
        self.usuario.debe_cambiar_clave = True
        self.usuario.save()
        self.client.force_login(self.usuario)
        destino = reverse("usuarios:cambiar_clave")
        for url in (reverse("dashboard"), reverse("inventario:panel"), "/accounts/password/change/", "/admin/"):
            response = self.client.get(url)
            self.assertRedirects(response, destino, fetch_redirect_response=False)
        self.client.post(reverse("inventario:crear_categoria"), {"nombre": "No crear"})
        self.assertFalse(CategoriaArticulo.objects.exists())
        self.assertContains(self.client.get(destino), "Debe cambiar su contraseña temporal")
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)

    def test_login_con_clave_temporal_exige_cambio(self):
        self.usuario.set_password(DEBIL)
        self.usuario.debe_cambiar_clave = True
        self.usuario.save()
        self.client.logout()
        response = self.client.post(reverse("login"), {"username": self.usuario.email, "password": DEBIL}, follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("usuarios:cambiar_clave"))

    def test_cambio_definitivo_valida_actual_fortaleza_y_diferencia(self):
        self.usuario.debe_cambiar_clave = True
        self.usuario.save()
        self.client.force_login(self.usuario)
        url = reverse("usuarios:cambiar_clave")
        for datos in (
            {"old_password": "Incorrecta", "new_password1": NUEVA, "new_password2": NUEVA},
            {"old_password": FUERTE, "new_password1": DEBIL, "new_password2": DEBIL, "autorizar_clave_debil": "on"},
            {"old_password": FUERTE, "new_password1": FUERTE, "new_password2": FUERTE},
        ):
            response = self.client.post(url, datos)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            self.usuario.refresh_from_db()
            self.assertTrue(self.usuario.debe_cambiar_clave)
            self.assertTrue(self.usuario.check_password(FUERTE))
        response = self.client.post(url, {"old_password": FUERTE, "new_password1": NUEVA, "new_password2": NUEVA})
        self.assertRedirects(response, reverse("dashboard"))
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(NUEVA))
        self.assertFalse(self.usuario.debe_cambiar_clave)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.usuario.pk)

    def test_restablecimiento_debil_y_revocacion_de_sesion_anterior(self):
        from django.test import Client
        anterior = Client()
        anterior.force_login(self.usuario)
        url = reverse("usuarios:restablecer_clave", args=[self.usuario.pk])
        datos = {"clave1": DEBIL, "clave2": DEBIL}
        response = self.client.post(url, datos)
        token = response.context["form"].token_autorizacion
        response = self.client.post(url, datos | {"autorizar_clave_debil": "on", "autorizacion_clave": token})
        self.assertEqual(response.status_code, 302)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(DEBIL))
        self.assertTrue(self.usuario.debe_cambiar_clave)
        self.assertNotIn("_auth_user_id", anterior.get(reverse("dashboard")).wsgi_request.session)

    def test_editar_usuario_no_modifica_clave(self):
        url = reverse("usuarios:editar_usuario", args=[self.usuario.pk])
        response = self.client.get(url)
        self.assertNotIn("clave1", response.context["form"].fields)
        self.client.post(url, self.datos_alta(email=self.usuario.email, clave1=DEBIL, clave2=DEBIL))
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(FUERTE))
        self.assertFalse(self.usuario.debe_cambiar_clave)

    def test_usuarios_google_sin_clave_reciben_indicacion_para_habilitarla(self):
        self.usuario.set_unusable_password()
        self.usuario.save()
        self.client.force_login(self.usuario)
        response = self.client.get(reverse("usuarios:cambiar_clave"))
        self.assertContains(response, "Solicite al administrador")
        self.assertIsNone(response.context["form"])

    def test_autorizacion_vencida_o_sin_casilla_no_habilita_clave_debil(self):
        from unittest.mock import patch
        from django.core import signing
        url = reverse("usuarios:crear_usuario")
        datos = self.datos_alta(clave1=DEBIL, clave2=DEBIL)
        response = self.client.post(url, datos)
        token = response.context["form"].token_autorizacion
        response = self.client.post(url, datos | {"autorizacion_clave": token})
        self.assertEqual(response.status_code, 200)
        import time
        huella = signing.loads(token, salt="usuarios.autorizacion-clave")
        with patch("django.core.signing.time.time", return_value=time.time() - 601):
            vencido = signing.dumps(huella, salt="usuarios.autorizacion-clave")
        response = self.client.post(url, datos | {"autorizacion_clave": vencido, "autorizar_clave_debil": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Usuario.objects.filter(email=datos["email"]).exists())

    def test_csrf_en_creacion_cambio_y_restablecimiento(self):
        from django.test import Client
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.admin)
        for url, datos in (
            (reverse("usuarios:crear_usuario"), self.datos_alta()),
            (reverse("usuarios:cambiar_clave"), {"old_password": FUERTE, "new_password1": NUEVA, "new_password2": NUEVA}),
            (reverse("usuarios:restablecer_clave", args=[self.usuario.pk]), {"clave1": DEBIL, "clave2": DEBIL}),
        ):
            self.assertEqual(cliente.post(url, datos).status_code, 403)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(FUERTE))

    def test_reset_habilita_clave_local_para_cuenta_google_sin_cambiar_roles(self):
        self.usuario.set_unusable_password()
        self.usuario.groups.set([self.rol])
        self.usuario.save()
        response = self.client.post(reverse("usuarios:restablecer_clave", args=[self.usuario.pk]),
                                    {"clave1": FUERTE, "clave2": FUERTE})
        self.assertEqual(response.status_code, 302)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.has_usable_password())
        self.assertTrue(self.usuario.debe_cambiar_clave)
        self.assertEqual(list(self.usuario.groups.all()), [self.rol])
