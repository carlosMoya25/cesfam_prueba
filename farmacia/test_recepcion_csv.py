import csv
import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventario.models import Articulo, CategoriaArticulo, StockAnexo
from organizacion.models import Anexo
from .importacion import COLUMNAS, COLUMNAS_OPCIONALES, preparar_importacion, confirmar_importacion, token_revision
from .models import Medicamento, LoteMedicamento, StockLote, MovimientoFarmacia, DetalleMovimientoFarmacia
from .services import registrar_entrada_medicamento
from .bodega import exigir_bodega_central


class RecepcionCSVBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_superuser(email="recepciones@example.com")
        cls.norte = exigir_bodega_central()
        cls.sur = Anexo.objects.create(nombre="Anexo Sur")

    def setUp(self):
        self.client.force_login(self.usuario)

    def datos(self, **cambios):
        return dict(
            codigo="PAR-001", nombre="Paracetamol", principio_activo="Paracetamol",
            concentracion="500 mg", forma_farmaceutica="Comprimido", presentacion="Caja de 20",
            requiere_receta="no", stock_minimo="10", unidad_medida="comprimido",
            numero_lote="PAR-2026-A", fecha_vencimiento=(timezone.localdate() + timedelta(days=90)).isoformat(),
            ubicacion_id=str(self.norte.pk), cantidad="40", proveedor="Abastecimiento de prueba",
            documento_recepcion="GUIA-001", fecha_recepcion=timezone.localdate().isoformat(),
            observaciones="Recepción inicial", descripcion="Catálogo de prueba",
        ) | cambios

    def archivo(self, filas=None, nombre="recepciones.csv"):
        salida = io.StringIO(newline="")
        columnas = [*COLUMNAS, *COLUMNAS_OPCIONALES]
        writer = csv.writer(salida, delimiter=";")
        writer.writerow(columnas)
        for fila in filas if filas is not None else [self.datos()]:
            writer.writerow([fila.get(columna, "") for columna in columnas])
        return SimpleUploadedFile(nombre, salida.getvalue().encode("utf-8-sig"), content_type="text/csv")

    def preparar(self, filas=None, **kwargs):
        return preparar_importacion(archivo=self.archivo(filas, **kwargs), usuario=self.usuario)

    def confirmar(self, carga=None):
        return confirmar_importacion(importacion_id=(carga or self.preparar()).pk, usuario=self.usuario)


