import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from farmacia.centralizacion import plan_centralizacion, filas_por_centralizar, centralizar_existencias


class Command(BaseCommand):
    help = "Revisa o centraliza en una transacción el stock de Farmacia, conservando los ingresos originales."

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true")
        parser.add_argument("--usuario")
        parser.add_argument("--huella")
        parser.add_argument("--respaldo", type=Path)

    def handle(self, *args, **opciones):
        try:
            plan = plan_centralizacion()
            if not opciones["aplicar"]:
                filas = filas_por_centralizar(plan)
                self.stdout.write(json.dumps({
                    "huella": plan["huella"], "bodega_id": plan["bodega_id"],
                    "registros": len(filas), "medicamentos": len({f["lote__medicamento_id"] for f in filas}),
                    "cantidad_registrada": sum(f["cantidad"] for f in filas),
                    "revisar_unidad": [{"codigo": f["lote__medicamento__articulo__codigo"],
                        "nombre": f["lote__medicamento__articulo__nombre"], "cantidad": f["cantidad"]}
                        for f in filas if not f["lote__medicamento__unidad_medida"]],
                }, ensure_ascii=False))
                return
            if not all(opciones[campo] for campo in ("usuario", "huella", "respaldo")):
                raise CommandError("Aplicar exige --usuario, --huella del plan revisado y --respaldo nuevo.")
            usuario = get_user_model().objects.get(email__iexact=opciones["usuario"])
            ruta = opciones["respaldo"]
            ruta.parent.mkdir(parents=True, exist_ok=True)

            def respaldar(datos):
                descriptor = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
                    json.dump(datos, archivo, ensure_ascii=False, indent=2)

            resultado = centralizar_existencias(usuario=usuario, huella_esperada=opciones["huella"], respaldar=respaldar)
            self.stdout.write(json.dumps(resultado, ensure_ascii=False))
        except (ValidationError, get_user_model().DoesNotExist, OSError) as error:
            raise CommandError(str(error)) from error
