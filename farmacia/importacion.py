"""Importación con trazabilidad: catálogo, lote, recepción y existencias."""
import csv
import hashlib
import io
import json
from pathlib import PurePath

from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from usuarios.utils import puede_gestionar_farmacia
from .forms import MedicamentoForm
from .models import ImportacionMedicamentos, Medicamento, LoteMedicamento, StockLote, MovimientoFarmacia
from .services import registrar_entrada_medicamento
from .trazabilidad import normalizar, clave_de_recepcion
from .bodega import exigir_bodega_central
from organizacion.models import Anexo
from inventario.models import Articulo

MAX_BYTES = 2 * 1024 * 1024
MAX_FILAS = 1000
COLUMNAS_CATALOGO = (
    "codigo", "nombre", "principio_activo", "concentracion", "forma_farmaceutica",
    "presentacion", "requiere_receta", "stock_minimo",
)
COLUMNAS = (*COLUMNAS_CATALOGO, "unidad_medida", "numero_lote", "fecha_vencimiento", "ubicacion_id",
            "cantidad", "proveedor", "documento_recepcion", "fecha_recepcion")
COLUMNAS_OPCIONALES = ("descripcion", "observaciones")


class ArchivoMedicamentosForm(forms.Form):
    archivo = forms.FileField(
        label="Archivo CSV", widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}),
        help_text="CSV UTF-8, separado por coma o punto y coma. Máximo 2 MB y 1.000 filas de recepción.",
    )

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        if not archivo.name.lower().endswith(".csv"):
            raise forms.ValidationError("Seleccione un archivo con extensión .csv.")
        if archivo.size > MAX_BYTES:
            raise forms.ValidationError("El archivo supera el máximo de 2 MB.")
        return archivo


class MedicamentoCSVForm(MedicamentoForm):
    unidad_medida = forms.CharField(max_length=50, required=False, label="Unidad de medida")
    codigo = forms.CharField(max_length=50, required=True, label="Código")
    requiere_receta = forms.TypedChoiceField(
        choices=[("si", "Sí"), ("no", "No")], coerce=lambda valor: valor == "si",
        label="Requiere receta",
    )
    stock_minimo = forms.IntegerField(min_value=0, max_value=2147483647, label="Stock mínimo")

    def clean_codigo(self):
        codigo = self.cleaned_data["codigo"].strip()
        if Articulo.objects.filter(codigo__iexact=codigo).exists():
            raise forms.ValidationError(f"El código {codigo} ya existe en el catálogo.")
        return codigo