class RecepcionCSVTests(RecepcionCSVBase):
    def test_registra_catalogo_lote_stock_y_respaldo_en_la_misma_carga(self):
        carga = self.preparar()
        self.assertTrue(carga.puede_confirmar)
        self.assertFalse(Medicamento.objects.exists())
        self.assertFalse(StockLote.objects.exists())
        carga = self.confirmar(carga)
        self.assertEqual(carga.modalidad, "RECEPCION")
        self.assertEqual(carga.cantidad_importada, 1)
        self.assertEqual(carga.cantidad_lotes_creados, 1)
        self.assertEqual(carga.cantidad_movimientos, 1)
        self.assertEqual(carga.cantidad_unidades, 40)
        self.assertEqual(StockLote.objects.get().cantidad, 40)
        self.assertEqual(Medicamento.objects.get().unidad_medida, "comprimido")
        movimiento = MovimientoFarmacia.objects.get()
        self.assertEqual(movimiento.usuario_id, self.usuario.pk)
        self.assertEqual(movimiento.importacion_id, carga.pk)
        self.assertEqual(movimiento.fila_importacion, 2)
        self.assertEqual(movimiento.proveedor, "Abastecimiento de prueba")
        self.assertEqual(movimiento.documento_recepcion, "GUIA-001")
        self.assertEqual(movimiento.fecha_recepcion, timezone.localdate())
        self.assertEqual(carga.filas[0]["movimiento_id"], movimiento.pk)
        detalle = movimiento.detalles.get()
        self.assertEqual(detalle.numero_lote_registrado, "PAR-2026-A")
        self.assertEqual(detalle.unidad_registrada, "comprimido")
        self.assertFalse(StockAnexo.objects.exists())

    def test_varios_lotes_y_documentos_reutilizan_el_mismo_medicamento_en_bodega(self):
        carga = self.confirmar(self.preparar([
            self.datos(), self.datos(numero_lote="PAR-B", cantidad="20"),
            self.datos(documento_recepcion="GUIA-003", cantidad="15"),
        ]))
        self.assertEqual(Medicamento.objects.count(), 1)
        self.assertEqual(LoteMedicamento.objects.count(), 2)
        self.assertEqual(StockLote.objects.count(), 2)
        self.assertEqual(MovimientoFarmacia.objects.count(), 3)
        self.assertEqual(carga.cantidad_lotes_creados, 2)
        self.assertEqual(carga.cantidad_unidades, 75)

    def test_nueva_guia_suma_existencias_sin_modificar_la_ficha(self):
        self.confirmar()
        med = Medicamento.objects.get()
        creada_at = med.created_at
        segunda = self.preparar([self.datos(documento_recepcion="GUIA-002", cantidad="10")])
        self.assertTrue(segunda.puede_confirmar)
        segunda = self.confirmar(segunda)
        self.assertEqual(segunda.cantidad_importada, 0)
        self.assertEqual(segunda.cantidad_lotes_creados, 0)
        self.assertEqual(StockLote.objects.get().cantidad, 50)
        self.assertEqual(Medicamento.objects.get().created_at, creada_at)

    def test_otro_archivo_con_la_misma_recepcion_no_duplica_stock(self):
        carga = self.confirmar()
        self.confirmar(carga)
        repetida = self.preparar([self.datos(cantidad="99", proveedor=" ABASTECIMIENTO DE PRUEBA ")], nombre="otro-nombre.csv")
        self.assertFalse(repetida.puede_confirmar)
        self.assertIn("ya fue registrada", " ".join(repetida.filas[0]["errores"]))
        self.confirmar(repetida)
        self.assertEqual(StockLote.objects.get().cantidad, 40)
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)

    def test_duplicado_dentro_del_archivo_bloquea_la_carga_completa(self):
        carga = self.preparar([self.datos(), self.datos(cantidad="15")])
        self.assertEqual(carga.filas_con_error, 2)
        self.confirmar(carga)
        self.assertFalse(Medicamento.objects.exists())
        self.assertFalse(MovimientoFarmacia.objects.exists())

    def test_valida_fechas_ubicacion_cantidad_unidad_y_documento(self):
        cambios = [
            {"fecha_vencimiento": (timezone.localdate() - timedelta(days=1)).isoformat()},
            {"fecha_vencimiento": "10/12/2027"},
            {"fecha_recepcion": (timezone.localdate() + timedelta(days=1)).isoformat()},
            {"ubicacion_id": "999999"}, {"cantidad": "0"}, {"cantidad": "1.5"},
            {"unidad_medida": ""}, {"documento_recepcion": ""}, {"proveedor": ""},
        ]
        for cambio in cambios:
            with self.subTest(cambio=cambio):
                carga = self.preparar([self.datos(**cambio)])
                self.assertFalse(carga.puede_confirmar)
                self.confirmar(carga)
        self.assertFalse(Medicamento.objects.exists())
        self.assertFalse(MovimientoFarmacia.objects.exists())

    def test_no_acepta_cambiar_datos_de_medicamento_unidad_o_vencimiento_de_lote(self):
        self.confirmar()
        for cambio in ({"concentracion": "100 mg"}, {"unidad_medida": "caja"},
                       {"fecha_vencimiento": (timezone.localdate() + timedelta(days=120)).isoformat()}):
            with self.subTest(cambio=cambio):
                carga = self.preparar([self.datos(documento_recepcion="NUEVA", **cambio)])
                self.assertFalse(carga.puede_confirmar)
                self.confirmar(carga)
        self.assertEqual(StockLote.objects.get().cantidad, 40)
        self.assertEqual(Medicamento.objects.get().concentracion, "500 mg")

    def test_datos_inconsistentes_del_mismo_codigo_o_lote_en_csv_son_errores(self):
        for segundo in (self.datos(numero_lote="OTRO", concentracion="10 mg"),
                        self.datos(documento_recepcion="GUIA-002", fecha_vencimiento=(timezone.localdate() + timedelta(days=91)).isoformat())):
            carga = self.preparar([self.datos(), segundo])
            self.assertFalse(carga.puede_confirmar)
            self.confirmar(carga)
        self.assertFalse(Medicamento.objects.exists())

    def test_error_a_mitad_de_recepciones_revierte_catalogo_lotes_y_stock(self):
        carga = self.preparar([self.datos(), self.datos(numero_lote="OTRO")])
        llamadas = []
        def recibir(**kwargs):
            llamadas.append(1)
            if len(llamadas) == 2:
                raise ValidationError("Fallo simulado de recepción")
            return registrar_entrada_medicamento(**kwargs)
        with patch("farmacia.importacion.registrar_entrada_medicamento", side_effect=recibir):
            with self.assertRaises(ValidationError):
                self.confirmar(carga)
        for modelo in (Medicamento, Articulo, LoteMedicamento, StockLote, MovimientoFarmacia, DetalleMovimientoFarmacia):
            self.assertFalse(modelo.objects.exists())
        carga.refresh_from_db()
        self.assertEqual(carga.estado, "PENDIENTE")
        self.assertEqual(carga.cantidad_movimientos, 0)

    def test_conserva_datos_historicos_aunque_el_catalogo_cambie(self):
        self.confirmar()
        detalle = DetalleMovimientoFarmacia.objects.get()
        vencimiento = detalle.vencimiento_registrado
        LoteMedicamento.objects.filter(pk=detalle.lote_id).update(numero_lote="NUMERO-CORREGIDO", fecha_vencimiento=vencimiento + timedelta(days=5))
        Articulo.objects.filter(pk=detalle.lote.medicamento.articulo_id).update(nombre="Nombre corregido")
        detalle.refresh_from_db()
        self.assertEqual(detalle.numero_lote_registrado, "PAR-2026-A")
        self.assertEqual(detalle.nombre_registrado, "Paracetamol")
        self.assertEqual(detalle.vencimiento_registrado, vencimiento)
        self.assertEqual(detalle.unidad_registrada, "comprimido")
        response = self.client.get(reverse("farmacia:historial"))
        self.assertContains(response, "PAR-2026-A")
        self.assertContains(response, "GUIA-001")

    def test_catalogo_antiguo_con_stock_sin_unidad_requiere_verificacion_previa(self):
        categoria = CategoriaArticulo.objects.create(nombre="Medicamentos")
        med = Medicamento.objects.create(
            articulo=Articulo.objects.create(nombre="Paracetamol", codigo="PAR-001", categoria=categoria, stock_minimo=10),
            principio_activo="Paracetamol", concentracion="500 mg", forma_farmaceutica="Comprimido",
            presentacion="Caja de 20", requiere_receta=False,
        )
        lote = LoteMedicamento.objects.create(medicamento=med, numero_lote="ANTIGUO", fecha_vencimiento=timezone.localdate() + timedelta(days=90))
        StockLote.objects.create(lote=lote, anexo=self.norte, cantidad=5)
        carga = self.preparar()
        self.assertFalse(carga.puede_confirmar)
        self.assertIn("sin unidad definida", " ".join(carga.filas[0]["errores"]))
        med.unidad_medida = "comprimido"
        med.save()
        carga = self.confirmar(carga)
        self.assertEqual(carga.estado, "IMPORTADA")

    def test_flujo_web_muestra_vencimientos_y_permita_consultar_la_carga_en_historial(self):
        response = self.client.post(reverse("farmacia:importar_csv"), {"archivo": self.archivo()})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(StockLote.objects.exists())
        response = self.client.get(response.url)
        self.assertContains(response, "PAR-2026-A")
        self.assertContains(response, self.datos()["fecha_vencimiento"])
        self.assertContains(response, "Bodega Central")
        response = self.client.post(response.request["PATH_INFO"], {"revision": response.context["revision"]}, follow=True)
        self.assertContains(response, "Importación completada")
        carga = MovimientoFarmacia.objects.get().importacion
        response = self.client.get(reverse("farmacia:historial"), {"importacion": str(carga.pk)})
        self.assertEqual(response.context["pagina"].paginator.count, 1)
        self.assertContains(response, "recepciones.csv")
        self.assertContains(response, "Fila 2")

    def test_misma_recepcion_csv_y_manual_no_se_pueden_duplicar(self):
        self.confirmar()
        med = Medicamento.objects.get()
        lote = LoteMedicamento.objects.get()
        datos = {"medicamento": med.pk, "anexo": self.norte.pk, "lote": lote.pk,
                 "cantidad": 10, "proveedor": "Abastecimiento de prueba", "documento_recepcion": "GUIA-001",
                 "fecha_recepcion": timezone.localdate().isoformat()}
        response = self.client.post(reverse("farmacia:registrar_entrada"), datos)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ya fue registrada")
        self.assertEqual(StockLote.objects.get().cantidad, 40)
        datos.pop("proveedor")
        response = self.client.post(reverse("farmacia:registrar_entrada"), datos)
        self.assertIn("proveedor", response.context["form"].errors)
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)

    def test_plantilla_incluye_trazabilidad_y_rechaza_archivo_antiguo_sin_lote(self):
        response = self.client.get(reverse("farmacia:plantilla_csv"))
        encabezados = next(csv.reader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
        for columna in ("numero_lote", "fecha_vencimiento", "ubicacion_id", "cantidad", "unidad_medida", "proveedor", "documento_recepcion", "fecha_recepcion"):
            self.assertIn(columna, encabezados)
        antiguo = b"codigo;nombre;principio_activo;concentracion;forma_farmaceutica;requiere_receta;stock_minimo\nP;Paracetamol;Paracetamol;500 mg;Comprimido;no;10\n"
        response = self.client.post(reverse("farmacia:importar_csv"), {"archivo": SimpleUploadedFile("antiguo.csv", antiguo)})
        self.assertContains(response, "Faltan columnas")
        self.assertFalse(Medicamento.objects.exists())

    def test_compara_ids_y_minimos_como_numeros_y_no_como_texto(self):
        carga = self.preparar([
            self.datos(), self.datos(numero_lote="OTRO", ubicacion_id="0" + str(self.norte.pk), stock_minimo="010"),
        ])
        self.assertTrue(carga.puede_confirmar)
        carga = self.confirmar(carga)
        self.assertEqual(carga.cantidad_movimientos, 2)
        repetida = self.preparar([self.datos(documento_recepcion="GUIA-002"),
                                  self.datos(documento_recepcion="GUIA-002", ubicacion_id="0" + str(self.norte.pk))])
        self.assertEqual(repetida.filas_con_error, 2)


class ImportacionParcialTests(RecepcionCSVBase):
    def parcial(self, carga):
        return confirmar_importacion(importacion_id=carga.pk, usuario=self.usuario,
                                     omitir_errores=True, revision=token_revision(carga))

    def mixta(self):
        return self.preparar([self.datos(), self.datos(codigo="OTRO", nombre="Otro", cantidad="0")])

    def test_importa_solo_validas_audita_y_no_duplica_al_reintentar(self):
        carga = self.parcial(self.mixta())
        self.assertEqual(carga.estado, "IMPORTADA")
        self.assertEqual(carga.cantidad_movimientos, 1)
        self.assertEqual(carga.cantidad_omitidas, 1)
        self.assertEqual([f["resultado"] for f in carga.filas], ["IMPORTADA", "OMITIDA"])
        self.assertTrue(carga.filas[1]["errores"])
        self.assertEqual(StockLote.objects.get().cantidad, 40)
        self.parcial(carga)
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)

    def test_conflictos_excluyen_todas_las_filas_del_grupo(self):
        for indice, cambios in enumerate(({"concentracion": "10 mg"},
                        {"fecha_vencimiento": (timezone.localdate() + timedelta(days=95)).isoformat()})):
            carga = self.preparar([self.datos(), self.datos(documento_recepcion="GUIA-002", **cambios),
                                   self.datos(documento_recepcion="GUIA-003"),
                                   self.datos(codigo=f"VALIDO{indice}", nombre="Otro", numero_lote="OTRO")])
            self.assertEqual(carga.filas_con_error, 3)
            carga = self.parcial(carga)
            self.assertEqual(carga.cantidad_omitidas, 3)
            self.assertFalse(Medicamento.objects.filter(articulo__codigo="PAR-001").exists())

    def test_si_cambia_la_validacion_exige_nueva_confirmacion(self):
        carga = self.mixta()
        Anexo.objects.filter(pk=self.norte.pk).update(activo=False)
        carga = self.parcial(carga)
        self.assertEqual(carga.estado, "PENDIENTE")
        self.assertEqual(carga.filas_validas, 0)
        self.assertFalse(MovimientoFarmacia.objects.exists())

    def test_no_importa_filas_antes_invalidas_sin_revisarlas(self):
        Anexo.objects.filter(pk=self.norte.pk).update(activo=False)
        carga = self.mixta()
        Anexo.objects.filter(pk=self.norte.pk).update(activo=True)
        carga = self.parcial(carga)
        self.assertEqual(carga.estado, "PENDIENTE")
        self.assertEqual(carga.filas_validas, 1)
        self.assertFalse(MovimientoFarmacia.objects.exists())
        carga = self.parcial(carga)
        self.assertEqual(carga.cantidad_movimientos, 1)

    def test_error_en_subconjunto_revierte_todo(self):
        carga = self.preparar([self.datos(), self.datos(numero_lote="OTRO"),
                               self.datos(codigo="ERROR", cantidad="0")])
        llamadas = []
        def recibir(**kwargs):
            llamadas.append(1)
            if len(llamadas) == 2:
                raise ValidationError("Fallo de recepción")
            return registrar_entrada_medicamento(**kwargs)
        with patch("farmacia.importacion.registrar_entrada_medicamento", side_effect=recibir):
            with self.assertRaises(ValidationError):
                self.parcial(carga)
        self.assertFalse(Medicamento.objects.exists())
        self.assertFalse(MovimientoFarmacia.objects.exists())
        carga.refresh_from_db()
        self.assertEqual(carga.estado, "PENDIENTE")
        self.assertEqual(carga.cantidad_omitidas, 0)

    def test_web_exige_checkbox_revision_y_rechaza_token_de_otra_carga(self):
        carga = self.mixta()
        url = reverse("farmacia:revisar_importacion", args=[carga.pk])
        response = self.client.get(url)
        self.assertContains(response, "Importar 1 válidas y omitir 1")
        revision = response.context["revision"]
        for datos in ({"accion": "parcial", "revision": revision},
                      {"accion": "parcial", "aceptar_omisiones": "si"},
                      {"accion": "parcial", "aceptar_omisiones": "si", "revision": token_revision(self.mixta())}):
            self.client.post(url, datos)
            self.assertFalse(MovimientoFarmacia.objects.exists())
        response = self.client.post(url, {"accion": "parcial", "aceptar_omisiones": "si", "revision": revision}, follow=True)
        self.assertContains(response, "1 filas omitidas")
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)

    def test_reporte_preserva_errores_auditoria_y_escapa_formulas(self):
        carga = self.parcial(self.preparar([self.datos(), self.datos(codigo="=1+1", cantidad="0", nombre="@SUM(1)")]))
        response = self.client.get(reverse("farmacia:reporte_importacion", args=[carga.pk]))
        self.assertEqual(response.status_code, 200)
        filas = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(filas[0]["resultado"], "IMPORTADA")
        self.assertEqual(filas[1]["resultado"], "OMITIDA")
        self.assertEqual(filas[1]["codigo"], "'=1+1")
        self.assertTrue(filas[1]["motivos"])
        self.assertTrue(filas[1]["que_revisar"])
        otro = get_user_model().objects.create_superuser(email="otro-reporte@example.com")
        self.client.force_login(otro)
        self.assertEqual(self.client.get(reverse("farmacia:reporte_importacion", args=[carga.pk])).status_code, 404)

    def test_no_permite_parcial_sin_revision_ni_importar_cero_validas(self):
        carga = self.mixta()
        with self.assertRaises(ValidationError):
            confirmar_importacion(importacion_id=carga.pk, usuario=self.usuario, omitir_errores=True)
        carga = self.parcial(self.preparar([self.datos(cantidad="0")]))
        self.assertEqual(carga.estado, "PENDIENTE")
        self.assertFalse(MovimientoFarmacia.objects.exists())

    def test_corrige_solo_omitidas_en_otra_carga(self):
        carga = self.parcial(self.mixta())
        corregida = self.confirmar(self.preparar([self.datos(codigo="OTRO", nombre="Otro", cantidad="5")]))
        self.assertEqual(corregida.cantidad_movimientos, 1)
        self.assertEqual(MovimientoFarmacia.objects.count(), 2)
        carga.refresh_from_db()
        self.assertEqual(carga.cantidad_omitidas, 1)
        self.assertEqual(carga.filas[1]["resultado"], "OMITIDA")

    def test_filtrar_errores_no_cambia_el_resumen_ni_el_token(self):
        carga = self.mixta()
        url = reverse("farmacia:revisar_importacion", args=[carga.pk])
        response = self.client.get(url, {"filas": "errores"})
        self.assertEqual(response.context["pagina"].paginator.count, 1)
        self.assertEqual(response.context["pagina"][0]["numero"], 3)
        self.assertEqual(response.context["carga"].total_filas, 2)
        carga.refresh_from_db()
        self.assertNotIn("que_revisar", carga.filas[1])
        response = self.client.post(url, {"accion": "parcial", "aceptar_omisiones": "si",
                                          "revision": response.context["revision"]}, follow=True)
        self.assertContains(response, "Importación completada")
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)


