from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventario.models import Articulo, CategoriaArticulo, StockAnexo, MovimientoInventario, DetalleMovimiento, TipoMovimiento
from inventario.forms import EntradaInventarioForm
from inventario.services import registrar_entrada
from .bodega import exigir_bodega_central
from .models import Medicamento, LoteMedicamento, StockLote, StockPendienteFarmacia
from .regularizacion_general import plan_regularizacion_general, regularizar_general


class RegularizacionGeneralTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(email="regularizar@example.com")
        cls.central = exigir_bodega_central()
        categoria = CategoriaArticulo.objects.create(nombre="Medicamentos")
        cls.vacio = Articulo.objects.create(nombre="Medicamento vacío", codigo="VAC-1", categoria=categoria)
        cls.sertralina = Articulo.objects.create(nombre="Sertralina 50 mg", codigo="MED0087", categoria=categoria)
        cls.paracetamol = Articulo.objects.create(nombre="Paracetamol", codigo="MED-001", categoria=categoria)
        med = Medicamento.objects.create(articulo=cls.paracetamol, principio_activo="Paracetamol", concentracion="500 mg", forma_farmaceutica="Comprimido")
        lote = LoteMedicamento.objects.create(medicamento=med, numero_lote="001", fecha_vencimiento=timezone.localdate() + timedelta(days=100))
        StockLote.objects.create(lote=lote, anexo=cls.central, cantidad=3500)
        tipo, _ = TipoMovimiento.objects.get_or_create(nombre="Entrada")
        for articulo, cantidad in ((cls.sertralina, 20), (cls.paracetamol, 2000)):
            StockAnexo.objects.create(articulo=articulo, anexo=cls.central, cantidad=cantidad)
            movimiento = MovimientoInventario.objects.create(tipo_movimiento=tipo, anexo_destino=cls.central, usuario=cls.admin)
            DetalleMovimiento.objects.create(movimiento=movimiento, articulo=articulo, cantidad=cantidad)

    def aplicar(self, **cambios):
        self.respaldos = []
        return regularizar_general(**(dict(usuario=self.admin, huella_esperada=plan_regularizacion_general()["huella"], respaldar=self.respaldos.append) | cambios))

    def test_retira_solo_vacios_y_conserva_saldos_en_pendientes_sin_sumar_a_farmacia(self):
        resultado = self.aplicar()
        self.assertFalse(Articulo.objects.filter(pk=self.vacio.pk).exists())
        self.assertTrue(Articulo.objects.filter(pk=self.sertralina.pk).exists())
        self.assertEqual(len(resultado["eliminados"]), 1)
        self.assertEqual(len(resultado["pendientes"]), 2)
        self.assertEqual(StockPendienteFarmacia.objects.get(articulo=self.sertralina).cantidad, 20)
        self.assertEqual(StockPendienteFarmacia.objects.get(articulo=self.paracetamol).cantidad, 2000)
        self.assertFalse(StockAnexo.objects.filter(cantidad__gt=0).exists())
        self.assertEqual(StockLote.objects.get().cantidad, 3500)
        self.assertEqual(DetalleMovimiento.objects.filter(movimiento__tipo_movimiento__nombre="Entrada").count(), 2)
        self.assertEqual(self.respaldos[0]["plan"]["saldos"][0]["cantidad"], 20)
        self.assertFalse(Medicamento.objects.filter(articulo=self.sertralina).exists())

    def test_pendiente_desaparece_de_general_y_se_consulta_desde_farmacia(self):
        self.aplicar()
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse("inventario:articulos")), "Sertralina 50 mg")
        self.assertNotContains(self.client.get(reverse("admin:inventario_articulo_changelist")), "Sertralina 50 mg")
        self.assertNotContains(self.client.get(reverse("admin:inventario_stockanexo_changelist")), "Paracetamol")
        self.assertNotIn(self.sertralina, EntradaInventarioForm().fields["articulo"].queryset)
        with self.assertRaises(ValidationError):
            registrar_entrada(usuario=self.admin, articulo=self.sertralina, anexo=self.central, cantidad=2)
        response = self.client.get(reverse("farmacia:pendientes_conciliacion"))
        self.assertContains(response, "Sertralina 50 mg")
        self.assertContains(response, "Paracetamol")
        self.assertContains(response, "No se sumaron a Farmacia")

    def test_segunda_ejecucion_no_elimina_historial_ni_duplica_pendientes(self):
        self.aplicar()
        movimientos = MovimientoInventario.objects.count()
        resultado = self.aplicar()
        self.assertEqual(resultado["pendientes"], [])
        self.assertEqual(resultado["eliminados"], [])
        self.assertEqual(StockPendienteFarmacia.objects.count(), 2)
        self.assertEqual(MovimientoInventario.objects.count(), movimientos)

    def test_error_intermedio_revierte_movimientos_saldos_y_eliminaciones(self):
        crear = StockPendienteFarmacia.objects.create
        llamadas = []
        def archivar(**kwargs):
            llamadas.append(1)
            if len(llamadas) == 2:
                raise ValidationError("Fallo simulado")
            return crear(**kwargs)
        with patch("farmacia.regularizacion_general.StockPendienteFarmacia.objects.create", side_effect=archivar):
            with self.assertRaises(ValidationError):
                self.aplicar()
        self.assertTrue(Articulo.objects.filter(pk=self.vacio.pk).exists())
        self.assertEqual(StockAnexo.objects.get(articulo=self.sertralina).cantidad, 20)
        self.assertEqual(StockAnexo.objects.get(articulo=self.paracetamol).cantidad, 2000)
        self.assertFalse(StockPendienteFarmacia.objects.exists())
        self.assertEqual(MovimientoInventario.objects.count(), 2)

    def test_plan_modificado_y_usuario_sin_permiso_no_modifican_registros(self):
        huella = plan_regularizacion_general()["huella"]
        StockAnexo.objects.filter(articulo=self.sertralina).update(cantidad=21)
        with self.assertRaises(ValidationError):
            self.aplicar(huella_esperada=huella)
        usuario = get_user_model().objects.create_user(email="sin-permiso-regularizar@example.com")
        with self.assertRaises(ValidationError):
            self.aplicar(usuario=usuario)
        self.assertEqual(StockAnexo.objects.get(articulo=self.sertralina).cantidad, 21)
        self.assertFalse(StockPendienteFarmacia.objects.exists())

    def test_sin_respaldo_no_se_aplica_y_la_consulta_requiere_rol_de_farmacia(self):
        with self.assertRaises(OSError):
            self.aplicar(respaldar=lambda datos: (_ for _ in ()).throw(OSError("Sin respaldo")))
        self.assertTrue(Articulo.objects.filter(pk=self.vacio.pk).exists())
        usuario = get_user_model().objects.create_user(email="lector-sin-permiso@example.com")
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(reverse("farmacia:pendientes_conciliacion")).status_code, 403)

    def test_formulario_general_no_permite_crear_otro_medicamento_huerfano(self):
        from inventario.forms import ArticuloForm
        form = ArticuloForm(data={"nombre": "Otro medicamento", "codigo": "MED-NUEVO",
            "categoria": self.vacio.categoria_id, "stock_minimo": 0, "activo": "on"})
        self.assertFalse(form.is_valid())
        self.assertIn("categoria", form.errors)
        self.assertIn("Farmacia", " ".join(form.errors["categoria"]))
