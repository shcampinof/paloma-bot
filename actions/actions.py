# actions.py — Acciones y validadores para el bot de la Defensoría
# - Consulta por cédula con privacidad si el defendido es menor (TI)
# - Validadores de forms: consulta_proceso_form, pqrsdf_form, contacto_form
# - Limpieza de slots de PQRSDF al cerrar (ActionResetPqrsSlots)

import re
import csv
import logging
import traceback
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Text, Optional

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, FollowupAction

# Compatibilidad con Rasa SDK 2/3
try:
    from rasa_sdk.forms import FormValidationAction
except Exception:  # pragma: no cover
    from rasa_sdk import FormValidationAction

logger = logging.getLogger(__name__)

# ------------------------- Config de datos -------------------------
# Intentamos ubicar data/radicados.csv de forma robusta.
_THIS = Path(__file__).resolve()
_CANDIDATES = [
    _THIS.parent / "data" / "radicados.csv",          # proyecto raíz típico
    _THIS.parent.parent / "data" / "radicados.csv",   # si actions/ vive en subcarpeta
    Path.cwd() / "data" / "radicados.csv",            # por si el cwd es el raíz
]
_DB_PATH: Optional[Path] = next((p for p in _CANDIDATES if p.exists()), None)
if _DB_PATH is None:
    # Fallback a la ruta “esperada” en el proyecto
    _DB_PATH = _THIS.parent / "data" / "radicados.csv"
DB_PATH: Path = _DB_PATH

# Encabezados admitidos (variantes con/sin acento)
H_ID        = ("Número de identificación", "Numero de identificacion", "numero_identificacion", "Cédula", "Cedula", "cedula")
H_TIPO_DOC  = ("Tipo de documento", "tipo_documento", "Tipo doc", "tipo_doc", "Documento")
H_USR       = ("Nombre completo", "Usuario", "nombre_completo")
H_DEFENSOR  = ("Defensor asignado", "defensor_asignado")
H_CORREO    = ("Correo", "correo", "email", "e-mail")
H_SUP       = ("Supervisor", "supervisor")
H_SUP_MAIL  = ("Correo supervisor", "Correo Supervisor", "correo_supervisor", "email_supervisor")

H_RAD       = ("Número de radicado", "Numero de radicado", "radicado")
H_DEP       = ("Departamento",)
H_MUN       = ("Municipio",)
H_JUZ       = ("Juzgado",)
H_INICIO    = ("Inicio de proceso", "Inicio del proceso")
H_DELITO    = ("Delito",)
H_CAPT      = ("Capturado",)
H_TIPO_CAP  = ("Tipo de captura",)
H_MED       = ("Medida impuesta",)
H_CENTRO    = ("Centro carcelario", "Centro de reclusión", "Centro de reclusion")

# Opcionales (si algún día los agregas)
H_ES_MENOR  = ("Es menor", "es_menor", "Menor", "menor", "Menor de edad", "menor_de_edad")
H_EDAD      = ("Edad", "edad")

_ROWS_CACHE: Optional[List[Dict[str, str]]] = None

# ------------------------- Utilidades -------------------------
def _digits(s: Any) -> str:
    """Deja solo dígitos."""
    return re.sub(r"\D", "", str(s or ""))

def _get(row: Dict[str, Any], keys) -> str:
    """Obtiene el primer valor no vacío para cualquiera de las llaves candidatas."""
    for k in keys:
        if k in row and row[k] is not None:
            v = str(row[k]).strip()
            if v != "":
                return v
    return ""

def _val(x: Any) -> str:
    """Valor o 'NA' si vacío."""
    x = (x or "").strip()
    return x if x else "NA"

def _to_int(x: Any) -> Optional[int]:
    try:
        return int(re.sub(r"[^\d\-]", "", str(x)))
    except Exception:
        return None

