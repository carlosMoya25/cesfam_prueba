from django.core.exceptions import ValidationError
from organizacion.models import Anexo
from .models import ConfiguracionFarmacia


def ubicaciones_recepcion():
    return Anexo.objects.filter(activo=True, configuracion_farmacia__pk=1)


def exigir_bodega_central():
    configuracion = ConfiguracionFarmacia.objects.select_related("bodega_central").filter(pk=1).first()
    if not configuracion or not configuracion.bodega_central.activo:
        raise ValidationError("Bodega Central no está configurada o está inactiva. Solicite al administrador revisar la ubicación antes de registrar operaciones.")
    return configuracion.bodega_central
