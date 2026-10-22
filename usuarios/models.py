from django.db import models

from django.contrib.auth.models import AbstractUser
from django.db import models
from .managers import UsuarioManager

class Usuario(AbstractUser):
    username = None

    email = models.EmailField(
        "correo electrónico",
        unique=True
    )

    nombres = models.CharField(
        "nombres",
        max_length=100
    )

    apellido_paterno = models.CharField(
        "apellido paterno",
        max_length=100
    )

    apellido_materno = models.CharField(
        "apellido materno",
        max_length=100,
        blank=True
    )

    telefono = models.CharField(
        max_length=30,
        blank=True
    )

    anexo = models.ForeignKey(
        "organizacion.Anexo",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usuarios"
    )

    departamento = models.ForeignKey(
        "organizacion.Departamento",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usuarios"
    )

    debe_cambiar_clave = models.BooleanField(default=False, verbose_name="Debe cambiar su contraseña temporal")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["nombres", "apellido_paterno"]
    objects = UsuarioManager()
    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"
        ordering = ["apellido_paterno", "nombres"]

    def __str__(self):
        return f"{self.nombres} {self.apellido_paterno} ({self.email})"



class HistorialSeguridad(models.Model):

    TIPO_ACCION_CHOICES = [
        ("DESBLOQUEO_USUARIO", "Desbloqueo de usuario"),
        ("REINICIO_INTENTOS", "Reinicio de intentos"),
        ("BLOQUEO_USUARIO", "Bloqueo de usuario"),
        ("OTRO", "Otra acción de seguridad"),
    ]

    tipo_accion = models.CharField(
        max_length=50,
        choices=TIPO_ACCION_CHOICES
    )

    usuario_afectado = models.ForeignKey(
        "usuarios.Usuario",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eventos_seguridad_recibidos"
    )

    email_afectado = models.EmailField()

    realizado_por = models.ForeignKey(
        "usuarios.Usuario",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acciones_seguridad_realizadas"
    )

    descripcion = models.TextField(
        blank=True
    )

    fecha = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        verbose_name = "Historial de seguridad"
        verbose_name_plural = "Historial de seguridad"
        ordering = ["-fecha"]

    def __str__(self):
        return (
            f"{self.get_tipo_accion_display()} - "
            f"{self.email_afectado}"
        )


class HistorialAdministracion(models.Model):

    ACCIONES = [
        ("CREAR_USUARIO", "Creación de usuario"),
        ("EDITAR_USUARIO", "Modificación de usuario"),
        ("CAMBIAR_ROLES", "Cambio de roles"),
        ("ACTIVAR_USUARIO", "Activación de usuario"),
        ("DESACTIVAR_USUARIO", "Desactivación de usuario"),
    ]

    accion = models.CharField(
        max_length=50,
        choices=ACCIONES,
    )

    administrador = models.ForeignKey(
        "usuarios.Usuario",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acciones_administrativas",
    )

    usuario_afectado = models.ForeignKey(
        "usuarios.Usuario",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historial_administrativo",
    )

    descripcion = models.TextField()

    fecha = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        verbose_name = "Historial administrativo"
        verbose_name_plural = "Historial administrativo"
        ordering = ["-fecha"]

    def __str__(self):
        return f"{self.get_accion_display()} - {self.fecha}"