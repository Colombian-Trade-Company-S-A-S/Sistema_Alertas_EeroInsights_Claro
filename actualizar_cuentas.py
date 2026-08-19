"""Asigna el 'Identificador de cuenta de cliente' (customer account /
partner_account_id) de varias redes en eero Insight, via API con el token Admin, y
refresca el cache local network_labels.json que usa el bot (cuenta + identificador
de la casa + nickname).

El customer account NO se escribe directo en la red: se crea un registro atado a
los SERIALES de los eeros de la red:
    POST /2.2/customer_accounts   { "partner_account_id": "<cuenta>", "serials": [...] }
Reversible con  DELETE /2.2/customer_accounts/:partner_account_id .

Como el objeto de la red no refleja el customer_account cuando la red esta OFFLINE,
el bot lo lee del cache local network_labels.json (que este script mantiene).

Uso:
    python actualizar_cuentas.py --dry     # muestra que haria (no escribe)
    python actualizar_cuentas.py           # crea, verifica y refresca el cache local

Edita MAPEO para agregar o cambiar redes. Se detiene ante el primer fallo.
"""
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import json
import os
import sys

import requests

import config

BASE = "https://api-user.e2ro.com"
LABELS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "network_labels.json")

# network_id (texto) -> numero de cuenta de cliente (partner_account_id). Editar aqui.
MAPEO = {
    "22525143": "46753884",
    "21832282": "47607618",
    "21931792": "48365851",
    "21813619": "47558225",
}


def _sesion():
    s = requests.Session()
    s.headers.update({"X-User-Token": config.EERO_ADMIN_TOKEN})
    return s


def _label(s, nid, params=None):
    r = s.get(f"{BASE}/2.2/networks/{nid}/label", params=params, timeout=25)
    if r.status_code == 404:
        return ""
    r.raise_for_status()
    return (r.json().get("data") or {}).get("label") or ""


def serials_de_red(s, nid):
    r = s.get(f"{BASE}/2.2/networks/{nid}/eeros", timeout=25)
    r.raise_for_status()
    return [e["serial"] for e in (r.json().get("data") or []) if e.get("serial")]


def cuenta_de_red(s, nid):
    r = s.get(f"{BASE}/2.2/networks/{nid}", timeout=25)
    r.raise_for_status()
    ca = (r.json().get("data") or {}).get("customer_account")
    return (ca or {}).get("partner_account_id") if ca else None


def account_obj(s, cuenta):
    """El objeto customer account (o None si no existe)."""
    r = s.get(f"{BASE}/2.2/customer_accounts/{cuenta}", timeout=25)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json().get("data")


def crear_account(s, cuenta, serials):
    r = s.post(f"{BASE}/2.2/customer_accounts",
               json={"partner_account_id": cuenta, "serials": serials},
               headers={"content-type": "application/json"}, timeout=25)
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    return True, r.json().get("data")


def _load_labels():
    try:
        with open(LABELS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_labels(data):
    with open(LABELS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    dry = "--dry" in sys.argv
    s = _sesion()
    labels = _load_labels()
    cambios_cache = 0
    print(f"{'DRY-RUN (no escribe)' if dry else 'ACTUALIZANDO'} — {len(MAPEO)} red(es)\n")

    for nid, cuenta in MAPEO.items():
        try:
            actual = cuenta_de_red(s, nid)          # None si offline o sin cuenta
            account = account_obj(s, cuenta)         # existe la cuenta?
            serials = serials_de_red(s, nid)
        except requests.HTTPError as e:
            print(f"✗ {nid}: no pude leer red/cuenta/seriales ({e}). Me detengo.")
            return 1

        red_url = f"/2.2/networks/{nid}"
        asociada = (actual == cuenta) or (account and red_url in (account.get("networks") or []))

        if not asociada:
            if actual not in (None, ""):
                print(f"! {nid}: la red ya tiene OTRA cuenta '{actual}'. Se OMITE.")
                continue
            if account is not None:  # existe pero para otra(s) red(es)
                print(f"! {nid}: el partner_account_id '{cuenta}' ya existe para "
                      f"{account.get('networks')}. Se OMITE (evita colision).")
                continue
            if dry:
                print(f"• {nid} ({', '.join(serials)}) -> crear cuenta '{cuenta}'  (pendiente)")
                continue
            ok, detalle = crear_account(s, cuenta, serials)
            if not ok:
                print(f"✗ {nid}: fallo al crear cuenta '{cuenta}' ({detalle}). Me detengo.")
                return 1
            asociada = True
            print(f"✓ {nid}: cuenta '{cuenta}' creada (serials={serials}).")
        else:
            print(f"= {nid}: ya asociada a la cuenta '{cuenta}'.")

        if dry:
            continue

        # Refrescar cache local (cuenta + identificador de la casa + nickname).
        label = _label(s, nid)                                   # Home Identifier
        nickname = _label(s, nid, params={"labelType": "SpecialMarket"})
        entry = labels.get(nid, {})
        entry["label"] = label
        entry["nickname"] = nickname
        entry["customer_account"] = cuenta
        labels[nid] = entry
        cambios_cache += 1
        print(f"   cache: cuenta='{cuenta}' casa='{label or '-'}' nickname='{nickname or '-'}'")

    if not dry and cambios_cache:
        _save_labels(labels)
        print(f"\nnetwork_labels.json actualizado ({cambios_cache} red(es)).")
    print("\nDRY-RUN terminado (no se escribio nada)." if dry else "\nListo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())