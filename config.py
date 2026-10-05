"""Carga y valida la configuracion desde variables de entorno (.env)."""
import os
import sys
from dotenv import load_dotenv

# Junto al .exe si esta empaquetado, si no junto a este archivo.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))


def _get(name, default=None, required=False):
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Falta la variable requerida '{name}' (revisa el .env).")
    return val


def _list(name):
    raw = _get(name, "") or ""
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


# --- Eero ---
EERO_ADMIN_TOKEN = _get("EERO_ADMIN_TOKEN", required=True)
EERO_ORG_ID = _get("EERO_ORG_ID", "self")

# --- Polling ---
POLL_MINUTES = int(_get("POLL_MINUTES", "10"))
RENOTIFY_MINUTES = int(_get("RENOTIFY_MINUTES", "10"))

# --- Redes excluidas (testing/prueba) ---
# IDs de red a IGNORAR por completo (separados por coma). Sirve para las redes de
# prueba de eero Insights que se caen a proposito y no deben alertar. Se ignoran en
# caidas y en no saludables: no notifican, no cuentan en /estado y se limpian del
# store en silencio (sin aviso de "recuperada"). Para volver a incluir una red,
# quita su ID de aqui y redespliega. Los IDs se comparan como texto.
EXCLUDED_NETWORK_IDS = set(_list("EXCLUDED_NETWORK_IDS"))

# --- Link Insights ---
INSIGHT_URL_TEMPLATE = _get(
    "INSIGHT_URL_TEMPLATE", "https://insight.eero.com/networks/{network_id}"
)
# URL generica (sin id) para el consolidado, derivada de la plantilla.
INSIGHT_BASE = INSIGHT_URL_TEMPLATE.split("/{")[0]

# --- WhatsApp Cloud API (Meta) ---
WA_TOKEN = _get("WA_TOKEN")                     # token permanente (System User)
WA_PHONE_NUMBER_ID = _get("WA_PHONE_NUMBER_ID")  # id del numero emisor
WA_API_VERSION = _get("WA_API_VERSION", "v21.0")
# Dos plantillas aprobadas:
#  - Individual (8 variables): para cada alerta NUEVA.
#  - Consolidado (10 variables): una red por variable, para re-notificaciones/resueltas.
WA_TEMPLATE_INDIVIDUAL = _get("WA_TEMPLATE_INDIVIDUAL", "alerta_individual")
WA_TEMPLATE_INDIVIDUAL_LANG = _get("WA_TEMPLATE_INDIVIDUAL_LANG", "es")
WA_TEMPLATE_CONSOL = _get("WA_TEMPLATE_CONSOL", "recordatorio_consolidado")
WA_TEMPLATE_CONSOL_LANG = _get("WA_TEMPLATE_CONSOL_LANG", "es")
# Numeros destino de las alertas (coma-separados, solo digitos con indicativo).
# Ahora es solo un FALLBACK: la lista real vive en la tabla de receptores
# (autogestion por WhatsApp). Se usa si no hay DATABASE_URL o la tabla esta vacia.
WA_RECIPIENTS = _list("WA_RECIPIENTS")

# Bot EXCLUSIVO: si es true, el bot solo responde a numeros que esten de ALTA
# (suscritos y activos); a los no suscritos o dados de baja los ignora (sin
# respuesta). Nuevos numeros los agrega un numero YA de alta con "4 <numero>"
# (o via WA_RECIPIENTS al desplegar). false = responde a cualquiera (comportamiento
# antiguo). Si la DB no responde, por seguridad de UX se responde igual (no bloquea).
BOT_SOLO_SUSCRITOS = _get("BOT_SOLO_SUSCRITOS", "true").lower() in ("1", "true", "yes", "si")

# --- Receptores de alertas (Postgres, autogestion alta/baja por WhatsApp) ---
# Cadena de conexion de Render (External Database URL). Si falta, se usa
# WA_RECIPIENTS como antes.
DATABASE_URL = _get("DATABASE_URL")
SUBSCRIBERS_SCHEMA = _get("SUBSCRIBERS_SCHEMA", "eero_insight_whatsapp")
# Modo SSL para Postgres. 'require' sirve para la URL interna y externa de
# Render. Si la interna diera problema de SSL, se puede poner 'prefer' o
# 'disable' sin tocar codigo.
DB_SSLMODE = _get("DB_SSLMODE", "require")
# Token que TU inventas para verificar el webhook con Meta.
WA_VERIFY_TOKEN = _get("WA_VERIFY_TOKEN", "cambia_esta_palabra")

