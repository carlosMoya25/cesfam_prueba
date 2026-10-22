"""Identidad de una recepción, independiente del archivo y de su cantidad."""
import hashlib
import json
import unicodedata


def normalizar(valor):
    return " ".join(unicodedata.normalize("NFKC", str(valor)).split()).casefold()


def clave_de_recepcion(*, proveedor, documento, medicamento_id, lote_id, ubicacion_id):
    identidad = [normalizar(proveedor), normalizar(documento), medicamento_id, lote_id, ubicacion_id]
    return hashlib.sha256(json.dumps(identidad, ensure_ascii=False).encode()).hexdigest()
