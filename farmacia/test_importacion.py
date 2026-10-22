import csv
import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventario.models import Articulo, CategoriaArticulo, StockAnexo
from .importacion import COLUMNAS_CATALOGO as COLUMNAS, COLUMNAS_OPCIONALES, MAX_BYTES, MedicamentoCSVForm, preparar_importacion, confirmar_importacion
from .models import ImportacionMedicamentos, Medicamento, LoteMedicamento, StockLote, MovimientoFarmacia


class ImportacionMedicamentosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_user(email="farmacia-csv@example.com")
        grupo, _ = Group.objects.get_or_create(name="Encargado de Farmacia")
        cls.usuario.groups.add(grupo)
        cls.otro = get_user_model().objects.create_superuser(email="otro-csv@example.com")
        cls.funcionario = get_user_model().objects.create_user(email="funcionario-csv@example.com")

    def setUp(self):
        self.client.force_login(self.usuario)

    def datos(self, **cambios):
        return dict(codigo="PAR-001", nombre="Paracetamol", principio_activo="Paracetamol",
                    concentracion="500 mg", forma_farmaceutica="Comprimido", presentacion="Caja de 20",
                    requiere_receta="no", stock_minimo="10", descripcion="Medicamento de prueba") | cambios

    def archivo(self, filas=None, separador=";", columnas=None, bom=True):
        columnas = columnas or [*COLUMNAS, *COLUMNAS_OPCIONALES]
        salida = io.StringIO(newline="")
        writer = csv.writer(salida, delimiter=separador)
        writer.writerow(columnas)
        for fila in filas if filas is not None else [self.datos()]:
            writer.writerow([fila.get(columna, "") for columna in columnas])
        return SimpleUploadedFile("medicamentos.csv", salida.getvalue().encode("utf-8-sig" if bom else "utf-8"), content_type="text/csv")

    def preparar(self, **kwargs):
        return preparar_importacion(archivo=self.archivo(**kwargs), usuario=self.usuario, modalidad="CATALOGO")

    def test_vista_previa_no_crea_medicamentos_ni_stock(self):
        carga = self.preparar()
        self.assertTrue(carga.puede_confirmar)
        self.assertEqual(carga.filas_validas, 1)
        self.assertEqual(carga.filas[0]["numero"], 2)
        self.assertFalse(Articulo.objects.exists())
        self.assertFalse(Medicamento.objects.exists())
        self.assertFalse(StockLote.objects.exists())

    def test_confirmar_crea_catalogo_y_auditoria_sin_tocar_existencias(self):
        carga = confirmar_importacion(importacion_id=self.preparar().pk, usuario=self.usuario)
        medicamento = Medicamento.objects.select_related("articulo").get()
        self.assertEqual(medicamento.articulo.codigo, "PAR-001")
        self.assertEqual(medicamento.articulo.categoria.nombre, "Medicamentos")
        self.assertEqual(medicamento.articulo.stock_minimo, 10)
        self.assertFalse(medicamento.requiere_receta)
        self.assertEqual(carga.estado, "IMPORTADA")
        self.assertEqual(carga.cantidad_importada, 1)
        self.assertEqual(carga.usuario_id, self.usuario.pk)
        self.assertIsNotNone(carga.importada_at)
        self.assertEqual(len(carga.archivo_sha256), 64)
        for modelo in (StockAnexo, StockLote, LoteMedicamento, MovimientoFarmacia):
            self.assertFalse(modelo.objects.exists())

    def test_acepta_coma_utf8_sin_bom_y_campos_entre_comillas(self):
        carga = self.preparar(separador=",", bom=False, filas=[self.datos(nombre="Ácido, solución", descripcion='Texto con "comillas"\ny salto')])
        self.assertTrue(carga.puede_confirmar)
        self.assertEqual(carga.filas[0]["datos"]["nombre"], "Ácido, solución")

    def test_acepta_encabezados_con_espacios_y_mayusculas(self):
        contenido = self.archivo().read().decode("utf-8-sig")
        contenido = contenido.replace("codigo;nombre;", " CODIGO ; NOMBRE ;", 1)
        carga = preparar_importacion(archivo=SimpleUploadedFile("archivo.csv", contenido.encode()), usuario=self.usuario, modalidad="CATALOGO")
        self.assertTrue(carga.puede_confirmar)

    def test_codigos_repetidos_en_csv_marcan_ambas_filas_y_bloquean_toda_la_carga(self):
        carga = self.preparar(filas=[self.datos(), self.datos(codigo="par-001")])
        self.assertEqual(carga.filas_con_error, 2)
        self.assertFalse(carga.puede_confirmar)
        confirmar_importacion(importacion_id=carga.pk, usuario=self.usuario)
        self.assertFalse(Medicamento.objects.exists())

    def test_codigo_existente_en_inventario_no_se_sobrescribe(self):
        categoria = CategoriaArticulo.objects.create(nombre="General")
        articulo = Articulo.objects.create(nombre="Original", codigo="par-001", categoria=categoria)
        carga = self.preparar()
        self.assertEqual(carga.filas_con_error, 1)
        confirmar_importacion(importacion_id=carga.pk, usuario=self.usuario)
        articulo.refresh_from_db()
        self.assertEqual(articulo.nombre, "Original")
        self.assertFalse(Medicamento.objects.exists())

    def test_datos_invalidos_reportan_error_por_fila(self):
        carga = self.preparar(filas=[self.datos(codigo="", requiere_receta="quizas", stock_minimo="-2", nombre=""), self.datos(codigo="LARGO", concentracion="a" * 101)])
        self.assertEqual(carga.filas_con_error, 2)
        self.assertIn("Requiere receta", " ".join(carga.filas[0]["errores"]))
        self.assertIn("Stock mínimo", " ".join(carga.filas[0]["errores"]))
        self.assertFalse(carga.puede_confirmar)

    def test_columnas_opcionales_pueden_omitirse(self):
        carga = self.preparar(columnas=[columna for columna in COLUMNAS if columna != "presentacion"])
        self.assertTrue(carga.puede_confirmar)
        confirmar_importacion(importacion_id=carga.pk, usuario=self.usuario)
        self.assertEqual(Medicamento.objects.get().presentacion, "")

    def test_archivos_sin_datos_o_encabezados_invalidos_se_rechazan(self):
        for contenido in (b"", self.archivo(filas=[]).read(), b"codigo;codigo\nA;B\n", b"codigo;nombre\nA;Prueba\n", b"\xff\xfe\xfa"):
            with self.subTest(contenido=contenido[:30]):
                with self.assertRaises(ValidationError):
                    preparar_importacion(archivo=SimpleUploadedFile("error.csv", contenido), usuario=self.usuario, modalidad="CATALOGO")
        self.assertFalse(ImportacionMedicamentos.objects.exists())

    def test_columna_de_stock_no_se_ignora_silenciosamente(self):
        columnas = [*COLUMNAS, "cantidad"]
        with self.assertRaisesMessage(ValidationError, "plantilla actualizada"):
            self.preparar(columnas=columnas)

    def test_fila_con_columnas_de_mas_no_puede_importarse(self):
        contenido = self.archivo().read() + b"OTRO;Nombre;Principio;10;Comprimido;Caja;no;0;Desc;EXTRA;EXTRA2\r\n"
        carga = preparar_importacion(archivo=SimpleUploadedFile("error.csv", contenido), usuario=self.usuario, modalidad="CATALOGO")
        self.assertEqual(carga.filas_con_error, 1)
        confirmar_importacion(importacion_id=carga.pk, usuario=self.usuario)
        self.assertFalse(Medicamento.objects.exists())

    def test_limites_de_tamano_y_cantidad(self):
        with self.assertRaisesMessage(ValidationError, "2 MB"):
            preparar_importacion(archivo=SimpleUploadedFile("grande.csv", b"x" * (MAX_BYTES + 1)), usuario=self.usuario, modalidad="CATALOGO")
        with self.assertRaisesMessage(ValidationError, "1.000"):
            self.preparar(filas=[self.datos(codigo=f"COD-{n}") for n in range(1001)])

    def test_doble_confirmacion_es_idempotente_y_recargar_archivo_no_duplica(self):
        carga = self.preparar()
        confirmar_importacion(importacion_id=carga.pk, usuario=self.usuario)
        confirmar_importacion(importacion_id=carga.pk, usuario=self.usuario)
        self.assertEqual(Medicamento.objects.count(), 1)
        repetida = self.preparar()
        self.assertEqual(repetida.filas_con_error, 1)
        confirmar_importacion(importacion_id=repetida.pk, usuario=self.usuario)
        self.assertEqual(Medicamento.objects.count(), 1)

    def test_revalida_si_aparece_un_codigo_antes_de_confirmar(self):
        carga = self.preparar(filas=[self.datos(), self.datos(codigo="NUEVO")])
        categoria = CategoriaArticulo.objects.create(nombre="General")
        Articulo.objects.create(nombre="Creado después", codigo="PAR-001", categoria=categoria)
        carga = confirmar_importacion(importacion_id=carga.pk, usuario=self.usuario)
        self.assertEqual(carga.estado, "PENDIENTE")
        self.assertEqual(carga.filas_con_error, 1)
        self.assertFalse(Medicamento.objects.exists())
        self.assertFalse(Articulo.objects.filter(codigo="NUEVO").exists())

    def test_error_a_mitad_de_carga_revierte_todos_los_registros(self):
        carga = self.preparar(filas=[self.datos(), self.datos(codigo="OTRO")])
        original = MedicamentoCSVForm.save
        llamadas = []
        def guardar(form):
            llamadas.append(1)
            if len(llamadas) == 2:
                raise IntegrityError("Conflicto simulado")
            return original(form)
        with patch.object(MedicamentoCSVForm, "save", guardar):
            with self.assertRaises(IntegrityError):
                confirmar_importacion(importacion_id=carga.pk, usuario=self.usuario)
        self.assertFalse(Articulo.objects.exists())
        self.assertFalse(Medicamento.objects.exists())
        carga.refresh_from_db()
        self.assertEqual(carga.estado, "PENDIENTE")
        self.assertEqual(carga.cantidad_importada, 0)

    def test_caducidad_y_propietario_de_la_vista_previa(self):
        carga = self.preparar()
        self.client.force_login(self.otro)
        url = reverse("farmacia:revisar_importacion", args=[carga.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 404)
        ImportacionMedicamentos.objects.filter(pk=carga.pk).update(creada_at=timezone.now() - timedelta(minutes=31))
        with self.assertRaisesMessage(ValidationError, "caducó"):
            confirmar_importacion(importacion_id=carga.pk, usuario=self.usuario)
        self.assertFalse(Medicamento.objects.exists())

    def test_flujo_web_descarga_subida_revision_y_confirmacion(self):
        response = self.client.get(reverse("farmacia:plantilla_csv"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(len(response.content.decode("utf-8-sig").splitlines()), 1)
        carga = self.preparar()
        response = self.client.get(reverse("farmacia:revisar_importacion", args=[carga.pk]))
        url = reverse("farmacia:revisar_importacion", args=[carga.pk])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Medicamento.objects.exists())
        self.assertContains(self.client.get(url), "Confirmar importación")
        response = self.client.post(url, {"revision": response.context["revision"]}, follow=True)
        self.assertContains(response, "Importación completada")
        self.assertEqual(Medicamento.objects.count(), 1)
        self.assertContains(self.client.get(reverse("farmacia:importar_csv")), "Mis últimas importaciones")

    def test_vista_previa_con_error_oculta_confirmacion(self):
        carga = self.preparar(filas=[self.datos(stock_minimo="-1")])
        response = self.client.get(reverse("farmacia:revisar_importacion", args=[carga.pk]))
        self.assertContains(response, "Fila 2")
        self.assertContains(response, "No hay filas válidas para importar")
        self.assertNotContains(response, ">Confirmar importación</button>")

    def test_permisos_y_extension_del_archivo(self):
        response = self.client.post(reverse("farmacia:importar_csv"), {"archivo": SimpleUploadedFile("error.txt", b"no csv")})
        self.assertContains(response, "extensión .csv")
        self.assertFalse(ImportacionMedicamentos.objects.exists())
        carga = self.preparar()
        self.client.force_login(self.funcionario)
        for ruta in (reverse("farmacia:importar_csv"), reverse("farmacia:plantilla_csv"), reverse("farmacia:revisar_importacion", args=[carga.pk])):
            self.assertEqual(self.client.get(ruta).status_code, 403)
            self.assertEqual(self.client.post(ruta).status_code, 403)
        with self.assertRaisesMessage(ValidationError, "permisos"):
            confirmar_importacion(importacion_id=carga.pk, usuario=self.funcionario)