class RecepcionCSVForm(MedicamentoCSVForm):
    unidad_medida = forms.CharField(max_length=50, label="Unidad de medida")
    numero_lote = forms.CharField(max_length=100, label="Número de lote")
    fecha_vencimiento = forms.DateField(input_formats=["%Y-%m-%d"], label="Vencimiento (AAAA-MM-DD)")
    ubicacion_id = forms.IntegerField(min_value=1, max_value=9223372036854775807, label="Ubicación de destino")
    cantidad = forms.IntegerField(min_value=1, max_value=2147483647, label="Cantidad recibida")
    proveedor = forms.CharField(max_length=150, label="Proveedor / origen de abastecimiento")
    documento_recepcion = forms.CharField(max_length=100, label="Documento de recepción")
    fecha_recepcion = forms.DateField(input_formats=["%Y-%m-%d"], label="Fecha de recepción (AAAA-MM-DD)")
    observaciones = forms.CharField(required=False, label="Observaciones")

    def __init__(self, data=None, *args, **kwargs):
        coincidencias = list(Articulo.objects.filter(codigo__iexact=str((data or {}).get("codigo", "")).strip())[:2])
        self.articulo_existente = coincidencias[0] if len(coincidencias) == 1 else None
        self.codigo_ambiguo = len(coincidencias) > 1
        existente = Medicamento.objects.select_related("articulo").filter(articulo=self.articulo_existente).first() if self.articulo_existente else None
        super().__init__(data, *args, medicamento=existente, **kwargs)

    def clean_codigo(self):
        codigo = self.cleaned_data["codigo"].strip()
        if self.codigo_ambiguo:
            raise ValidationError("El código identifica más de un artículo. Corrija el catálogo antes de importar.")
        if self.articulo_existente and not self.medicamento:
            if self.articulo_existente.saldos_pendientes_farmacia.exists():
                raise ValidationError("El código tiene saldos pendientes de conciliación en Farmacia. Verifique primero la identidad del medicamento y su ficha.")
            raise ValidationError("El código pertenece al inventario general y no a un medicamento.")
        return self.articulo_existente.codigo if self.articulo_existente else codigo

    def clean_ubicacion_id(self):
        ubicacion = Anexo.objects.filter(pk=self.cleaned_data["ubicacion_id"], activo=True).first()
        if not ubicacion:
            raise ValidationError("La ubicación no existe o está inactiva. Revise los identificadores disponibles.")
        central = exigir_bodega_central()
        if ubicacion.pk != central.pk:
            raise ValidationError(f"Las recepciones deben ingresar a Bodega Central (ubicacion_id={central.pk}). Después registre un traslado al anexo; no se cambia el destino del CSV automáticamente.")
        self.ubicacion = ubicacion
        return ubicacion.pk

    def clean_fecha_vencimiento(self):
        fecha = self.cleaned_data["fecha_vencimiento"]
        if fecha < timezone.localdate():
            raise ValidationError("No se reciben lotes vencidos.")
        return fecha

    def clean_fecha_recepcion(self):
        fecha = self.cleaned_data["fecha_recepcion"]
        if fecha > timezone.localdate():
            raise ValidationError("La recepción no puede tener una fecha futura.")
        return fecha

    def clean(self):
        datos = super().clean()
        med = self.medicamento
        lote_existente = None
        if med:
            if not med.activo or not med.articulo.activo:
                self.add_error("codigo", "El medicamento está inactivo.")
            valores = {"nombre": med.articulo.nombre, "principio_activo": med.principio_activo,
                       "concentracion": med.concentracion, "forma_farmaceutica": med.forma_farmaceutica,
                       "presentacion": med.presentacion, "requiere_receta": med.requiere_receta,
                       "stock_minimo": med.articulo.stock_minimo}
            for campo, existente in valores.items():
                if campo in datos and normalizar(datos[campo]) != normalizar(existente):
                    self.add_error(campo, "No coincide con la ficha del medicamento existente. La recepción no modifica el catálogo.")
            if "unidad_medida" in datos:
                if med.unidad_medida and normalizar(datos["unidad_medida"]) != normalizar(med.unidad_medida):
                    self.add_error("unidad_medida", "No coincide con la unidad de stock del medicamento. No se convierten cajas, comprimidos u otras unidades automáticamente.")
                elif not med.unidad_medida and StockLote.objects.filter(lote__medicamento=med, cantidad__gt=0).exists():
                    self.add_error("unidad_medida", "El medicamento tiene stock sin unidad definida. Complete primero su ficha verificando la unidad de esas existencias.")
            if datos.get("numero_lote"):
                lotes = list(LoteMedicamento.objects.filter(medicamento=med, numero_lote__iexact=datos["numero_lote"])[:2])
                if len(lotes) > 1:
                    self.add_error("numero_lote", "Hay lotes ambiguos en el catálogo. Deben regularizarse antes de recibir unidades.")
                elif lotes:
                    lote = lotes[0]
                    lote_existente = lote
                    datos["numero_lote"] = lote.numero_lote
                    if not lote.activo:
                        self.add_error("numero_lote", "El lote está inactivo.")
                    if datos.get("fecha_vencimiento") and datos["fecha_vencimiento"] != lote.fecha_vencimiento:
                        self.add_error("fecha_vencimiento", "El lote ya existe con otra fecha de vencimiento.")
        if med and lote_existente and all(campo in datos for campo in ("proveedor", "documento_recepcion", "ubicacion_id")):
            clave = clave_de_recepcion(
                proveedor=datos["proveedor"], documento=datos["documento_recepcion"],
                medicamento_id=med.pk, lote_id=lote_existente.pk, ubicacion_id=datos["ubicacion_id"],
            )
            if MovimientoFarmacia.objects.filter(clave_recepcion=clave).exists():
                self.add_error("documento_recepcion", "Esta recepción ya fue registrada para el proveedor, documento, medicamento, lote y ubicación indicados.")
        return datos


