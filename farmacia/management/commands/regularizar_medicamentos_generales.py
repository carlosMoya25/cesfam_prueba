import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from farmacia.regularizacion_general import plan_regularizacion_general, regularizar_general


class Command(BaseCommand):
    help = "Revisa y retira medicamentos ajenos al inventario general, conservando los saldos dudosos en conciliación."

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true")
        parser.add_argument("--usuario")
        parser.add_argument("--huella")
        parser.add_argument("--respaldo", type=Path)

    def handle(self, *args, **opciones):
        try:
            plan = plan_regularizacion_general()
            if not opciones["aplicar"]:
                self.stdout.write(json.dumps({"huella": plan["huella"], "articulos": [
                    {"codigo": a["datos"]["codigo"], "nombre": a["datos"]["nombre"],
                     "accion": "ELIMINAR_SIN_STOCK_NI_HISTORIAL" if not a["stocks"] and not a["movimientos"] and not a["recursos"] else "CONSERVAR_EN_CONCILIACION"}
                    for a in plan["articulos"]], "saldos_a_conciliar": [{"stock_id": s["id"], "cantidad": s["cantidad"]} for s in plan["saldos"]]}, ensure_ascii=False))
                return
            if not all(opciones[c] for c in ("usuario", "huella", "respaldo")):
                raise CommandError("Aplicar exige --usuario, --huella revisada y --respaldo nuevo.")
            usuario = get_user_model().objects.get(email__iexact=opciones["usuario"])
            ruta = opciones["respaldo"]
            ruta.parent.mkdir(parents=True, exist_ok=True)
            def respaldar(datos):
                descriptor = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
                    json.dump(datos, archivo, ensure_ascii=False, indent=2)
            resultado = regularizar_general(usuario=usuario, huella_esperada=opciones["huella"], respaldar=respaldar)
            self.stdout.write(json.dumps(resultado, ensure_ascii=False))
        except (ValidationError, get_user_model().DoesNotExist, OSError) as error:
            raise CommandError(str(error)) from error
