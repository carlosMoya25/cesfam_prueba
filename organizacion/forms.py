from django import forms

from .models import Anexo, Departamento


class NombreOrganizacionMixin:
    def clean_nombre(self):
        nombre = " ".join(self.cleaned_data["nombre"].split())
        existentes = self._meta.model.objects.filter(nombre__iexact=nombre)
        if self.instance.pk:
            existentes = existentes.exclude(pk=self.instance.pk)
        if existentes.exists():
            raise forms.ValidationError(
                "Ya existe un registro con este nombre. Revise también los registros inactivos antes de crear otro."
            )
        return nombre


class DepartamentoForm(NombreOrganizacionMixin, forms.ModelForm):
    class Meta:
        model = Departamento
        fields = ("nombre", "descripcion")
        labels = {"nombre": "Nombre del departamento", "descripcion": "Descripción"}
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 3})}


class UbicacionInventarioForm(NombreOrganizacionMixin, forms.ModelForm):
    class Meta:
        model = Anexo
        fields = ("nombre", "tipo", "direccion", "telefono")
        labels = {
            "nombre": "Nombre de la ubicación", "tipo": "Tipo de establecimiento",
            "direccion": "Dirección", "telefono": "Teléfono",
        }
        help_texts = {"nombre": "Nombre que aparecerá al registrar entradas, salidas y transferencias."}