def validar_filas(filas, modalidad="CATALOGO"):
    resultado, formularios, vistos = [], [], {}
    catalogos, lotes = {}, {}
    for fila in filas:
        datos = {clave: str(valor).strip() for clave, valor in fila["datos"].items()}
        valor = datos.get("requiere_receta", "").lower()
        datos["requiere_receta"] = {"sí": "si", "true": "si", "1": "si", "false": "no", "0": "no"}.get(valor, valor)
        form = RecepcionCSVForm(datos) if modalidad == "RECEPCION" else MedicamentoCSVForm(datos)
        errores = list(fila.get("errores_estructura", []))
        if not form.is_valid():
            for campo, mensajes in form.errors.items():
                etiqueta = form[campo].label if campo in form.fields else "Datos"
                errores.extend(f"{etiqueta}: {mensaje}" for mensaje in mensajes)
        actual = {"numero": fila["numero"], "datos": datos, "errores": errores,
                  "errores_estructura": list(fila.get("errores_estructura", []))}
        if getattr(form, "ubicacion", None):
            actual["ubicacion_nombre"] = form.ubicacion.nombre
        comparables = {**datos, **form.cleaned_data}
        codigo = normalizar(comparables.get("codigo", ""))
        if modalidad == "RECEPCION":
            identidad = tuple(normalizar(comparables.get(campo, "")) for campo in
                              ("proveedor", "documento_recepcion", "codigo", "numero_lote", "ubicacion_id"))
            catalogo = tuple(normalizar(comparables.get(campo, "")) for campo in
                             ("nombre", "principio_activo", "concentracion", "forma_farmaceutica", "presentacion", "requiere_receta", "stock_minimo", "unidad_medida"))
            catalogos.setdefault(codigo, []).append((catalogo, actual))
            lote_id = (codigo, normalizar(comparables.get("numero_lote", "")))
            vencimiento = str(comparables.get("fecha_vencimiento", ""))
            lotes.setdefault(lote_id, []).append((vencimiento, actual))
        else:
            identidad = codigo
        if identidad and identidad in vistos:
            anterior = resultado[vistos[identidad]]
            actual["errores"].append(f"Registro repetido en el archivo (también en fila {anterior['numero']}). Agrupe las cantidades de una misma recepción.")
            anterior["errores"].append(f"Registro repetido en el archivo (también en fila {fila['numero']}).")
        else:
            vistos[identidad] = len(resultado)
        resultado.append(actual)
        formularios.append(form)
    # Excluir todo el grupo ambiguo, incluida la primera fila y las coincidencias posteriores.
    for grupos, mensaje in (
        (catalogos, "El mismo código tiene datos de medicamento o unidad diferentes dentro del archivo. Revise todas sus filas."),
        (lotes, "El mismo lote tiene vencimientos diferentes dentro del archivo. Verifique el vencimiento en el respaldo del lote."),
    ):
        for grupo in grupos.values():
            if len({valor for valor, _ in grupo}) > 1:
                for _, fila in grupo:
                    fila["errores"].append(mensaje)
    return resultado, formularios


def revision_importacion(carga, filas=None):
    contenido = json.dumps(filas if filas is not None else carga.filas, sort_keys=True, ensure_ascii=False)
    return {"carga": str(carga.pk), "huella": hashlib.sha256(contenido.encode()).hexdigest()}


def token_revision(carga):
    return signing.dumps(revision_importacion(carga), salt="farmacia.revision")


