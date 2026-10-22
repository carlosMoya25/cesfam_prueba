import uuid
from unittest.mock import patch
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventario.models import Articulo, CategoriaArticulo, StockAnexo
from organizacion.models import Anexo
from .bodega import exigir_bodega_central
from .models import Medicamento, LoteMedicamento, StockLote, MovimientoFarmacia, DetalleMovimientoFarmacia
from .services import registrar_entrada_medicamento, registrar_traslado_bodega
from .test_recepcion_csv import RecepcionCSVBase


class RecepcionCentralTests(RecepcionCSVBase):
    def test_alta_manual_ignora_destino_manipulado_y_recibe_en_central(self):
        response = self.client.post(reverse("farmacia:crear_medicamento"), self.datos(ubicacion_id=str(self.sur.pk)))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StockLote.objects.get().anexo_id, self.norte.pk)
        self.assertFalse(StockLote.objects.filter(anexo=self.sur).exists())

    def test_csv_no_redirige_silenciosamente_otro_destino(self):
        carga = self.preparar([self.datos(ubicacion_id=str(self.sur.pk))])
        self.assertFalse(carga.puede_confirmar)
        self.assertIn("Bodega Central", " ".join(carga.filas[0]["errores"]))
        self.confirmar(carga)
        self.assertFalse(Medicamento.objects.exists())
        self.assertFalse(StockLote.objects.exists())

    def test_formularios_muestran_destino_fijo_y_servicio_rechaza_recepcion_directa_a_anexo(self):
        self.confirmar()
        for ruta, campo in (("crear_medicamento", "ubicacion_id"), ("registrar_entrada", "anexo")):
            response = self.client.get(reverse(f"farmacia:{ruta}"))
            self.assertTrue(response.context["form"].fields[campo].disabled)
            self.assertEqual(response.context["form"].initial[campo], self.norte.pk)
        with self.assertRaisesMessage(ValidationError, "Bodega Central"):
            registrar_entrada_medicamento(medicamento=Medicamento.objects.get(), anexo=self.sur,
                numero_lote="NUEVO", fecha_vencimiento=timezone.localdate() + timedelta(days=90),
                cantidad=1, usuario=self.usuario)
        self.assertEqual(StockLote.objects.get().cantidad, 40)

    def test_central_renombrada_conserva_configuracion_y_no_se_duplican_ubicaciones(self):
        anteriores = Anexo.objects.count()
        self.norte.nombre = "Bodega central de abastecimiento"
        self.norte.save()
        self.assertEqual(exigir_bodega_central().pk, self.norte.pk)
        self.confirmar()
        self.assertEqual(StockLote.objects.get().anexo_id, self.norte.pk)
        self.assertEqual(Anexo.objects.count(), anteriores)

    def test_entrada_existente_no_permite_cambiar_el_destino_por_post(self):
        self.confirmar()
        response = self.client.post(reverse("farmacia:registrar_entrada"), {
            "medicamento": Medicamento.objects.get().pk, "lote": Medicamento.objects.get().lotes.get().pk,
            "anexo": self.sur.pk, "cantidad": 5, "proveedor": "Proveedor manual",
            "documento_recepcion": "GUIA-2", "fecha_recepcion": timezone.localdate().isoformat(),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StockLote.objects.get().cantidad, 45)
        self.assertEqual(StockLote.objects.get().anexo_id, self.norte.pk)

    def test_migracion_reutiliza_bodega_existente_sin_duplicar_ni_mover_stock(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        from .models import ConfiguracionFarmacia
        self.confirmar()
        ConfiguracionFarmacia.objects.all().delete()
        migracion = import_module("farmacia.migrations.0004_movimientofarmacia_clave_traslado_and_more")
        anteriores = Anexo.objects.count()
        migracion.configurar_bodega(apps, SimpleNamespace(connection=connection))
        self.assertEqual(exigir_bodega_central().pk, self.norte.pk)
        self.assertEqual(Anexo.objects.count(), anteriores)
        self.assertEqual(StockLote.objects.get().cantidad, 40)


class TrasladosFarmaciaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_superuser(email="traslados@example.com")
        cls.central = exigir_bodega_central()
        cls.destino = Anexo.objects.create(nombre="Anexo receptor")
        cls.otro = Anexo.objects.create(nombre="Segundo destino")
        cls.medicamento = Medicamento.objects.create(
            articulo=Articulo.objects.create(nombre="Medicamento traslado", codigo="TRA-1",
                categoria=CategoriaArticulo.objects.create(nombre="Medicamentos traslado")),
            principio_activo="Prueba", concentracion="500 mg", forma_farmaceutica="Comprimido",
            unidad_medida="comprimido",
        )
        cls.recepcion = registrar_entrada_medicamento(medicamento=cls.medicamento, anexo=cls.central,
            numero_lote="L-001", fecha_vencimiento=timezone.localdate() + timedelta(days=90), cantidad=40,
            usuario=cls.usuario, proveedor="Proveedor", documento_recepcion="GUIA-1", fecha_recepcion=timezone.localdate())
        cls.lote = cls.medicamento.lotes.get()

    def setUp(self):
        self.client.force_login(self.usuario)

    def trasladar(self, **cambios):
        datos = dict(lote=self.lote, destino=self.destino, cantidad=12, usuario=self.usuario,
                     documento_traslado="TR-001", solicitud_id=uuid.uuid4(), motivo="Reposición del anexo")
        return registrar_traslado_bodega(**(datos | cambios))

    def saldo(self, anexo):
        return StockLote.objects.filter(anexo=anexo, lote=self.lote).aggregate(total=Sum("cantidad"))["total"] or 0

    def test_traslada_mismo_lote_y_preserva_total_respaldo_y_copias_historicas(self):
        StockLote.objects.create(lote=self.lote, anexo=self.destino, cantidad=3)
        movimiento = self.trasladar()
        self.assertEqual(self.saldo(self.central), 28)
        self.assertEqual(self.saldo(self.destino), 15)
        self.assertEqual(StockLote.objects.aggregate(total=Sum("cantidad"))["total"], 43)
        self.assertEqual(movimiento.tipo, "TRANSFERENCIA")
        self.assertEqual(movimiento.anexo_origen_id, self.central.pk)
        self.assertEqual(movimiento.anexo_destino_id, self.destino.pk)
        self.assertEqual(movimiento.usuario_id, self.usuario.pk)
        self.assertEqual(movimiento.documento_traslado, "TR-001")
        detalle = movimiento.detalles.get()
        self.assertEqual(detalle.lote_id, self.lote.pk)
        self.assertEqual(detalle.vencimiento_registrado, self.lote.fecha_vencimiento)
        self.assertEqual(detalle.unidad_registrada, "comprimido")
        self.assertFalse(StockAnexo.objects.exists())
        self.recepcion.refresh_from_db()
        self.assertEqual(self.recepcion.documento_recepcion, "GUIA-1")
        response = self.client.get(reverse("farmacia:historial"), {"tipo": "TRANSFERENCIA"})
        self.assertContains(response, "TR-001")
        self.assertContains(response, self.central.nombre)
        self.assertContains(response, self.destino.nombre)
        self.assertEqual(response.context["pagina"].paginator.count, 1)

    def test_cantidad_invalida_insuficiente_y_overflow_no_modifican_saldos(self):
        for cantidad in (0, -1, True, 41, 2147483648):
            with self.subTest(cantidad=cantidad), self.assertRaises(ValidationError):
                self.trasladar(cantidad=cantidad)
        StockLote.objects.create(lote=self.lote, anexo=self.destino, cantidad=2147483647)
        with self.assertRaises(ValidationError):
            self.trasladar()
        self.assertEqual(self.saldo(self.central), 40)
        self.assertEqual(self.saldo(self.destino), 2147483647)
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)

    def test_destino_igual_o_inactivo_y_lote_vencido_rechazados(self):
        with self.assertRaises(ValidationError):
            self.trasladar(destino=self.central)
        self.destino.activo = False
        self.destino.save()
        with self.assertRaises(ValidationError):
            self.trasladar()
        self.destino.activo = True
        self.destino.save()
        self.lote.fecha_vencimiento = timezone.localdate() - timedelta(days=1)
        self.lote.save()
        with self.assertRaises(ValidationError):
            self.trasladar()
        self.assertEqual(self.saldo(self.central), 40)
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)

    def test_lote_o_medicamento_inactivo_y_unidad_desconocida_rechazados(self):
        self.lote.activo = False
        self.lote.save()
        with self.assertRaises(ValidationError):
            self.trasladar()
        self.lote.activo = True
        self.lote.save()
        for cambios in ({"activo": False}, {"unidad_medida": ""}):
            Medicamento.objects.filter(pk=self.medicamento.pk).update(**cambios)
            with self.assertRaises(ValidationError):
                self.trasladar()
            Medicamento.objects.filter(pk=self.medicamento.pk).update(activo=True, unidad_medida="comprimido")
        self.assertEqual(self.saldo(self.central), 40)

    def test_reintento_identico_no_duplica_y_cambiar_datos_exige_nueva_solicitud(self):
        identificador = uuid.uuid4()
        primero = self.trasladar(solicitud_id=identificador)
        segundo = self.trasladar(solicitud_id=identificador)
        self.assertEqual(primero.pk, segundo.pk)
        with self.assertRaises(ValidationError):
            self.trasladar(solicitud_id=identificador, cantidad=2)
        self.assertEqual(self.saldo(self.central), 28)
        self.assertEqual(MovimientoFarmacia.objects.filter(tipo="TRANSFERENCIA").count(), 1)

    def test_fallo_despues_de_descontar_revierte_stock_y_movimiento(self):
        with patch("farmacia.services.DetalleMovimientoFarmacia.objects.create", side_effect=ValidationError("Fallo simulado")):
            with self.assertRaises(ValidationError):
                self.trasladar()
        self.assertEqual(self.saldo(self.central), 40)
        self.assertFalse(StockLote.objects.filter(anexo=self.destino).exists())
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)

    def test_flujo_web_doble_post_incluso_al_vaciar_bodega(self):
        url = reverse("farmacia:trasladar_a_anexo")
        response = self.client.get(url, {"lote": self.lote.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["lote"], self.lote.pk)
        datos = {"lote": self.lote.pk, "destino": self.destino.pk, "cantidad": 40,
                 "documento_traslado": "TR-002", "solicitud_id": str(response.context["form"]["solicitud_id"].value())}
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        self.assertEqual(self.saldo(self.central), 0)
        self.assertEqual(self.saldo(self.destino), 40)
        self.assertEqual(MovimientoFarmacia.objects.filter(tipo="TRANSFERENCIA").count(), 1)

    def test_central_inactiva_bloquea_operacion_y_no_pierde_stock(self):
        self.central.activo = False
        self.central.save()
        with self.assertRaisesMessage(ValidationError, "inactiva"):
            self.trasladar()
        response = self.client.post(reverse("farmacia:trasladar_a_anexo"), {
            "lote": self.lote.pk, "destino": self.destino.pk, "cantidad": 5,
            "documento_traslado": "TR-003", "solicitud_id": str(uuid.uuid4()),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.saldo(self.central), 40)

    def test_pantalla_muestra_stock_en_selector_y_saldos_por_anexo(self):
        StockLote.objects.create(lote=self.lote, anexo=self.destino, cantidad=8)
        inactivo = Anexo.objects.create(nombre="Anexo cerrado", activo=False)
        StockLote.objects.create(lote=self.lote, anexo=inactivo, cantidad=99)
        response = self.client.get(reverse("farmacia:trasladar_a_anexo"), {"lote": self.lote.pk})
        self.assertContains(response, "Stock: 40 comprimido")
        self.assertContains(response, 'role="combobox"')
        self.assertContains(response, 'role="listbox"')
        self.assertContains(response, 'id="stock-bodega-traslado"')
        self.assertContains(response, 'id="stock-anexo-despues"')
        self.assertEqual(response.context["stocks_traslado"][0]["cantidad"], 40)
        self.assertEqual(response.context["stocks_traslado"][0]["lote__numero_lote"], "L-001")
        self.assertEqual(response.context["stocks_anexos"], [{"lote_id": self.lote.pk, "anexo_id": self.destino.pk, "cantidad": 8}])

    def test_busqueda_no_ofrece_lotes_sin_saldo_vencidos_o_inactivos(self):
        for numero, cantidad, activo, dias in (("SIN-SALDO", 0, True, 90), ("VENCIDO", 10, True, -1), ("INACTIVO", 10, False, 90)):
            lote = LoteMedicamento.objects.create(medicamento=self.medicamento, numero_lote=numero,
                activo=activo, fecha_vencimiento=timezone.localdate() + timedelta(days=dias))
            StockLote.objects.create(lote=lote, anexo=self.central, cantidad=cantidad)
        response = self.client.get(reverse("farmacia:trasladar_a_anexo"))
        self.assertEqual([stock["lote_id"] for stock in response.context["stocks_traslado"]], [self.lote.pk])
        self.assertNotContains(response, "SIN-SALDO")
        self.assertNotContains(response, "VENCIDO")
        self.assertNotContains(response, "INACTIVO")