class RegistroManualMedicamentoTests(RecepcionCSVBase):
    def test_formulario_incluye_datos_de_csv_y_ubicaciones_activas(self):
        Anexo.objects.create(nombre="Ubicación cerrada", activo=False)
        response = self.client.get(reverse("farmacia:crear_medicamento"))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFalse(form.is_bound)
        self.assertTrue(set(COLUMNAS).issubset(form.fields))
        self.assertContains(response, "Lote y existencias iniciales")
        self.assertContains(response, "Respaldo de la recepción")
        self.assertNotContains(response, "Ubicación cerrada")

    def test_registro_manual_crea_ficha_lote_stock_y_movimiento(self):
        response = self.client.post(reverse("farmacia:crear_medicamento"), self.datos())
        self.assertEqual(response.status_code, 302)
        med = Medicamento.objects.get()
        movimiento = MovimientoFarmacia.objects.get()
        self.assertEqual(StockLote.objects.get().cantidad, 40)
        self.assertEqual(LoteMedicamento.objects.get().numero_lote, "PAR-2026-A")
        self.assertEqual(movimiento.proveedor, "Abastecimiento de prueba")
        self.assertEqual(movimiento.documento_recepcion, "GUIA-001")
        self.assertEqual(movimiento.fecha_recepcion, timezone.localdate())
        self.assertEqual(movimiento.usuario_id, self.usuario.pk)
        self.assertIsNone(movimiento.importacion_id)
        self.assertEqual(movimiento.detalles.get().unidad_registrada, "comprimido")
        self.assertEqual(med.articulo.codigo, "PAR-001")
        # El alta manual también impide repetir después la recepción por CSV.
        self.assertFalse(self.preparar().puede_confirmar)

    def test_campos_invalidos_no_crean_datos(self):
        for cambio in ({"numero_lote": ""}, {"fecha_vencimiento": ""}, {"cantidad": "0"},
                       {"fecha_vencimiento": (timezone.localdate() - timedelta(days=1)).isoformat()},
                       {"fecha_recepcion": (timezone.localdate() + timedelta(days=1)).isoformat()},
                       {"unidad_medida": ""}, {"proveedor": ""},
                       {"documento_recepcion": ""}, {"codigo": ""}):
            with self.subTest(cambio=cambio):
                response = self.client.post(reverse("farmacia:crear_medicamento"), self.datos(**cambio))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertFalse(Medicamento.objects.exists())
                self.assertFalse(StockLote.objects.exists())
                self.assertFalse(MovimientoFarmacia.objects.exists())

    def test_error_al_recibir_revierte_alta_completa(self):
        with patch("farmacia.registro.registrar_entrada_medicamento", side_effect=ValidationError("Fallo simulado")):
            response = self.client.post(reverse("farmacia:crear_medicamento"), self.datos())
        self.assertContains(response, "Fallo simulado")
        for modelo in (Articulo, Medicamento, LoteMedicamento, StockLote, MovimientoFarmacia):
            self.assertFalse(modelo.objects.exists())

    def test_codigo_existente_no_modifica_ficha_ni_duplica_stock(self):
        self.confirmar()
        response = self.client.post(reverse("farmacia:crear_medicamento"), self.datos(nombre="Nombre equivocado"))
        self.assertContains(response, "El código ya existe")
        self.assertEqual(Medicamento.objects.count(), 1)
        self.assertEqual(Medicamento.objects.get().articulo.nombre, "Paracetamol")
        self.assertEqual(StockLote.objects.get().cantidad, 40)
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)

    def test_editar_ficha_no_genera_recepcion(self):
        self.confirmar()
        med = Medicamento.objects.get()
        url = reverse("farmacia:editar_medicamento", args=[med.pk])
        response = self.client.get(url)
        self.assertNotIn("numero_lote", response.context["form"].fields)
        response = self.client.post(url, self.datos(descripcion="Descripción actualizada", requiere_receta=""))
        self.assertEqual(response.status_code, 302)
        med.refresh_from_db()
        self.assertEqual(med.articulo.descripcion, "Descripción actualizada")
        self.assertEqual(StockLote.objects.get().cantidad, 40)
        self.assertEqual(MovimientoFarmacia.objects.count(), 1)
