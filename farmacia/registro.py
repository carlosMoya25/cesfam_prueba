"""Alta manual con las mismas validaciones que una recepción CSV."""
from django import forms
from django.db import transaction
from django.utils import timezone

from organizacion.models import Anexo
from .importacion import RecepcionCSVForm
from .services import registrar_entrada_medicamento
from .bodega import ubicaciones_recepcion, exigir_bodega_central


class RegistroMedicamentoForm(RecepcionCSVForm):
    ubicacion_id = forms.ModelChoiceField(
        queryset=Anexo.objects.none(), label="Ubicación de destino",
        empty_label="Seleccione una ubicación",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ubicacion_id"].queryset = ubicaciones_recepcion()
        self.fields["ubicacion_id"].disabled = True
        self.fields["ubicacion_id"].label = "Destino de la recepción: Bodega Central"
        self.initial["ubicacion_id"] = self.fields["ubicacion_id"].queryset.values_list("pk", flat=True).first()
        self.fields["ubicacion_id"].help_text = "Los medicamentos se reciben en Bodega Central. Después utilice Trasladar a anexo."
        for campo in ("fecha_vencimiento", "fecha_recepcion"):
            self.fields[campo].widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
        self.fields["fecha_recepcion"].initial = timezone.localdate
        for campo, etiqueta in {
            "descripcion": "Descripción", "concentracion": "Concentración",
            "forma_farmaceutica": "Forma farmacéutica", "presentacion": "Presentación",
            "stock_minimo": "Stock mínimo", "fecha_vencimiento": "Vencimiento del lote",
            "fecha_recepcion": "Fecha de recepción",
        }.items():
            self.fields[campo].label = etiqueta
        self.fields["stock_minimo"].initial = 0
        self.fields["requiere_receta"].initial = "si"
        self.fields["observaciones"].widget = forms.Textarea(attrs={"rows": 3})
        self.fields["codigo"].help_text = "Código único del catálogo. Si ya existe, utilice Registrar entrada para recibir más unidades."
        self.fields["numero_lote"].help_text = "Copie el número del envase o del documento de recepción."
        self.fields["fecha_vencimiento"].help_text = "Vencimiento real del lote. No se aceptan lotes vencidos."
        self.fields["cantidad"].help_text = "Entero positivo expresado en la unidad de medida del medicamento."
        self.fields["documento_recepcion"].help_text = "Referencia de la guía, factura, acta u otro documento de respaldo."

    def clean(self):
        datos = super().clean()
        exigir_bodega_central()
        return datos

    def clean_codigo(self):
        codigo = super().clean_codigo()
        if self.articulo_existente:
            raise forms.ValidationError("El código ya existe. Revise su ficha y utilice Registrar entrada para recibir más unidades del medicamento.")
        return codigo

    def clean_ubicacion_id(self):
        self.ubicacion = self.cleaned_data["ubicacion_id"]
        return self.ubicacion.pk

    @property
    def secciones(self):
        return [
            ("Datos del medicamento", [self[campo] for campo in (
                "codigo", "nombre", "principio_activo", "concentracion", "forma_farmaceutica",
                "presentacion", "unidad_medida", "requiere_receta", "stock_minimo", "descripcion",
            )]),
            ("Lote y existencias iniciales", [self[campo] for campo in (
                "numero_lote", "fecha_vencimiento", "ubicacion_id", "cantidad",
            )]),
            ("Respaldo de la recepción", [self[campo] for campo in (
                "proveedor", "documento_recepcion", "fecha_recepcion", "observaciones",
            )]),
        ]

    @transaction.atomic
    def guardar_recepcion(self, *, usuario):
        medicamento = self.save()
        datos = self.cleaned_data
        movimiento = registrar_entrada_medicamento(
            medicamento=medicamento, anexo=self.ubicacion, numero_lote=datos["numero_lote"],
            fecha_vencimiento=datos["fecha_vencimiento"], cantidad=datos["cantidad"], usuario=usuario,
            motivo=datos["observaciones"], proveedor=datos["proveedor"],
            documento_recepcion=datos["documento_recepcion"], fecha_recepcion=datos["fecha_recepcion"],
        )
        return medicamento, movimiento