def _strip_accents_lower(s: Any) -> str:
    t = unicodedata.normalize("NFKD", str(s or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.lower()

def _norm_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

# ------------------------- Carga CSV -------------------------
def _load_rows() -> List[Dict[str, str]]:
    """Carga en memoria el CSV de radicados (con cache)."""
    global _ROWS_CACHE
    if _ROWS_CACHE is not None:
        return _ROWS_CACHE
    if not DB_PATH.exists():
        logger.error(f"[lookup] No existe el CSV en: {DB_PATH}")
        _ROWS_CACHE = []
        return _ROWS_CACHE
    with DB_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        _ROWS_CACHE = list(csv.DictReader(f))
    logger.info(f"[lookup] Cargadas {len(_ROWS_CACHE)} filas desde {DB_PATH}")
    if _ROWS_CACHE:
        logger.debug(f"[lookup] Encabezados: {list(_ROWS_CACHE[0].keys())}")
    return _ROWS_CACHE

# ---- ¿Menor? SOLO si el tipo de documento es TI (o por edad/flag opcional) ----
def _row_is_minor_defendido(row: Dict[str, Any]) -> bool:
    tipo = _strip_accents_lower(_get(row, H_TIPO_DOC))
    if tipo in {"ti", "tarjeta de identidad", "tarjeta_identidad", "tarjeta identidad"}:
        return True
    # respaldos opcionales
    edad = _to_int(_get(row, H_EDAD))
    if edad is not None and 0 <= edad < 18:
        return True
    es_menor_flag = _strip_accents_lower(_get(row, H_ES_MENOR))
    if es_menor_flag in {"si", "sí", "true", "1", "x", "yes"}:
        return True
    return False

# ------------------------- Acción principal -------------------------
class ActionLookupCedula(Action):
    def name(self) -> Text:
        return "action_lookup_cedula"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        try:
            rows = _load_rows()
            if not rows:
                dispatcher.utter_message(text="No puedo acceder a la base en este momento. Intenta más tarde.")
                return [SlotSet("numero_identificacion", None)]

            ced_in = tracker.get_slot("numero_identificacion")
            ced = _digits(ced_in)
            if not ced:
                dispatcher.utter_message(text="No recibí el número de identificación. ¿Puedes indicarlo de nuevo?")
                return []

            # Filtrar por cédula
            matches: List[Dict[str, str]] = [r for r in rows if _digits(_get(r, H_ID)) == ced]

            if not matches:
                dispatcher.utter_message(
                    text="No encontré registros con esa cédula. ¿Quieres intentar de nuevo o hablar con un asesor?",
                    buttons=[
                        {"title": "🔁 Intentar de nuevo", "payload": "/consultar_proceso"},
                        {"title": "👤 Hablar con una persona", "payload": "/hablar_con_humano"},
                    ],
                )
                return [SlotSet("numero_identificacion", None)]

            # Datos de contacto (primera coincidencia)
            d_nombre = _val(_get(matches[0], H_DEFENSOR))
            d_correo = _val(_get(matches[0], H_CORREO))
            s_nombre = _val(_get(matches[0], H_SUP))
            s_correo = _val(_get(matches[0], H_SUP_MAIL))

            def_str = (d_nombre if d_nombre != "NA" else "No disponible") + (f" ({d_correo})" if d_correo != "NA" else "")
            sup_str = (s_nombre if s_nombre != "NA" else "No disponible") + (f" ({s_correo})" if s_correo != "NA" else "")

            # ¿Todos los procesos corresponden a defendido menor?
            all_minor = all(_row_is_minor_defendido(r) for r in matches)

            if all_minor:
                # Mensaje mínimo SIN nombre de la persona
                msg = (
                    f"**Caso con persona menor de edad.**\n"
                    f"**Defensor(a):** {def_str}\n"
                    f"**Supervisor:** {sup_str}"
                )
                dispatcher.utter_message(text=msg)

            else:
                # Cabecera para adultos
                header_md = f"**Defensor asignado:** {def_str}"
                if s_nombre != 'NA' or s_correo != 'NA':
                    header_md += f"\n**Supervisor:** {sup_str}"
                dispatcher.utter_message(text=header_md)

                # Por cada proceso: si el defendido es menor → mensaje mínimo; si no, detalle completo
                for i, r in enumerate(matches, start=1):
                    if _row_is_minor_defendido(r):
                        card = (
                            f"### Proceso {i}\n"
                            f"**Caso con persona menor de edad.**\n"
                            f"**Defensor(a):** {def_str}\n"
                            f"**Supervisor:** {sup_str}"
                        )
                        dispatcher.utter_message(text=card)
                        continue

                    # Detalle normal (adulto)
                    rad    = _val(_get(r, H_RAD))
                    dep    = _val(_get(r, H_DEP))
                    mun    = _val(_get(r, H_MUN))
                    juz    = _val(_get(r, H_JUZ))
                    inicio = _val(_get(r, H_INICIO))
                    delito = _val(_get(r, H_DELITO))
                    capt   = _val(_get(r, H_CAPT))
                    tcap   = _val(_get(r, H_TIPO_CAP))
                    med    = _val(_get(r, H_MED))
                    centro = _val(_get(r, H_CENTRO))

                    card = (
                        f"### Proceso {i}\n"
                        f"**Radicado:** `{rad}`\n"
                        f"- **Departamento:** {dep}\n"
                        f"- **Municipio:** {mun}\n"
                        f"- **Juzgado:** {juz}\n"
                        f"- **Inicio de proceso:** {inicio}\n"
                        f"- **Delito:** {delito}\n"
                        f"- **Capturado:** {capt}" + (f" ({tcap})" if tcap != 'NA' else "") + "\n"
                        f"- **Medida:** {med}\n"
                        f"- **Centro carcelario:** {centro} \n"
                    )
                    dispatcher.utter_message(text=card)

            dispatcher.utter_message(
                text="\n¿Quieres hacer otra consulta o volver al menú?",
                buttons=[
                    {"title": "🔁 Consultar otro número de documento", "payload": "/consultar_proceso"},
                    {"title": "🏠 Menú principal", "payload": "/saludar"},
                ],
            )

            return [SlotSet("numero_identificacion", None)]

        except Exception as e:
            logger.error("[lookup] Error ejecutando action_lookup_cedula: %s", e)
            logger.error(traceback.format_exc())
            dispatcher.utter_message(text="Ocurrió un problema al consultar tu proceso. Intenta de nuevo en un momento.")
            return [SlotSet("numero_identificacion", None)]

# ------------------------- Regex/validaciones comunes -------------------------
_TIPO_PQRS_MAP = {
    "peticion": "peticion",
    "petición": "peticion",
    "queja": "queja",
    "reclamo": "reclamo",
    "sugerencia": "sugerencia",
    "denuncia": "denuncia",
    "felicitacion": "felicitacion",
    "felicitación": "felicitacion",
    # sinónimos
    "pqr": "peticion", "pqrs": "peticion", "pqrsdf": "peticion",
}

_MEDIO_NOTIF_MAP = {
    "correo": "correo",
    "email": "correo",
    "e-mail": "correo",
    "mail": "correo",
    "telefono": "telefono",
    "teléfono": "telefono",
    "llamada": "telefono",
    "whatsapp": "telefono",  # si decides equipararlo
}

_EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.IGNORECASE)
_NAME_RE  = re.compile(r"^[A-Za-zÁÉÍÓÚÑáéíóúñüÜ'´` ]+$")

def _title_name(s: str) -> str:
    s = _norm_spaces(s)
    return " ".join(w.capitalize() for w in s.split(" "))

def _valid_email(s: str) -> bool:
    return bool(_EMAIL_RE.match((s or "").strip()))

def _valid_nombre(s: str) -> bool:
    s = _norm_spaces(s)
    return len(s) >= 5 and bool(_NAME_RE.match(s))

def _tel_ok(digits: str) -> bool:
    # Ajusta a tu política (7–11 cubre fijos largos y celulares)
    return 7 <= len(digits) <= 11

def _map_medio(s: str) -> Optional[str]:
    """Mapea textos libres como 'por teléfono', 'correo electrónico', 'notificación física' a valores canónicos."""
    t = _strip_accents_lower(_norm_spaces(s))
    if any(w in t for w in ("telefono", "teléfono", "llamada", "celular", "movil", "móvil", "whatsapp")):
        return "telefono"
    if any(w in t for w in ("correo", "email", "e-mail", "mail", "electronico", "electrónico")):
        return "correo"
    if any(w in t for w in ("fisica", "física", "domicilio", "direccion", "dirección")):
        return "fisico"
    return None

# ------------------------- Validadores -------------------------
class ValidateConsultaProcesoForm(FormValidationAction):
    """Valida el número de identificación para la consulta de proceso."""
    def name(self) -> Text:
        return "validate_consulta_proceso_form"

    def validate_numero_identificacion(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        value = _digits(slot_value)
        # Reglas mínimas: >=6 dígitos y <=12 (ajustable)
        if 6 <= len(value) <= 12:
            return {"numero_identificacion": value}
        dispatcher.utter_message(text="El número de identificación debe tener **entre 6 y 12 dígitos**. Intenta de nuevo.")
        return {"numero_identificacion": None}

class ValidatePqrsdfForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_pqrsdf_form"

    def validate_tipo_pqrs(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        raw = _strip_accents_lower(slot_value)
        raw = _norm_spaces(raw)
        choice = _TIPO_PQRS_MAP.get(raw, raw)
        if choice in _TIPO_PQRS_MAP.values():
            return {"tipo_pqrs": choice}
        dispatcher.utter_message(text="Por favor indica si es **petición, queja, reclamo, sugerencia, denuncia o felicitación**.")
        return {"tipo_pqrs": None}

    def validate_nombre_completo(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        s = str(slot_value or "")
        if _valid_nombre(s):
            return {"nombre_completo": _title_name(s)}
        dispatcher.utter_message(text="Por favor ingresa tu **nombre completo** (solo letras y espacios).")
        return {"nombre_completo": None}

    def validate_numero_identificacion(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        value = _digits(slot_value)
        if 6 <= len(value) <= 12:
            return {"numero_identificacion": value}
        dispatcher.utter_message(text="El número de identificación debe tener **entre 6 y 12 dígitos**.")
        return {"numero_identificacion": None}

    def validate_correo_contacto(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        s = str(slot_value or "").strip()
        if _valid_email(s):
            return {"correo_contacto": s}
        dispatcher.utter_message(text="Por favor ingresa un **correo válido** (ej.: nombre@dominio.com).")
        return {"correo_contacto": None}

    def validate_telefono_contacto(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        digits = _digits(slot_value)
        if _tel_ok(digits):
            return {"telefono_contacto": digits}
        dispatcher.utter_message(text="Por favor digita **solo números** (7 a 11 dígitos).")
        return {"telefono_contacto": None}

    def validate_descripcion_caso(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        txt = _norm_spaces(str(slot_value or ""))
        if len(txt) >= 10:
            return {"descripcion_caso": txt}
        dispatcher.utter_message(text="Describe tu caso con **al menos 10 caracteres** para poder orientarte mejor.")
        return {"descripcion_caso": None}

    def validate_medio_notificacion(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        mapped = _map_medio(str(slot_value or ""))
        # Si NO manejas "físico", cambia el set a {"correo", "telefono"} y elimina el botón en el prompt.
        if mapped in {"correo", "telefono", "fisico"}:
            return {"medio_notificacion": mapped}
        dispatcher.utter_message(text="Por favor elige una opción válida.")
        return {"medio_notificacion": None}

class ValidateContactoForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_contacto_form"

    def validate_nombre_contacto(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        s = _norm_spaces(str(slot_value or ""))
        # Permite que vengan nombre y teléfono juntos; si detectamos un número, lo quitamos al validar nombre.
        s_clean = _norm_spaces(re.sub(r"\d+", "", s)).strip()
        if _valid_nombre(s_clean):
            return {"nombre_contacto": _title_name(s_clean)}
        dispatcher.utter_message(text="Indica tu **nombre completo** (mínimo 5 caracteres, solo letras y espacios).")
        return {"nombre_contacto": None}

    def validate_telefono_contacto(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        # Acepta “Sebastian 3138447735” o “313 844 77 35”
        digits = _digits(slot_value)
        if _tel_ok(digits):
            return {"telefono_contacto": digits}
        dispatcher.utter_message(text="Por favor escribe **solo números** (7 a 11 dígitos).")
        return {"telefono_contacto": None}

# ------------------------- Handoff (stub) -------------------------
class ActionHandoff(Action):
    def name(self) -> Text:
        return "action_handoff"

    def run(self, dispatcher, tracker, domain):
        dispatcher.utter_message(
            text=("Te pondré en contacto con un asesor humano. "
                  "Si quieres, puedes dejar tu **nombre** y **teléfono** para adelantar la gestión.")
        )
        return [FollowupAction("contacto_form")]

# ------------------------- Limpieza de slots PQRSDF -------------------------
class ActionResetPqrsSlots(Action):
    """Limpia los slots usados por pqrsdf_form para evitar que contaminen otros flujos."""
    def name(self) -> Text:
        return "action_reset_pqrs_slots"

    def run(self, dispatcher, tracker, domain):
        slots = [
            "tipo_pqrs",
            "nombre_completo",
            "numero_identificacion",
            "correo_contacto",
            "telefono_contacto",
            "descripcion_caso",
            "medio_notificacion",
            "requested_slot",
        ]
        return [SlotSet(s, None) for s in slots]