# Consolidado: WA_BATCH_MAX = numero de variables de la plantilla (una red por
# variable). Debe coincidir con la plantilla aprobada en Meta.
WA_BATCH_MAX = int(_get("WA_BATCH_MAX", "10"))
WA_BODY_BUDGET = int(_get("WA_BODY_BUDGET", "900"))

# --- Envio automatico (proactivo) a Meta — MODELO HIBRIDO ---
# Meta COBRA por los mensajes proactivos (plantillas: alertas, re-notificaciones,
# reporte diario, cierres). Cualquiera que le ESCRIBA al bot puede CONSULTAR gratis
# por el menu (opciones 1 y 2). El push automatico va SOLO a los numeros de
# ALERTAS_PUSH_NUMBERS (max 2), para controlar el costo:
#  - ALERTAS_PUSH_ENABLED=false -> nadie recibe push; todos solo consultan.
#  - ALERTAS_PUSH_ENABLED=true  -> los numeros de ALERTAS_PUSH_NUMBERS reciben
#    alertas/renotif/reporte automaticos; los demas siguen solo consultando.
ALERTAS_PUSH_ENABLED = _get("ALERTAS_PUSH_ENABLED", "false").lower() in ("1", "true", "yes", "si")
# Hasta 2 numeros (coma-separados, con indicativo) que reciben el push automatico
# EN MODO HIBRIDO. Si hay mas de 2, se toman solo los 2 primeros.
_push_raw = _list("ALERTAS_PUSH_NUMBERS")
ALERTAS_PUSH_NUMBERS = _push_raw[:2]
ALERTAS_PUSH_TRUNCADO = len(_push_raw) > 2  # se pasaron mas de 2 (se recorto)
# Cuando el push esta activado, este interruptor decide A QUIEN va:
#  - true (HIBRIDO): push SOLO a ALERTAS_PUSH_NUMBERS (autorizados, max 2).
#  - false (COMPLETO): push a TODOS los numeros de alta (tabla de suscriptores;
#    si no hay DB o esta vacia, WA_RECIPIENTS). Sin tope.
ALERTAS_MODO_HIBRIDO = _get("ALERTAS_MODO_HIBRIDO", "true").lower() in ("1", "true", "yes", "si")

# --- Redes no saludables (modulo "Redes con problemas") ---
UNHEALTHY_ENABLED = _get("UNHEALTHY_ENABLED", "true").lower() in ("1", "true", "yes", "si")
# Modo de notificacion de las no saludables:
#  - true (TIEMPO REAL): en el MISMO ciclo de las caidas (cada POLL_MINUTES) se
#    notifican SOLO las CRITICAS: alerta individual al aparecer (o al escalar a
#    critica), re-notificacion cada RENOTIFY_MINUTES dentro del MISMO consolidado de
#    las caidas, y aviso "Estado saludable" al resolverse. Las NO criticas no se
#    envian (ver UNHEALTHY_SOLO_CRITICAS). No se envia el reporte diario.
#  - false (DIARIO): UN consolidado al dia a las UNHEALTHY_REPORT_HOUR (ver abajo).
UNHEALTHY_TIEMPO_REAL = _get("UNHEALTHY_TIEMPO_REAL", "true").lower() in ("1", "true", "yes", "si")
# Tiempo real: true (default) = las NO criticas se ignoran por completo (no se
# rastrean ni aparecen en el menu). false = se rastrean en silencio y se ven en
# las opciones 1 y 2 (igual nunca envian WhatsApp).
UNHEALTHY_SOLO_CRITICAS = _get("UNHEALTHY_SOLO_CRITICAS", "true").lower() in ("1", "true", "yes", "si")
# --- Reporte diario (solo si UNHEALTHY_TIEMPO_REAL=false) ---
# Se envia UN consolidado por WhatsApp en la manana y se consultan con /estado el
# resto del dia. (Las CAIDAS siguen en tiempo real.)
# Hora local (America/Bogota) del envio del reporte diario.
UNHEALTHY_REPORT_HOUR = int(_get("UNHEALTHY_REPORT_HOUR", "9"))
# Si es true, el reporte matutino incluye SOLO las criticas (las no criticas
# quedan solo en /estado). Por defecto false = incluye todas.
UNHEALTHY_REPORT_CRITICAL_ONLY = _get(
    "UNHEALTHY_REPORT_CRITICAL_ONLY", "false").lower() in ("1", "true", "yes", "si")

# --- General ---
DRY_RUN = _get("DRY_RUN", "true").lower() in ("1", "true", "yes", "si")
DB_PATH = _get("DB_PATH", os.path.join(BASE_DIR, "alertas.db"))
PORT = int(_get("PORT", "10000"))  # Render inyecta PORT