def que_revisar(fila):
    if not fila["errores"]:
        return "Verifique medicamento, unidad, lote, vencimiento, destino, cantidad y documento contra el respaldo original."
    errores = normalizar(" ".join(fila["errores"]))
    consejos = []
    if "ficha" in errores or "código" in errores or "mismo código" in errores:
        consejos.append("Busque el código en el catálogo y compare la identidad y los campos indicados con la ficha vigente; no la modifique para forzar la carga.")
    if "unidad" in errores:
        consejos.append("Verifique la unidad real del stock; no mezcle cajas, comprimidos o frascos sin una conversión verificada.")
    if "lote" in errores or "vencimiento" in errores:
        consejos.append("Compare lote y vencimiento con el envase y su respaldo; todas las filas del mismo lote deben coincidir.")
    if "ubicación" in errores:
        consejos.append("Compruebe el ID de una ubicación activa en la lista de destinos disponibles.")
    if "cantidad" in errores:
        consejos.append("Revise la cantidad recibida: debe ser un entero positivo expresado en la unidad de stock.")
    if "repetido" in errores or "ya fue registrada" in errores:
        consejos.append("Consulte el historial: no vuelva a cargar una recepción registrada; agrupe en una fila las cantidades de una misma recepción pendiente.")
    if "recepción" in errores or "proveedor" in errores:
        consejos.append("Compruebe proveedor, documento y fecha real contra el respaldo original.")
    return " ".join(consejos) or "Corrija los campos indicados y verifique sus valores contra la ficha y el documento de recepción original."



def leer_csv(archivo, modalidad="RECEPCION"):
    contenido = archivo.read(MAX_BYTES + 1)
    if len(contenido) > MAX_BYTES:
        raise ValidationError("El archivo supera el máximo de 2 MB.")
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationError("No se pudo leer el archivo. Guárdelo como CSV UTF-8 e inténtelo de nuevo.")
    if not texto.strip() or "\x00" in texto:
        raise ValidationError("El archivo está vacío o contiene caracteres no válidos.")
    try:
        separador = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=",;").delimiter
        lector = csv.reader(io.StringIO(texto, newline=""), delimiter=separador, strict=True)
        cabeceras = [col.strip().lower() for col in next(lector)]
        if len(set(cabeceras)) != len(cabeceras):
            raise ValidationError("El archivo contiene encabezados repetidos.")
        columnas = COLUMNAS if modalidad == "RECEPCION" else COLUMNAS_CATALOGO
        faltantes = set(columnas) - {"presentacion"} - set(cabeceras)
        desconocidas = set(cabeceras) - set(columnas) - set(COLUMNAS_OPCIONALES)
        if faltantes:
            raise ValidationError("Faltan columnas: " + ", ".join(sorted(faltantes)) + ". Descargue la plantilla.")
        if desconocidas:
            raise ValidationError("Columnas no reconocidas: " + ", ".join(sorted(desconocidas)) + ". Descargue la plantilla actualizada.")
        filas = []
        while True:
            numero = lector.line_num + 1
            try:
                valores = next(lector)
            except StopIteration:
                break
            if not any(valor.strip() for valor in valores):
                continue
            if len(filas) >= MAX_FILAS:
                raise ValidationError("El archivo supera el máximo de 1.000 medicamentos.")
            errores = [] if len(valores) == len(cabeceras) else ["La cantidad de columnas no coincide con los encabezados."]
            filas.append({"numero": numero, "datos": dict(zip(cabeceras, valores)), "errores_estructura": errores})
    except (csv.Error, StopIteration):
        raise ValidationError("El formato del CSV no es válido. Revise el separador, las comillas y los encabezados.")
    if not filas:
        raise ValidationError("El archivo solo contiene encabezados. Agregue al menos un medicamento.")
    filas, _ = validar_filas(filas, modalidad)
    return filas, hashlib.sha256(contenido).hexdigest()


def preparar_importacion(*, archivo, usuario, modalidad="RECEPCION"):
    if not puede_gestionar_farmacia(usuario):
        raise ValidationError("No tienes permisos para importar medicamentos.")
    if modalidad not in ("RECEPCION", "CATALOGO"):
        raise ValidationError("Modalidad de importación no válida.")
    filas, huella = leer_csv(archivo, modalidad)
    return ImportacionMedicamentos.objects.create(
        usuario=usuario, nombre_archivo=PurePath(archivo.name).name[:255],
        archivo_sha256=huella, filas=filas, modalidad=modalidad,
    )


