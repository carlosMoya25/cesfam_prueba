from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventario.models import Articulo, CategoriaArticulo
from organizacion.models import Anexo
from usuarios.utils import puede_gestionar_farmacia
from .models import Medicamento, MovimientoFarmacia, StockLote
from .services import registrar_entrada_medicamento
from .bodega import exigir_bodega_central


class PermisosFarmaciaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuarios = {}
        for indice, rol in enumerate(("Administrador", "Encargado de Farmacia",
                                     "Encargado de Inventario", "Funcionario", "Técnico TI")):
            grupo, _ = Group.objects.get_or_create(name=rol)
            usuario = get_user_model().objects.create_user(email=f"rol{indice}@example.com")
            usuario.groups.add(grupo)
            cls.usuarios[rol] = usuario
        cls.superusuario = get_user_model().objects.create_superuser(email="root@example.com")
        categoria = CategoriaArticulo.objects.create(nombre="Farmacia de pruebas")
        articulo = Articulo.objects.create(nombre="Medicamento de prueba", categoria=categoria)
        cls.medicamento = Medicamento.objects.create(
            articulo=articulo, principio_activo="Prueba", concentracion="10",
            forma_farmaceutica="Comprimido", unidad_medida="comprimido",
        )
        cls.anexo = exigir_bodega_central()

    def rutas(self):
        return [
            ("dashboard", {}), ("lista_medicamentos", {}), ("historial", {}),
            ("crear_medicamento", {}), ("registrar_entrada", {}), ("trasladar_a_anexo", {}),
            ("detalle_medicamento", {"pk": self.medicamento.pk}),
            ("editar_medicamento", {"pk": self.medicamento.pk}),
        ]

    def entrada(self, usuario):
        return registrar_entrada_medicamento(
            medicamento=self.medicamento, anexo=self.anexo, numero_lote="LOTE",
            fecha_vencimiento=timezone.localdate() + timedelta(days=90),
            cantidad=5, usuario=usuario,
        )

    def test_roles_autorizados_acceden_a_todas_las_paginas(self):
        for usuario in (self.usuarios["Administrador"],
                        self.usuarios["Encargado de Farmacia"], self.superusuario):
            self.client.force_login(usuario)
            for nombre, kwargs in self.rutas():
                with self.subTest(usuario=usuario.email, ruta=nombre):
                    self.assertEqual(self.client.get(reverse(f"farmacia:{nombre}", kwargs=kwargs)).status_code, 200)
            response = self.client.get(reverse("dashboard"))
            self.assertContains(response, reverse("farmacia:dashboard"))

    def test_otros_roles_no_pueden_acceder_ni_publicar_directamente(self):
        for rol in ("Encargado de Inventario", "Funcionario", "Técnico TI"):
            self.client.force_login(self.usuarios[rol])
            for nombre, kwargs in self.rutas() + [
                ("cambiar_estado_medicamento", {"pk": self.medicamento.pk})
            ]:
                url = reverse(f"farmacia:{nombre}", kwargs=kwargs)
                for metodo in (self.client.get, self.client.post):
                    with self.subTest(rol=rol, ruta=nombre, metodo=metodo.__name__):
                        self.assertEqual(metodo(url).status_code, 403)
            response = self.client.get(reverse("dashboard"))
            self.assertNotContains(response, reverse("farmacia:dashboard"))
        self.medicamento.refresh_from_db()
        self.assertTrue(self.medicamento.activo)
        self.assertFalse(MovimientoFarmacia.objects.exists())

    def test_servicio_rechaza_otros_roles_y_no_crea_stock(self):
        for rol in ("Encargado de Inventario", "Funcionario", "Técnico TI"):
            with self.subTest(rol=rol):
                with self.assertRaisesMessage(ValidationError, "permisos"):
                    self.entrada(self.usuarios[rol])
        self.assertFalse(StockLote.objects.exists())
        self.assertFalse(self.medicamento.lotes.exists())
        self.assertFalse(MovimientoFarmacia.objects.exists())

    def test_servicio_permite_ambos_roles_y_superusuario(self):
        for usuario in (self.usuarios["Administrador"],
                        self.usuarios["Encargado de Farmacia"], self.superusuario):
            self.entrada(usuario)
        self.assertEqual(StockLote.objects.get().cantidad, 15)
        self.assertEqual(MovimientoFarmacia.objects.count(), 3)

    def test_encargado_puede_registrar_entrada_por_post(self):
        from .models import LoteMedicamento
        lote = LoteMedicamento.objects.create(
            medicamento=self.medicamento, numero_lote="POST",
            fecha_vencimiento=timezone.localdate() + timedelta(days=90),
        )
        self.client.force_login(self.usuarios["Encargado de Farmacia"])
        response = self.client.post(reverse("farmacia:registrar_entrada"), {
            "medicamento": self.medicamento.pk, "anexo": self.anexo.pk,
            "lote": lote.pk, "cantidad": 7,
            "proveedor": "Proveedor de prueba", "documento_recepcion": "GUIA-001",
            "fecha_recepcion": timezone.localdate().isoformat(),
        })
        self.assertRedirects(response, reverse("farmacia:dashboard"))
        self.assertEqual(StockLote.objects.get().cantidad, 7)

    def test_anonimo_redirigido_al_login(self):
        for nombre, kwargs in self.rutas():
            response = self.client.get(reverse(f"farmacia:{nombre}", kwargs=kwargs))
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse("login")))
        self.assertFalse(puede_gestionar_farmacia(AnonymousUser()))

    def test_usuario_inactivo_no_puede_usar_servicio(self):
        usuario = self.usuarios["Encargado de Farmacia"]
        usuario.is_active = False
        self.assertFalse(puede_gestionar_farmacia(usuario))
        with self.assertRaisesMessage(ValidationError, "permisos"):
            self.entrada(usuario)

    def test_roles_acumulados_conservan_acceso_a_farmacia(self):
        usuario = self.usuarios["Funcionario"]
        usuario.groups.add(Group.objects.get(name="Encargado de Farmacia"))
        self.assertTrue(puede_gestionar_farmacia(usuario))
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(reverse("farmacia:dashboard")).status_code, 200)


class FiltrosFarmaciaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .models import LoteMedicamento
        cls.usuario = get_user_model().objects.create_superuser(email="filtros@example.com")
        cls.norte = Anexo.objects.create(nombre="Norte")
        cls.sur = Anexo.objects.create(nombre="Sur")
        cls.categoria = CategoriaArticulo.objects.create(nombre="Medicamentos")
        cls.paracetamol = cls.crear_medicamento("Paracetamol", "PAR-001", "Acetaminofeno")
        cls.ibuprofeno = cls.crear_medicamento("Ibuprofeno", "IBU-001", "Ibuprofeno")
        cls.inactivo = cls.crear_medicamento("Inactivo", "INA-001", "Otro", activo=False)
        hoy = timezone.localdate()
        cls.vigente = LoteMedicamento.objects.create(
            medicamento=cls.paracetamol, numero_lote="VIGENTE",
            fecha_vencimiento=hoy + timedelta(days=10),
        )
        cls.vencido = LoteMedicamento.objects.create(
            medicamento=cls.paracetamol, numero_lote="VENCIDO",
            fecha_vencimiento=hoy - timedelta(days=1),
        )
        StockLote.objects.create(lote=cls.vigente, anexo=cls.norte, cantidad=10)
        StockLote.objects.create(lote=cls.vigente, anexo=cls.sur, cantidad=7)
        StockLote.objects.create(lote=cls.vencido, anexo=cls.sur, cantidad=3)

    @classmethod
    def crear_medicamento(cls, nombre, codigo, principio, activo=True):
        articulo = Articulo.objects.create(nombre=nombre, codigo=codigo, categoria=cls.categoria)
        return Medicamento.objects.create(
            articulo=articulo, principio_activo=principio, concentracion="10",
            forma_farmaceutica="Comprimido", unidad_medida="comprimido", activo=activo,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def catalogo(self, **params):
        return self.client.get(reverse("farmacia:lista_medicamentos"), params)

    def test_busca_por_nombre_principio_y_codigo_sin_duplicar_saldos(self):
        for busqueda in ("paracetamol", "Acetaminofeno", "PAR-001"):
            with self.subTest(busqueda=busqueda):
                response = self.catalogo(q=busqueda)
                medicamentos = list(response.context["medicamentos"])
                self.assertEqual(len(medicamentos), 1)
                self.assertEqual(medicamentos[0].pk, self.paracetamol.pk)
                self.assertEqual(medicamentos[0].stock_total, 20)

    def test_ubicacion_filtra_stock_catalogo_panel_y_detalle(self):
        params = {"ubicacion": self.norte.pk}
        response = self.catalogo(**params)
        self.assertEqual(list(response.context["medicamentos"])[0].stock_total, 10)
        self.assertEqual(response.context["pagina"].paginator.count, 1)
        response = self.client.get(reverse("farmacia:dashboard"), params)
        self.assertEqual(response.context["total_stock"], 10)
        self.assertEqual(response.context["lotes_vencidos"], 0)
        response = self.client.get(reverse("farmacia:detalle_medicamento", args=[self.paracetamol.pk]), params)
        self.assertEqual(response.context["stock_total"], 10)
        self.assertEqual(len(response.context["stocks"]), 1)

    def test_ubicacion_y_vencimiento_deben_coincidir_en_la_misma_fila(self):
        response = self.catalogo(ubicacion=self.norte.pk, vencimiento="vencido")
        self.assertEqual(response.context["pagina"].paginator.count, 0)
        response = self.catalogo(ubicacion=self.sur.pk, vencimiento="vencido")
        medicamentos = list(response.context["medicamentos"])
        self.assertEqual(len(medicamentos), 1)
        self.assertEqual(medicamentos[0].stock_total, 3)
        response = self.client.get(reverse("farmacia:dashboard"), {"ubicacion": self.sur.pk, "vencimiento": "vencido"})
        self.assertEqual(response.context["total_stock"], 3)
        self.assertEqual(response.context["lotes_vencidos"], 1)
        self.assertEqual(response.context["total_proximos"], 0)

    def test_estado_y_existencias_incluyen_medicamentos_sin_registros(self):
        response = self.catalogo(estado="inactivo")
        self.assertEqual([m.pk for m in response.context["medicamentos"]], [self.inactivo.pk])
        response = self.catalogo(estado="activo", existencias="sin_stock")
        self.assertEqual([m.pk for m in response.context["medicamentos"]], [self.ibuprofeno.pk])
        response = self.catalogo(existencias="con_stock")
        self.assertEqual([m.pk for m in response.context["medicamentos"]], [self.paracetamol.pk])

    def test_filtros_invalidos_muestran_error_y_no_amplian_resultados(self):
        for params in ({"ubicacion": "no-numero"}, {"ubicacion": "999999"},
                       {"estado": "inventado"}, {"vencimiento": "inventado"}):
            with self.subTest(params=params):
                response = self.catalogo(**params)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context["filtros_form"].is_valid())
                self.assertEqual(response.context["pagina"].paginator.count, 0)

    def test_paginacion_y_enlaces_conservan_filtros(self):
        from html import escape
        for numero in range(22):
            self.crear_medicamento(f"Prueba {numero:02}", f"P-{numero}", "Prueba")
        response = self.catalogo(q="Prueba", estado="activo")
        self.assertEqual(len(response.context["medicamentos"]), 20)
        self.assertContains(response, "Página 1 de 2")
        query = escape(response.context["filtros_query"], quote=True)
        self.assertContains(response, query + "&amp;page=2")
        response = self.catalogo(q="Prueba", estado="activo", page=2)
        self.assertEqual(len(response.context["medicamentos"]), 2)

    def test_movimientos_respetan_ubicacion_y_busqueda(self):
        for anexo in (self.norte, self.sur):
            # Recepciones históricas anteriores a la recepción centralizada.
            from .models import DetalleMovimientoFarmacia
            movimiento = MovimientoFarmacia.objects.create(tipo="ENTRADA", anexo_destino=anexo, usuario=self.usuario)
            DetalleMovimientoFarmacia.objects.create(movimiento=movimiento, lote=self.vigente, cantidad=2)
        response = self.client.get(reverse("farmacia:dashboard"), {"q": "PAR-001", "ubicacion": self.norte.pk})
        self.assertEqual(response.context["total_movimientos"], 1)
        self.assertEqual(response.context["movimientos_recientes"][0].anexo_destino_id, self.norte.pk)
        response = self.client.get(reverse("farmacia:dashboard"), {"q": "Sin coincidencias"})
        self.assertEqual(response.context["total_stock"], 0)
        self.assertEqual(response.context["total_movimientos"], 0)
        self.assertContains(response, "No se encontraron existencias")

    def test_unidades_utilizables_excluyen_lotes_y_ubicaciones_inactivos(self):
        self.client.force_login(self.usuario)
        response = self.client.get(reverse("farmacia:dashboard"))
        self.assertEqual(response.context["unidades_utilizables"], 17)
        self.assertEqual(response.context["total_proximos"], 1)
        self.sur.activo = False
        self.sur.save()
        response = self.client.get(reverse("farmacia:dashboard"))
        self.assertEqual(response.context["unidades_utilizables"], 10)
        self.vigente.activo = False
        self.vigente.save()
        response = self.client.get(reverse("farmacia:dashboard"))
        self.assertEqual(response.context["unidades_utilizables"], 0)

    def test_fila_vencida_no_ofrece_registrar_entrada(self):
        response = self.client.get(reverse("farmacia:dashboard"), {"vencimiento": "vencido"})
        url = reverse("farmacia:registrar_entrada") + f"?medicamento={self.paracetamol.pk}&amp;anexo={self.sur.pk}&amp;lote={self.vencido.pk}"
        self.assertNotContains(response, url)
        response = self.client.get(reverse("farmacia:dashboard"), {"vencimiento": "vigente"})
        url = reverse("farmacia:registrar_entrada") + f"?medicamento={self.paracetamol.pk}&amp;anexo={self.norte.pk}&amp;lote={self.vigente.pk}"
        self.assertNotContains(response, url)

    def test_historial_filtra_tipo_ubicacion_y_fechas_inclusivas(self):
        from datetime import datetime, time
        for anexo in (self.norte, self.sur):
            # Recepciones históricas anteriores a la recepción centralizada.
            from .models import DetalleMovimientoFarmacia
            movimiento = MovimientoFarmacia.objects.create(tipo="ENTRADA", anexo_destino=anexo, usuario=self.usuario)
            DetalleMovimientoFarmacia.objects.create(movimiento=movimiento, lote=self.vigente, cantidad=2)
        hoy = timezone.localdate()
        movimiento = MovimientoFarmacia.objects.get(anexo_destino=self.norte)
        MovimientoFarmacia.objects.filter(pk=movimiento.pk).update(
            fecha=timezone.make_aware(datetime.combine(hoy, time(23, 59, 59)))
        )
        params = {"q": "PAR-001", "ubicacion": self.norte.pk, "tipo": "ENTRADA",
                  "desde": hoy.isoformat(), "hasta": hoy.isoformat()}
        response = self.client.get(reverse("farmacia:historial"), params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m.pk for m in response.context["movimientos"]], [movimiento.pk])
        response = self.client.get(reverse("farmacia:historial"), {**params, "tipo": "SALIDA"})
        self.assertEqual(response.context["pagina"].paginator.count, 0)
        response = self.client.get(reverse("farmacia:historial"), {**params, "desde": (hoy + timedelta(days=1)).isoformat()})
        self.assertFalse(response.context["filtros_form"].is_valid())
        self.assertEqual(response.context["pagina"].paginator.count, 0)

    def test_navegacion_conserva_busqueda_y_ubicacion_sin_fechas_del_historial(self):
        response = self.client.get(reverse("farmacia:historial"), {
            "q": "PAR-001", "ubicacion": self.norte.pk, "tipo": "ENTRADA", "desde": "2026-01-01",
        })
        self.assertIn("q=PAR-001", response.context["navegacion_query"])
        self.assertIn(f"ubicacion={self.norte.pk}", response.context["navegacion_query"])
        self.assertNotIn("desde=", response.context["navegacion_query"])
        self.assertNotIn("tipo=", response.context["navegacion_query"])


class EntradaLotesExistentesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .models import LoteMedicamento
        cls.usuario = get_user_model().objects.create_superuser(email="entrada@example.com")
        cls.anexo = exigir_bodega_central()
        categoria = CategoriaArticulo.objects.create(nombre="Medicamentos de prueba")
        cls.medicamento = Medicamento.objects.create(
            articulo=Articulo.objects.create(nombre="Medicamento", categoria=categoria),
            principio_activo="Prueba", concentracion="10", forma_farmaceutica="Comprimido", unidad_medida="comprimido",
        )
        cls.otro = Medicamento.objects.create(
            articulo=Articulo.objects.create(nombre="Otro", categoria=categoria),
            principio_activo="Otro", concentracion="20", forma_farmaceutica="Comprimido", unidad_medida="comprimido",
        )
        cls.lote = LoteMedicamento.objects.create(
            medicamento=cls.medicamento, numero_lote="UNICO",
            fecha_vencimiento=timezone.localdate() + timedelta(days=60),
        )

    def setUp(self):
        self.client.force_login(self.usuario)
        self.datos = {"medicamento": self.medicamento.pk, "anexo": self.anexo.pk,
                      "cantidad": 9, "motivo": "Recepción de prueba",
                      "proveedor": "Proveedor de prueba", "documento_recepcion": "GUIA-001",
                      "fecha_recepcion": timezone.localdate().isoformat()}

    def crear_lote(self, numero, **kwargs):
        from .models import LoteMedicamento
        datos = dict(medicamento=self.medicamento, numero_lote=numero,
                     fecha_vencimiento=timezone.localdate() + timedelta(days=30))
        datos.update(kwargs)
        return LoteMedicamento.objects.create(**datos)

    def test_unico_lote_se_carga_sin_fecha_ni_numero_escritos(self):
        response = self.client.post(reverse("farmacia:registrar_entrada"), self.datos)
        self.assertRedirects(response, reverse("farmacia:dashboard"))
        stock = StockLote.objects.get()
        self.assertEqual(stock.lote_id, self.lote.pk)
        self.assertEqual(stock.cantidad, 9)
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.fecha_vencimiento, timezone.localdate() + timedelta(days=60))

    def test_varios_lotes_exigen_elegir_y_guardan_el_correspondiente(self):
        segundo = self.crear_lote("SEGUNDO")
        response = self.client.post(reverse("farmacia:registrar_entrada"), self.datos)
        self.assertFormError(response.context["form"], "lote", "Seleccione el lote correspondiente al medicamento recibido.")
        self.assertFalse(MovimientoFarmacia.objects.exists())
        response = self.client.post(reverse("farmacia:registrar_entrada"), {**self.datos, "lote": segundo.pk})
        self.assertRedirects(response, reverse("farmacia:dashboard"))
        self.assertEqual(StockLote.objects.get().lote_id, segundo.pk)

    def test_lote_de_otro_medicamento_es_rechazado(self):
        ajeno = self.crear_lote("AJENO", medicamento=self.otro)
        response = self.client.post(reverse("farmacia:registrar_entrada"), {**self.datos, "lote": ajeno.pk})
        self.assertIn("lote", response.context["form"].errors)
        self.assertFalse(StockLote.objects.exists())
        from .services import registrar_entrada_lote
        with self.assertRaisesMessage(ValidationError, "no corresponde"):
            registrar_entrada_lote(medicamento=self.medicamento, lote=ajeno, anexo=self.anexo,
                                   cantidad=1, usuario=self.usuario)

    def test_lotes_vencidos_o_inactivos_no_son_seleccionables(self):
        for lote in (self.crear_lote("INACTIVO", activo=False),
                     self.crear_lote("VENCIDO", fecha_vencimiento=timezone.localdate() - timedelta(days=1))):
            with self.subTest(lote=lote.numero_lote):
                response = self.client.post(reverse("farmacia:registrar_entrada"), {**self.datos, "lote": lote.pk})
                self.assertIn("lote", response.context["form"].errors)
        self.assertFalse(StockLote.objects.exists())

    def test_sin_lotes_muestra_aviso_y_no_crea_un_lote(self):
        response = self.client.post(reverse("farmacia:registrar_entrada"), {**self.datos, "medicamento": self.otro.pk})
        self.assertContains(response, "no tiene lotes activos sin vencer")
        self.assertFalse(self.otro.lotes.exists())
        self.assertFalse(MovimientoFarmacia.objects.exists())

    def test_pantalla_no_pide_vencimiento_y_preselecciona_lote_unico(self):
        response = self.client.get(reverse("farmacia:registrar_entrada"), {"medicamento": self.medicamento.pk})
        self.assertNotContains(response, 'name="fecha_vencimiento"')
        self.assertNotContains(response, 'name="numero_lote"')
        self.assertContains(response, 'id="buscar-medicamento"')
        self.assertEqual(response.context["form"].initial["lote"], self.lote.pk)
        self.assertEqual(len(response.context["lotes_disponibles"]), 1)
        self.assertContains(response, 'id="lotes-disponibles"', count=1)
        self.assertContains(response, 'src="/static/js/entrada_farmacia.js"', count=1)

    def test_fecha_enviada_por_cliente_no_modifica_la_del_lote(self):
        response = self.client.post(reverse("farmacia:registrar_entrada"), {
            **self.datos, "lote": self.lote.pk, "fecha_vencimiento": "2099-01-01",
        })
        self.assertEqual(response.status_code, 302)
        self.lote.refresh_from_db()
        self.assertNotEqual(self.lote.fecha_vencimiento.isoformat(), "2099-01-01")

    def test_entrada_desde_fila_precarga_medicamento_destino_y_lote_correcto(self):
        segundo = self.crear_lote("SEGUNDO")
        StockLote.objects.create(lote=segundo, anexo=self.anexo, cantidad=20)
        response = self.client.get(reverse("farmacia:registrar_entrada"), {
            "medicamento": self.medicamento.pk, "anexo": self.anexo.pk, "lote": segundo.pk,
        })
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(int(form["medicamento"].value()), self.medicamento.pk)
        self.assertEqual(int(form["anexo"].value()), self.anexo.pk)
        self.assertEqual(int(form["lote"].value()), segundo.pk)
        self.assertEqual(response.context["stocks_entrada"][0]["cantidad"], 20)
        self.assertContains(response, 'id="farmacia-entrada-saldo"')
