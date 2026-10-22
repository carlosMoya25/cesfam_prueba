
from django.utils import timezone

from django import forms

from organizacion.models import Anexo
from .models import Medicamento, LoteMedicamento
from .bodega import ubicaciones_recepcion, exigir_bodega_central


class IngresoMedicamentoForm(forms.Form):

    medicamento = forms.ModelChoiceField(
        queryset=Medicamento.objects.none(),
        label="Medicamento",
        empty_label="Seleccione un medicamento",
    )

    anexo = forms.ModelChoiceField(
        queryset=Anexo.objects.none(),
        label="Ubicación de destino",
        empty_label="Seleccione una ubicación",
    )

    lote = forms.ModelChoiceField(
        queryset=LoteMedicamento.objects.none(),
        label="Número de lote",
        required=False,
        empty_label="Seleccione primero un medicamento",
    )

    cantidad = forms.IntegerField(
        label="Cantidad recibida",
        min_value=1,
    )

    proveedor = forms.CharField(max_length=150, label="Proveedor / origen de abastecimiento")
    documento_recepcion = forms.CharField(max_length=100, label="Documento de recepción")
    fecha_recepcion = forms.DateField(
        label="Fecha de recepción", initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"), input_formats=["%Y-%m-%d"],
    )

    motivo = forms.CharField(
        label="Observaciones",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["medicamento"].queryset = (
            Medicamento.objects.filter(
                activo=True,
                articulo__activo=True,
            ).select_related("articulo")
        )

        self.fields["medicamento"].label_from_instance = lambda med: f"{med.articulo.nombre} · {med.concentracion} · {med.unidad_medida or 'Unidad sin definir'}"

        self.fields["anexo"].queryset = ubicaciones_recepcion()
        self.fields["anexo"].disabled = True
        self.fields["anexo"].label = "Destino de la recepción: Bodega Central"
        self.initial["anexo"] = self.fields["anexo"].queryset.values_list("pk", flat=True).first()

        self.fields["lote"].widget.attrs["aria-describedby"] = "lote-ayuda"
        self.fields["lote"].label_from_instance = lambda lote: lote.numero_lote
        medicamento_id = (
            self.data.get("medicamento") if self.is_bound
            else self.initial.get("medicamento")
        )
        try:
            medicamento_id = int(medicamento_id)
            if not 0 < medicamento_id <= 9223372036854775807:
                medicamento_id = None
        except (TypeError, ValueError):
            medicamento_id = None
        if medicamento_id:
            lotes = LoteMedicamento.objects.filter(
                medicamento_id=medicamento_id,
                medicamento__in=self.fields["medicamento"].queryset,
                activo=True,
                fecha_vencimiento__gte=timezone.localdate(),
            ).order_by("fecha_vencimiento", "numero_lote", "pk")
            self.fields["lote"].queryset = lotes
            self.fields["lote"].empty_label = "Seleccione el lote recibido"
            if not self.is_bound and lotes.count() == 1:
                self.initial["lote"] = lotes.first().pk

    def clean_fecha_recepcion(self):
        fecha = self.cleaned_data["fecha_recepcion"]
        if fecha > timezone.localdate():
            raise forms.ValidationError("La recepción no puede tener una fecha futura.")
        return fecha

    def clean(self):
        datos = super().clean()
        exigir_bodega_central()
        medicamento = datos.get("medicamento")
        if medicamento and not medicamento.unidad_medida:
            self.add_error("medicamento", "Complete la unidad de medida en la ficha del medicamento antes de recibir existencias.")
        if medicamento and "lote" not in self.errors and not datos.get("lote"):
            lotes = list(self.fields["lote"].queryset[:2])
            if len(lotes) == 1:
                datos["lote"] = lotes[0]
            elif not lotes:
                self.add_error("lote", "Este medicamento no tiene lotes activos sin vencer. Registre el lote antes de ingresar existencias.")
            else:
                self.add_error("lote", "Seleccione el lote correspondiente al medicamento recibido.")
        return datos


from django.db import transaction

from inventario.models import Articulo, CategoriaArticulo


class MedicamentoForm(forms.Form):

    unidad_medida = forms.CharField(
        max_length=50, required=True, label="Unidad de medida",
        help_text="Unidad en que se cuentan las existencias: por ejemplo, comprimido, frasco o ampolla. No se realizan conversiones automáticas.",
    )

    nombre = forms.CharField(
        max_length=150,
        label="Nombre del medicamento",
    )

    codigo = forms.CharField(
        max_length=50,
        label="Código interno",
        required=False,
    )

    descripcion = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    principio_activo = forms.CharField(
        max_length=150,
    )

    concentracion = forms.CharField(
        max_length=100,
    )

    forma_farmaceutica = forms.CharField(
        max_length=100,
    )

    presentacion = forms.CharField(
        max_length=150,
        required=False,
    )

    stock_minimo = forms.IntegerField(
        min_value=0,
        initial=0,
    )

    requiere_receta = forms.BooleanField(
        required=False,
        initial=True,
    )

    def __init__(self, *args, medicamento=None, **kwargs):
        self.medicamento = medicamento
        super().__init__(*args, **kwargs)

        if medicamento and not self.is_bound:
            articulo = medicamento.articulo

            self.initial.update({
                "nombre": articulo.nombre,
                "codigo": articulo.codigo,
                "descripcion": articulo.descripcion,
                "principio_activo": medicamento.principio_activo,
                "concentracion": medicamento.concentracion,
                "forma_farmaceutica": medicamento.forma_farmaceutica,
                "presentacion": medicamento.presentacion,
                "unidad_medida": medicamento.unidad_medida,
                "stock_minimo": articulo.stock_minimo,
                "requiere_receta": medicamento.requiere_receta,
            })

    def _validar_cambio_unidad(self, medicamento, unidad):
        if medicamento and medicamento.unidad_medida:
            from .trazabilidad import normalizar
            from .models import StockLote
            if normalizar(unidad) != normalizar(medicamento.unidad_medida) and (
                StockLote.objects.filter(lote__medicamento=medicamento, cantidad__gt=0).exists()
                or medicamento.lotes.filter(movimientos_farmacia__isnull=False).exists()
            ):
                raise forms.ValidationError("No se puede cambiar la unidad con existencias o movimientos registrados. Se requiere una regularización explícita del inventario.")

    def clean_unidad_medida(self):
        unidad = self.cleaned_data["unidad_medida"].strip()
        self._validar_cambio_unidad(self.medicamento, unidad)
        return unidad

    def clean_codigo(self):
        codigo = self.cleaned_data["codigo"].strip() or None

        existentes = Articulo.objects.filter(codigo=codigo)

        if self.medicamento:
            existentes = existentes.exclude(
                pk=self.medicamento.articulo_id
            )

        if codigo and existentes.exists():
            raise forms.ValidationError(
                "Ya existe un artículo con este código."
            )

        return codigo

    @transaction.atomic
    def save(self):
        datos = self.cleaned_data

        categoria, _ = CategoriaArticulo.objects.get_or_create(
            nombre="Medicamentos",
            defaults={
                "descripcion": "Catálogo de medicamentos de Farmacia",
            },
        )

        if self.medicamento:
            medicamento = Medicamento.objects.select_for_update().get(
                pk=self.medicamento.pk
            )
            self._validar_cambio_unidad(medicamento, datos["unidad_medida"])
            articulo = Articulo.objects.select_for_update().get(
                pk=medicamento.articulo_id
            )
        else:
            articulo = Articulo()
            medicamento = Medicamento(articulo=articulo)

        articulo.nombre = datos["nombre"]
        articulo.codigo = datos["codigo"]
        articulo.descripcion = datos["descripcion"]
        articulo.categoria = categoria
        articulo.stock_minimo = datos["stock_minimo"]
        articulo.save()

        medicamento.articulo = articulo
        medicamento.principio_activo = datos["principio_activo"]
        medicamento.concentracion = datos["concentracion"]
        medicamento.forma_farmaceutica = datos["forma_farmaceutica"]
        medicamento.presentacion = datos["presentacion"]
        medicamento.unidad_medida = datos["unidad_medida"]
        medicamento.requiere_receta = datos["requiere_receta"]
        medicamento.save()

        return medicamento