@transaction.atomic
def confirmar_importacion(*, importacion_id, usuario, omitir_errores=False, revision=None):
    if not puede_gestionar_farmacia(usuario):
        raise ValidationError("No tienes permisos para importar medicamentos.")
    carga = ImportacionMedicamentos.objects.select_for_update().get(pk=importacion_id, usuario=usuario)
    if carga.estado == "IMPORTADA":
        return carga  # Reintentos y doble clic no vuelven a crear medicamentos.
    if carga.caducada:
        raise ValidationError("La vista previa caducó. Vuelva a cargar el archivo para revisarlo.")
    if not carga.filas:
        raise ValidationError("La carga no contiene medicamentos.")
    if omitir_errores and not revision:
        raise ValidationError("Debe revisar y confirmar explícitamente las filas que se omitirán.")
    if revision is not None:
        try:
            revisada = signing.loads(revision, salt="farmacia.revision", max_age=30 * 60)
        except signing.BadSignature:
            raise ValidationError("La revisión no es válida o caducó. Actualice la página y revise nuevamente.")
        if revisada != revision_importacion(carga):
            raise ValidationError("La vista previa cambió. Actualice la página y confirme el nuevo resumen.")
    filas, formularios = validar_filas(carga.filas, carga.modalidad)
    if revision is not None and revisada != revision_importacion(carga, filas):
        carga.filas = filas
        carga.save(update_fields=["filas"])
        return carga  # Nuevos errores o filas ahora válidas exigen otra confirmación.
    validas = sum(not fila["errores"] for fila in filas)
    if not validas or (not omitir_errores and any(fila["errores"] for fila in filas)):
        carga.filas = filas
        carga.save(update_fields=["filas"])
        return carga
    nuevos, lotes_nuevos, unidades = 0, 0, 0
    cache = {}
    for fila, form in zip(filas, formularios):
        if fila["errores"]:
            fila["resultado"] = "OMITIDA"
            continue
        fila["resultado"] = "IMPORTADA"
        if carga.modalidad == "CATALOGO":
            form.save()
            nuevos += 1
            continue
        datos = form.cleaned_data
        codigo = normalizar(datos["codigo"])
        if codigo not in cache:
            if form.medicamento:
                med = Medicamento.objects.select_for_update().select_related("articulo").get(pk=form.medicamento.pk)
                comprobacion = RecepcionCSVForm(fila["datos"])
                if not comprobacion.is_valid():
                    raise ValidationError(f"Fila {fila['numero']}: los datos cambiaron durante la confirmación. Vuelva a revisar el archivo.")
                if not med.unidad_medida:
                    med.unidad_medida = datos["unidad_medida"]
                    med.save(update_fields=["unidad_medida", "updated_at"])
            else:
                med = form.save()
                nuevos += 1
            cache[codigo] = med
        med = cache[codigo]
        lote = LoteMedicamento.objects.filter(medicamento=med, numero_lote__iexact=datos["numero_lote"]).first()
        if lote is None:
            lotes_nuevos += 1
        numero_lote = lote.numero_lote if lote else datos["numero_lote"]
        movimiento = registrar_entrada_medicamento(
            medicamento=med, anexo=form.ubicacion, numero_lote=numero_lote,
            fecha_vencimiento=datos["fecha_vencimiento"], cantidad=datos["cantidad"], usuario=usuario,
            motivo=datos.get("observaciones", ""), proveedor=datos["proveedor"],
            documento_recepcion=datos["documento_recepcion"], fecha_recepcion=datos["fecha_recepcion"],
            importacion=carga, fila_importacion=fila["numero"],
        )
        fila["movimiento_id"] = movimiento.pk
        fila["medicamento_id"] = med.pk
        fila["lote_id"] = movimiento.detalles.get().lote_id
        unidades += datos["cantidad"]
    carga.estado = "IMPORTADA"
    carga.cantidad_importada = nuevos
    carga.cantidad_lotes_creados = lotes_nuevos
    carga.cantidad_movimientos = validas if carga.modalidad == "RECEPCION" else 0
    carga.cantidad_unidades = unidades
    carga.importada_at = timezone.now()
    carga.filas = filas
    carga.save(update_fields=["estado", "cantidad_importada", "cantidad_lotes_creados", "cantidad_movimientos", "cantidad_unidades", "importada_at", "filas"])
    return carga
