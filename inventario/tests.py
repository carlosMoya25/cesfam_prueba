from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from farmacia.models import Medicamento, StockLote
from farmacia.services import registrar_entrada_medicamento
from organizacion.models import Anexo
from .forms import (
    EntradaInventarioForm, SalidaInventarioForm, TransferenciaInventarioForm,
    AjusteInventarioForm, RecursoRequerimientoForm,
)
from .models import Articulo, CategoriaArticulo, StockAnexo, MovimientoInventario
from .services import (
    registrar_entrada, registrar_salida, registrar_transferencia,
    registrar_ajuste, registrar_recurso_requerimiento,
)


class SeparacionFarmaciaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_superuser(
            email="admin@example.com", password="test-password",
            nombres="Admin", apellido_paterno="Pruebas",
        )
        cls.anexo = Anexo.objects.create(nombre="CESFAM")
        cls.destino = Anexo.objects.create(nombre="Anexo")
        categoria = CategoriaArticulo.objects.create(nombre="Catálogo compartido")
        cls.articulo = Articulo.objects.create(nombre="Mouse", categoria=categoria)
        cls.articulo_medicamento = Articulo.objects.create(
            nombre="Medicamento de prueba", categoria=categoria,
        )
        cls.medicamento = Medicamento.objects.create(
            articulo=cls.articulo_medicamento, principio_activo="Prueba",
            concentracion="10", forma_farmaceutica="Comprimido",
        )

    def test_formularios_rechazan_medicamentos_incluso_por_post(self):
        for clase in (EntradaInventarioForm, SalidaInventarioForm,
                      TransferenciaInventarioForm, AjusteInventarioForm,
                      RecursoRequerimientoForm):
            with self.subTest(formulario=clase.__name__):
                form = clase(data={"articulo": self.articulo_medicamento.pk})
                self.assertIn("articulo", form.errors)
                self.assertIn(self.articulo, form.fields["articulo"].queryset)

    def test_servicios_rechazan_medicamentos_sin_modificar_saldos(self):
        stock = StockAnexo.objects.create(
            articulo=self.articulo_medicamento, anexo=self.anexo, cantidad=20,
        )
        comunes = dict(usuario=self.usuario, articulo=self.articulo_medicamento, cantidad=2)
        operaciones = [
            (registrar_entrada, dict(anexo=self.anexo)),
            (registrar_salida, dict(anexo=self.anexo)),
            (registrar_transferencia, dict(anexo_origen=self.anexo, anexo_destino=self.destino)),
            (registrar_ajuste, dict(anexo=self.anexo, tipo_ajuste="positivo", motivo="Prueba")),
            (registrar_recurso_requerimiento, dict(requerimiento=SimpleNamespace())),
        ]
        for servicio, extras in operaciones:
            with self.subTest(servicio=servicio.__name__):
                with self.assertRaisesMessage(ValidationError, "Farmacia"):
                    servicio(**comunes, **extras)
        stock.refresh_from_db()
        self.assertEqual(stock.cantidad, 20)
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_articulos_comunes_siguen_permitiendo_entrada_y_salida(self):
        comunes = dict(usuario=self.usuario, articulo=self.articulo, anexo=self.anexo)
        registrar_entrada(**comunes, cantidad=10)
        registrar_salida(**comunes, cantidad=3)
        self.assertEqual(StockAnexo.objects.get(articulo=self.articulo).cantidad, 7)

    def entrada_farmacia(self, numero, cantidad, anexo=None):
        from farmacia.bodega import exigir_bodega_central
        from farmacia.services import registrar_traslado_bodega
        import uuid
        central = exigir_bodega_central()
        registrar_entrada_medicamento(
            medicamento=self.medicamento, anexo=central,
            numero_lote=numero, fecha_vencimiento=timezone.localdate() + timedelta(days=30),
            cantidad=cantidad, usuario=self.usuario,
        )
        if anexo and anexo.pk != central.pk:
            self.medicamento.unidad_medida = "comprimido"
            self.medicamento.save()
            registrar_traslado_bodega(lote=self.medicamento.lotes.get(numero_lote=numero), destino=anexo,
                cantidad=cantidad, usuario=self.usuario, documento_traslado="Prueba de distribución", solicitud_id=uuid.uuid4())

    def test_farmacia_solo_actualiza_existencias_por_lote(self):
        self.entrada_farmacia("LOTE-1", 10)
        self.entrada_farmacia("LOTE-1", 5)
        self.assertEqual(StockLote.objects.get().cantidad, 15)
        self.assertFalse(StockAnexo.objects.filter(articulo=self.articulo_medicamento).exists())

    def test_panel_separa_saldos_y_advierte_existencias_antiguas(self):
        self.entrada_farmacia("VIGENTE", 10)
        self.entrada_farmacia("VENCIDO", 4)
        self.entrada_farmacia("VENCIDO", 6, anexo=self.destino)
        lote = self.medicamento.lotes.get(numero_lote="VENCIDO")
        lote.fecha_vencimiento = timezone.localdate() - timedelta(days=1)
        lote.save()
        StockAnexo.objects.create(articulo=self.articulo_medicamento, anexo=self.anexo, cantidad=100)
        self.client.force_login(self.usuario)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_articulos"], 1)
        self.assertEqual(response.context["unidades_farmacia"], 10)
        self.assertEqual(response.context["lotes_vencidos"], 1)
        self.assertTrue(response.context["stock_medicamentos_sin_lote"])
        self.assertContains(response, "sin lote")
        panel = self.client.get(reverse("inventario:panel"))
        self.assertEqual(panel.status_code, 200)
        self.assertEqual(panel.context["total_unidades"], 0)
        self.assertEqual(panel.context["total_articulos"], 1)

    def test_edicion_general_no_permite_modificar_medicamentos(self):
        self.client.force_login(self.usuario)
        from inventario import views
        from django.test import RequestFactory
        request = RequestFactory().get("/")
        request.user = self.usuario
        from django.http import Http404
        with self.assertRaises(Http404):
            views.editar_articulo(request, self.articulo_medicamento.pk)


class UsabilidadInventarioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import Group
        from .models import TipoMovimiento
        cls.usuario = get_user_model().objects.create_user(email="inventario@example.com", nombres="Encargado", apellido_paterno="Prueba")
        grupo, _ = Group.objects.get_or_create(name="Encargado de Inventario")
        cls.usuario.groups.add(grupo)
        cls.funcionario = get_user_model().objects.create_user(email="funcionario@example.com")
        cls.origen = Anexo.objects.create(nombre="Norte")
        cls.destino = Anexo.objects.create(nombre="Sur")
        cls.categoria = CategoriaArticulo.objects.create(nombre="Equipos")
        cls.otra_categoria = CategoriaArticulo.objects.create(nombre="Insumos")
        cls.mouse = Articulo.objects.create(nombre="Mouse", codigo="MOU-01", categoria=cls.categoria, stock_minimo=5)
        cls.teclado = Articulo.objects.create(nombre="Teclado", codigo="TEC-01", categoria=cls.categoria, stock_minimo=5)
        cls.cable = Articulo.objects.create(nombre="Cable", codigo="CAB-01", categoria=cls.otra_categoria, stock_minimo=5)
        cls.inactivo = Articulo.objects.create(nombre="Retirado", categoria=cls.categoria, activo=False)
        StockAnexo.objects.create(articulo=cls.mouse, anexo=cls.origen, cantidad=10)
        StockAnexo.objects.create(articulo=cls.mouse, anexo=cls.destino, cantidad=2)
        StockAnexo.objects.create(articulo=cls.teclado, anexo=cls.origen, cantidad=0)
        StockAnexo.objects.create(articulo=cls.cable, anexo=cls.origen, cantidad=1)
        cls.tipo_entrada = TipoMovimiento.objects.get(nombre="Entrada")

    def setUp(self):
        self.client.force_login(self.usuario)

    def stock(self, **params):
        return self.client.get(reverse("inventario:consultar_stock"), params)

    def test_busqueda_por_codigo_con_ubicacion_y_categoria(self):
        response = self.stock(buscar="MOU-01", anexo=self.destino.pk, categoria=self.categoria.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_unidades"], 2)
        self.assertEqual(response.context["pagina"].paginator.count, 1)
        self.assertEqual(list(response.context["stocks"])[0].anexo_id, self.destino.pk)

    def test_stock_bajo_excluye_agotados_en_panel_consulta_y_modelo(self):
        for ruta in ("panel", "consultar_stock"):
            response = self.client.get(reverse(f"inventario:{ruta}"))
            self.assertEqual(response.context["total_stock_bajo"], 2)
            self.assertEqual(response.context["total_agotados"], 1)
        self.assertFalse(StockAnexo.objects.get(articulo=self.teclado).stock_bajo)
        self.assertEqual(self.stock(estado="bajo").context["total_registros"], 2)
        self.assertEqual(self.stock(estado="agotado").context["total_registros"], 1)

    def test_acciones_precargan_articulo_y_ubicacion(self):
        response = self.stock(buscar="MOU-01", anexo=self.origen.pk)
        self.assertContains(response, f'articulo={self.mouse.pk}&amp;anexo={self.origen.pk}')
        for ruta, campo in (("entrada", "anexo"), ("salida", "anexo"), ("transferencia", "anexo_origen")):
            with self.subTest(ruta=ruta):
                response = self.client.get(reverse(f"inventario:{ruta}"), {"articulo": self.mouse.pk, campo: self.origen.pk})
                self.assertEqual(str(response.context["form"]["articulo"].value()), str(self.mouse.pk))
                self.assertEqual(str(response.context["form"][campo].value()), str(self.origen.pk))
                self.assertContains(response, 'id="inv-saldo"')

    def test_salida_no_ofrece_articulos_sin_existencias_en_origen(self):
        response = self.client.get(reverse("inventario:salida"), {"anexo": self.origen.pk})
        articulos = response.context["form"].fields["articulo"].queryset
        self.assertIn(self.mouse, articulos)
        self.assertNotIn(self.teclado, articulos)
        self.assertNotIn(self.inactivo, articulos)

    def test_salida_excesiva_muestra_error_y_no_descuenta(self):
        response = self.client.post(reverse("inventario:salida"), {
            "anexo": self.origen.pk, "articulo": self.mouse.pk, "cantidad": 11, "motivo": "Entrega",
        })
        self.assertIn("cantidad", response.context["form"].errors)
        self.assertEqual(StockAnexo.objects.get(anexo=self.origen, articulo=self.mouse).cantidad, 10)
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_confirmacion_salida_muestra_saldo_real(self):
        response = self.client.post(reverse("inventario:salida"), {
            "anexo": self.origen.pk, "articulo": self.mouse.pk, "cantidad": 3, "motivo": "Entrega",
        }, follow=True)
        self.assertContains(response, "Se descontaron 3 unidades de Mouse en Norte. Saldo: 7.")
        self.assertEqual(StockAnexo.objects.get(anexo=self.origen, articulo=self.mouse).cantidad, 7)

    def test_transferencia_actualiza_ambos_saldos_y_muestra_confirmacion(self):
        response = self.client.post(reverse("inventario:transferencia"), {
            "anexo_origen": self.origen.pk, "anexo_destino": self.destino.pk,
            "articulo": self.mouse.pk, "cantidad": 4, "motivo": "Redistribución",
        }, follow=True)
        self.assertContains(response, "Quedan 6 en Norte y 6 en Sur.")
        self.assertEqual(StockAnexo.objects.get(anexo=self.origen, articulo=self.mouse).cantidad, 6)
        self.assertEqual(StockAnexo.objects.get(anexo=self.destino, articulo=self.mouse).cantidad, 6)

    def test_transferencia_a_misma_ubicacion_no_genera_movimiento(self):
        response = self.client.post(reverse("inventario:transferencia"), {
            "anexo_origen": self.origen.pk, "anexo_destino": self.origen.pk,
            "articulo": self.mouse.pk, "cantidad": 4, "motivo": "Prueba",
        })
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertFalse(MovimientoInventario.objects.exists())

    def test_ajuste_negativo_respeta_saldo_y_suma_funciona_sin_stock_previo(self):
        response = self.client.post(reverse("inventario:ajuste"), {
            "anexo": self.origen.pk, "articulo": self.mouse.pk, "cantidad": 20,
            "tipo_ajuste": "negativo", "motivo": "Conteo",
        })
        self.assertIn("cantidad", response.context["form"].errors)
        response = self.client.post(reverse("inventario:ajuste"), {
            "anexo": self.destino.pk, "articulo": self.cable.pk, "cantidad": 2,
            "tipo_ajuste": "positivo", "motivo": "Conteo",
        }, follow=True)
        self.assertContains(response, "Se agregaron 2 unidades de Cable en Sur. Saldo: 2.")

    def test_historial_filtra_nombre_codigo_origen_destino_tipo_y_responsable(self):
        entrada = registrar_entrada(usuario=self.usuario, articulo=self.mouse, anexo=self.origen, cantidad=1)
        registrar_entrada(usuario=self.usuario, articulo=self.cable, anexo=self.destino, cantidad=1)
        registrar_salida(usuario=self.usuario, articulo=self.mouse, anexo=self.origen, cantidad=1)
        params = {"buscar": "MOU-01", "anexo": self.origen.pk,
                  "tipo": self.tipo_entrada.pk, "responsable": self.usuario.pk}
        response = self.client.get(reverse("inventario:historial"), params)
        self.assertEqual([m.pk for m in response.context["movimientos"]], [entrada.pk])
        response = self.client.get(reverse("inventario:historial"), {"anexo": self.destino.pk})
        self.assertEqual(response.context["pagina"].paginator.count, 1)

    def test_historial_incluye_todo_el_dia_segun_zona_local(self):
        from datetime import datetime, time
        movimiento = registrar_entrada(usuario=self.usuario, articulo=self.mouse, anexo=self.origen, cantidad=1)
        hoy = timezone.localdate()
        fecha = timezone.make_aware(datetime.combine(hoy, time(23, 59, 59)))
        MovimientoInventario.objects.filter(pk=movimiento.pk).update(fecha=fecha)
        response = self.client.get(reverse("inventario:historial"), {"desde": hoy.isoformat(), "hasta": hoy.isoformat()})
        self.assertEqual(response.context["pagina"].paginator.count, 1)
        response = self.client.get(reverse("inventario:historial"), {"desde": hoy.isoformat(), "hasta": (hoy - timedelta(days=1)).isoformat()})
        self.assertFalse(response.context["filtros_form"].is_valid())
        self.assertEqual(response.context["pagina"].paginator.count, 0)

    def test_catalogo_busqueda_estado_y_paginacion(self):
        for numero in range(22):
            Articulo.objects.create(nombre=f"Prueba {numero:02}", codigo=f"P-{numero}", categoria=self.categoria)
        response = self.client.get(reverse("inventario:articulos"), {"buscar": "Prueba", "estado": "activo"})
        self.assertEqual(len(response.context["articulos"]), 20)
        self.assertContains(response, "Página 1 de 2")
        self.assertContains(response, "buscar=Prueba&amp;estado=activo&amp;page=2")
        response = self.client.get(reverse("inventario:articulos"), {"buscar": "Prueba", "estado": "activo", "page": 2})
        self.assertEqual(len(response.context["articulos"]), 2)
        response = self.client.get(reverse("inventario:articulos"), {"estado": "inactivo"})
        self.assertEqual([a.pk for a in response.context["articulos"]], [self.inactivo.pk])

    def test_filtros_invalidos_no_amplian_resultados(self):
        for ruta, params in (("consultar_stock", {"anexo": "invalido"}),
                             ("panel", {"estado": "invalido"}),
                             ("historial", {"desde": "no-fecha"})):
            response = self.client.get(reverse(f"inventario:{ruta}"), params)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["filtros_form"].is_valid())
            self.assertEqual(response.context["pagina"].paginator.count, 0)

    def test_funcionario_no_puede_consultar_ni_modificar_inventario(self):
        self.client.force_login(self.funcionario)
        for ruta in ("panel", "consultar_stock", "articulos", "historial", "entrada", "salida", "transferencia", "ajuste"):
            for metodo in (self.client.get, self.client.post):
                response = metodo(reverse(f"inventario:{ruta}"))
                self.assertEqual(response.status_code, 403)
