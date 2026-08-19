"""Mapa local network_id -> {label, nickname} para enriquecer las alertas.

network_label y nickname viven en el backend de reportes de Insight (no hay REST
directo ni funcionan con el X-User-Token de forma estable). Este archivo es un
snapshot que se refresca con: python actualizar_labels.py  (ver ese script).
"""
import json
import logging
import os
import sys
from html import escape as esc

log = logging.getLogger("labels")

# Junto al .exe si esta empaquetado, si no junto a este archivo.
if getattr(sys, "frozen", False):
    _BASE = os.path.dirname(sys.executable)
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))

_PATH = os.path.join(_BASE, "network_labels.json")


def _load():
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log.info("network_labels.json no encontrado; se omiten label/nickname.")
        return {}
    except json.JSONDecodeError as e:
        log.warning("network_labels.json invalido: %s", e)
        return {}


_MAP = _load()


def reload():
    """Recarga el mapa desde disco (tras actualizar network_labels.json)."""
    global _MAP
    _MAP = _load()
    return len(_MAP)


def get(network_id):
    """Devuelve (label, nickname) para una red; strings vacios si no hay dato."""
    info = _MAP.get(str(network_id), {})
    return info.get("label", "") or "", info.get("nickname", "") or ""


def get_full(network_id):
    """Devuelve dict {label, nickname, customer_account} de una red (strings vacios
    si no hay dato). 'label' = identificador de la casa (Home Identifier);
    'customer_account' = identificador de cuenta de cliente (partner_account_id)."""
    info = _MAP.get(str(network_id), {})
    return {
        "label": info.get("label", "") or "",
        "nickname": info.get("nickname", "") or "",
        "customer_account": info.get("customer_account", "") or "",
    }


def html_lines(network_id):
    """Lineas HTML de nickname/label para el mensaje (vacio si no hay dato)."""
    label, nick = get(network_id)
    out = ""
    if nick:
        out += f"📝 <b>Nickname:</b> {esc(nick)}\n"
    if label:
        out += f"🏷️ <b>Label:</b> {esc(label)}\n"
    return out
