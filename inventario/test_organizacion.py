from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from organizacion.models import Anexo, Departamento
from .forms import EntradaInventarioForm


class OrganizacionInventarioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.encargado = get_user_model().objects.create_user(email="organizacion-inventario@example.com")
        grupo, _ = Group.objects.get_or_create(name="Encargado de Inventario")
        cls.encargado.groups.add(grupo)
        cls.funcionario = get_user_model().objects.create_user(email="sin-acceso-organizacion@example.com")

    def setUp(self):
        self.client.force_login(self.encargado)

    def test_botones_y_formularios_accesibles(self):
        response = self.client.get(reverse("inventario:panel"))
        for nombre in ("crear_departamento", "crear_ubicacion"):
            self.assertContains(response, reverse(f"inventario:{nombre}"))
            self.assertEqual(self.client.get(reverse(f"inventario:{nombre}")).status_code, 200)

    def test_crea_departamento_y_ubicacion_activos(self):
        response = self.client.post(reverse("inventario:crear_departamento"), {
            "nombre": "  Enfermería  ", "descripcion": "Atención clínica",
        })
        self.assertRedirects(response, reverse("inventario:panel"))
        self.assertTrue(Departamento.objects.get(nombre="Enfermería").activo)
        response = self.client.post(reverse("inventario:crear_ubicacion"), {
            "nombre": "Bodega Norte", "tipo": "ANEXO", "direccion": "Calle 10", "telefono": "123456",
        })
        self.assertRedirects(response, reverse("inventario:panel"))
        ubicacion = Anexo.objects.get(nombre="Bodega Norte")
        self.assertTrue(ubicacion.activo)
        self.assertIn(ubicacion, EntradaInventarioForm().fields["anexo"].queryset)

    def test_rechaza_duplicados_y_tipo_invalido(self):
        ubicaciones_previas = Anexo.objects.count()
        Departamento.objects.create(nombre="Enfermería", activo=False)
        Anexo.objects.create(nombre="Bodega Norte")
        for nombre, datos in (
            ("crear_departamento", {"nombre": " enfermería "}),
            ("crear_ubicacion", {"nombre": "BODEGA NORTE", "tipo": "ANEXO"}),
            ("crear_ubicacion", {"nombre": "Otra", "tipo": "INVENTADO"}),
        ):
            response = self.client.post(reverse(f"inventario:{nombre}"), datos)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertEqual(Departamento.objects.count(), 1)
        self.assertEqual(Anexo.objects.count(), ubicaciones_previas + 1)

    def test_protege_consulta_y_creacion_segun_rol(self):
        self.client.force_login(self.funcionario)
        for nombre in ("crear_departamento", "crear_ubicacion"):
            url = reverse(f"inventario:{nombre}")
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {"nombre": "No autorizado", "tipo": "ANEXO"}).status_code, 403)
            self.client.logout()
            self.assertEqual(self.client.get(url).status_code, 302)
            self.client.force_login(self.funcionario)
        self.assertFalse(Departamento.objects.exists())
        self.assertFalse(Anexo.objects.filter(nombre="No autorizado").exists())
