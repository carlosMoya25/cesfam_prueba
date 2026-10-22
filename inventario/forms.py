from django import forms

from organizacion.models import Anexo

from .models import Articulo, CategoriaArticulo, StockAnexo

class MovimientoFormMixin:
    operacion = "entrada"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        campo_origen = "anexo_origen" if self.operacion == "transferencia" else "anexo"
        valor = self.data.get(campo_origen) if self.is_bound else self.initial.get(campo_origen)
        try:
            ubicacion_id = int(valor)
            if not 0 < ubicacion_id <= 9223372036854775807:
                ubicacion_id = None
        except (ValueError, TypeError):
            ubicacion_id = None
        if ubicacion_id and self.operacion in ("salida", "transferencia"):
            self.fields["articulo"].queryset = self.fields["articulo"].queryset.filter(
                stocks__anexo_id=ubicacion_id, stocks__cantidad__gt=0,
            )
        self.fields["articulo"].label_from_instance = lambda articulo: (
            f"{articulo.nombre} ({articulo.codigo})" if articulo.codigo else articulo.nombre
        )
        for nombre in ("anexo", "anexo_origen", "anexo_destino"):
            if nombre in self.fields:
                self.fields[nombre].label = {
                    "anexo": "Ubicación de destino" if self.operacion == "entrada" else "Ubicación",
                    "anexo_origen": "Ubicación de origen", "anexo_destino": "Ubicación de destino",
                }[nombre]
        if self.operacion == "salida":
            self.fields["anexo"].label = "Ubicación de origen"
        self.order_fields([campo_origen, "anexo_destino", "articulo", "tipo_ajuste", "cantidad", "motivo"])

    def clean(self):
        datos = super().clean()
        resta = self.operacion in ("salida", "transferencia") or (
            self.operacion == "ajuste" and datos.get("tipo_ajuste") == "negativo"
        )
        ubicacion = datos.get("anexo_origen") or datos.get("anexo")
        if resta and ubicacion and datos.get("articulo") and datos.get("cantidad"):
            saldo = StockAnexo.objects.filter(anexo=ubicacion, articulo=datos["articulo"]).values_list("cantidad", flat=True).first() or 0
            if datos["cantidad"] > saldo:
                self.add_error("cantidad", f"Stock insuficiente. Disponible en esta ubicación: {saldo}.")
        return datos


class EntradaInventarioForm(MovimientoFormMixin, forms.Form):
    operacion = "entrada"
    anexo = forms.ModelChoiceField(
        queryset=Anexo.objects.filter(activo=True),
        label="Anexo de destino",
    )

    articulo = forms.ModelChoiceField(
        queryset=Articulo.objects.filter(activo=True, medicamento__isnull=True, saldos_pendientes_farmacia__isnull=True),
        label="Artículo",
    )

    cantidad = forms.IntegerField(
        min_value=1,
        label="Cantidad",
    )

    motivo = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class SalidaInventarioForm(MovimientoFormMixin, forms.Form):
    operacion = "salida"
    anexo = forms.ModelChoiceField(
        queryset=Anexo.objects.filter(activo=True),
        label="Anexo de origen",
    )

    articulo = forms.ModelChoiceField(
        queryset=Articulo.objects.filter(activo=True, medicamento__isnull=True, saldos_pendientes_farmacia__isnull=True),
        label="Artículo",
    )

    cantidad = forms.IntegerField(
        min_value=1,
        label="Cantidad",
    )

    motivo = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class TransferenciaInventarioForm(MovimientoFormMixin, forms.Form):
    operacion = "transferencia"
    anexo_origen = forms.ModelChoiceField(
        queryset=Anexo.objects.filter(activo=True),
        label="Anexo de origen",
    )

    anexo_destino = forms.ModelChoiceField(
        queryset=Anexo.objects.filter(activo=True),
        label="Anexo de destino",
    )

    articulo = forms.ModelChoiceField(
        queryset=Articulo.objects.filter(activo=True, medicamento__isnull=True, saldos_pendientes_farmacia__isnull=True),
        label="Artículo",
    )

    cantidad = forms.IntegerField(
        min_value=1,
        label="Cantidad",
    )

    motivo = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean(self):
        cleaned_data = super().clean()

        origen = cleaned_data.get("anexo_origen")
        destino = cleaned_data.get("anexo_destino")

        if origen and destino and origen == destino:
            raise forms.ValidationError(
                "El anexo de origen y destino deben ser diferentes."
            )

        return cleaned_data

class AjusteInventarioForm(MovimientoFormMixin, forms.Form):
    operacion = "ajuste"

    TIPO_AJUSTE = [
        ("positivo", "Sumar unidades"),
        ("negativo", "Restar unidades"),
    ]

    anexo = forms.ModelChoiceField(
        queryset=Anexo.objects.filter(activo=True),
        label="Anexo",
    )

    articulo = forms.ModelChoiceField(
        queryset=Articulo.objects.filter(activo=True, medicamento__isnull=True, saldos_pendientes_farmacia__isnull=True),
        label="Artículo",
    )

    tipo_ajuste = forms.ChoiceField(
        choices=TIPO_AJUSTE,
        label="Tipo de ajuste",
    )

    cantidad = forms.IntegerField(
        min_value=1,
        label="Cantidad a ajustar",
    )

    motivo = forms.CharField(
        required=True,
        label="Motivo del ajuste",
        widget=forms.Textarea(
            attrs={"rows": 3}
        ),
    )

class RecursoRequerimientoForm(forms.Form):

    articulo = forms.ModelChoiceField(
        queryset=Articulo.objects.filter(activo=True, medicamento__isnull=True, saldos_pendientes_farmacia__isnull=True),
        label="Artículo utilizado",
    )

    cantidad = forms.IntegerField(
        min_value=1,
        label="Cantidad",
    )

class CategoriaArticuloForm(forms.ModelForm):
    class Meta:
        model = CategoriaArticulo
        fields = [
            "nombre",
            "descripcion",
            "activo",
        ]

        widgets = {
            "nombre": forms.TextInput(
                attrs={
                    "placeholder": "Ej: Equipos computacionales",
                }
            ),
            "descripcion": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Descripción de la categoría",
                }
            ),
        }


class ArticuloForm(forms.ModelForm):
    class Meta:
        model = Articulo
        fields = [
            "nombre",
            "descripcion",
            "categoria",
            "codigo",
            "stock_minimo",
            "activo",
        ]

        help_texts = {
            "codigo": "Código interno opcional. Debe ser único.",
            "stock_minimo": "Se mostrará stock bajo cuando la cantidad sea mayor que cero y no supere este mínimo. Se aplica a cada ubicación.",
            "activo": "Los artículos inactivos conservan su historial y no pueden utilizarse en nuevos movimientos.",
        }

        widgets = {
            "nombre": forms.TextInput(
                attrs={
                    "placeholder": "Ej: Mouse USB",
                }
            ),
            "descripcion": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Descripción del artículo",
                }
            ),
            "codigo": forms.TextInput(
                attrs={
                    "placeholder": "Ej: MOU-001",
                }
            ),
            "stock_minimo": forms.NumberInput(
                attrs={
                    "min": 0,
                }
            ),
        }

    def clean_categoria(self):
        categoria = self.cleaned_data["categoria"]
        if categoria.nombre.strip().casefold() == "medicamentos":
            raise forms.ValidationError("Registre los medicamentos desde Farmacia, con su ficha y trazabilidad, no como artículos generales.")
        return categoria

    def clean_codigo(self):
        codigo = self.cleaned_data.get("codigo")

        if codigo:
            codigo = codigo.strip()

        return codigo or None