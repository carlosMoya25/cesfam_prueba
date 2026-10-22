import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from inventario.models import Articulo, CategoriaArticulo
from organizacion.models import Anexo
from .bodega import exigir_bodega_central
from .centralizacion import plan_centralizacion, centralizar_existencias
from .models import Medicamento, LoteMedicamento, StockLote, MovimientoFarmacia, DetalleMovimientoFarmacia


class CentralizacionFarmaciaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(email="centralizar@example.com")
        cls.central = exigir_bodega_central()
        cls.origen = Anexo.objects.create(nombre="Origen antiguo")
        cls.otro = Anexo.objects.create(nombre="Otro anexo")
        categoria = CategoriaArticulo.objects.create(nombre="Farmacia centralización")
        cls.medicamento = Medicamento.objects.create(
            articulo=Articulo.objects.create(nombre="Medicamento", codigo="CENT-1", categoria=categoria),
            principio_activo="Prueba", concentracion="500 mg", forma_farmaceutica="Comprimido", unidad_medida="comprimido",
        )
        cls.lote = LoteMedicamento.objects.create(medicamento=cls.medicamento, numero_lote="ANTERIOR",
            fecha_vencimiento=timezone.localdate() + timedelta(days=50))
        StockLote.objects.create(lote=cls.lote, anexo=cls.central, cantidad=50)
        StockLote.objects.create(lote=cls.lote, anexo=cls.origen, cantidad=20)
        StockLote.objects.create(lote=cls.lote, anexo=cls.otro, cantidad=10)
        cls.recepcion = MovimientoFarmacia.objects.create(tipo="ENTRADA", anexo_destino=cls.origen,
            usuario=cls.admin, proveedor="Proveedor original", documento_recepcion="GUIA-ORIGINAL")
        DetalleMovimientoFarmacia.objects.create(movimiento=cls.recepcion, lote=cls.lote, cantidad=20)

    def aplicar(self, **cambios):
        self.respaldos = []
        datos = dict(usuario=self.admin, huella_esperada=plan_centralizacion()["huella"], respaldar=self.respaldos.append)
        return centralizar_existencias(**(datos | cambios))

    def saldos(self):
        return list(StockLote.objects.order_by("pk").values("pk", "lote_id", "anexo_id", "cantidad"))

    def test_consolida_por_lote_preserva_total_ingresos_y_registra_origenes(self):
        resultado = self.aplicar()
        self.assertEqual(resultado["registros_trasladados"], 2)
        self.assertEqual(resultado["cantidad_registrada"], 30)
        self.assertEqual(StockLote.objects.get(anexo=self.central).cantidad, 80)
        self.assertFalse(StockLote.objects.exclude(anexo=self.central).filter(cantidad__gt=0).exists())
        self.assertEqual(StockLote.objects.count(), 3)
        self.recepcion.refresh_from_db()
        self.assertEqual(self.recepcion.anexo_destino_id, self.origen.pk)
        self.assertEqual(self.recepcion.documento_recepcion, "GUIA-ORIGINAL")
        movimientos = MovimientoFarmacia.objects.filter(tipo="TRANSFERENCIA")
        self.assertEqual(movimientos.count(), 2)
        self.assertEqual(set(movimientos.values_list("anexo_origen_id", flat=True)), {self.origen.pk, self.otro.pk})
        self.assertEqual(self.respaldos[0]["plan"]["existencias"][0]["cantidad"], 50)
        self.assertEqual(self.respaldos[0]["plan"]["existencias"][1]["cantidad"], 20)
        for movimiento in movimientos:
            detalle = movimiento.detalles.get()
            self.assertEqual(movimiento.usuario_id, self.admin.pk)
            self.assertEqual(detalle.vencimiento_registrado, self.lote.fecha_vencimiento)
            self.assertEqual(detalle.lote_id, self.lote.pk)

    def test_unidad_desconocida_se_conserva_y_queda_para_revision(self):
        self.medicamento.unidad_medida = ""
        self.medicamento.save()
        resultado = self.aplicar()
        self.assertEqual(len(resultado["revisar_unidad"]), 2)
        self.medicamento.refresh_from_db()
        self.assertEqual(self.medicamento.unidad_medida, "")
        self.assertTrue(MovimientoFarmacia.objects.filter(tipo="TRANSFERENCIA", motivo__contains="pendiente de verificar").exists())
        self.assertEqual(StockLote.objects.get(anexo=self.central).cantidad, 80)

    def test_cambios_desde_revision_o_usuario_no_admin_impiden_modificar(self):
        huella = plan_centralizacion()["huella"]
        StockLote.objects.filter(anexo=self.otro).update(cantidad=11)
        antes = self.saldos()
        with self.assertRaises(ValidationError):
            self.aplicar(huella_esperada=huella)
        usuario = get_user_model().objects.create_user(email="sin-permiso-centralizar@example.com")
        with self.assertRaises(ValidationError):
            self.aplicar(usuario=usuario)
        self.assertEqual(self.saldos(), antes)

    def test_si_no_se_puede_respaldar_no_se_modifica_stock(self):
        antes = self.saldos()
        with self.assertRaises(OSError):
            self.aplicar(respaldar=lambda datos: (_ for _ in ()).throw(OSError("Sin respaldo")))
        self.assertEqual(self.saldos(), antes)
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)

    def test_fallo_durante_centralizacion_revierte_todos_los_cambios(self):
        antes = self.saldos()
        crear = DetalleMovimientoFarmacia.objects.create
        llamadas = []
        def detalle(**kwargs):
            llamadas.append(1)
            if len(llamadas) == 2:
                raise ValidationError("Fallo simulado")
            return crear(**kwargs)
        with patch("farmacia.centralizacion.DetalleMovimientoFarmacia.objects.create", side_effect=detalle):
            with self.assertRaises(ValidationError):
                self.aplicar()
        self.assertEqual(self.saldos(), antes)
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)

    def test_no_duplica_al_repetir_ni_modifica_plan_de_consulta(self):
        salida = io.StringIO()
        antes = self.saldos()
        call_command("centralizar_farmacia", stdout=salida)
        self.assertEqual(self.saldos(), antes)
        self.assertIn('"registros": 2', salida.getvalue())
        self.aplicar()
        resultado = self.aplicar()
        self.assertEqual(resultado["registros_trasladados"], 0)
        self.assertEqual(MovimientoFarmacia.objects.filter(tipo="TRANSFERENCIA").count(), 2)
        self.assertEqual(self.respaldos, [])

    def test_desbordamiento_y_lote_vencido_bloquean_toda_la_operacion(self):
        StockLote.objects.filter(anexo=self.central).update(cantidad=2147483647)
        with self.assertRaises(ValidationError):
            self.aplicar()
        StockLote.objects.filter(anexo=self.central).update(cantidad=50)
        self.lote.fecha_vencimiento = timezone.localdate() - timedelta(days=1)
        self.lote.save()
        with self.assertRaises(ValidationError):
            self.aplicar()
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)
        self.assertEqual(StockLote.objects.get(anexo=self.origen).cantidad, 20)
