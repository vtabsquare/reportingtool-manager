from __future__ import annotations

import base64
import json
import os
from copy import deepcopy
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings


SECRET_MARKERS=("SECRET","PASSWORD","TOKEN","PRIVATE","SERVICE_ROLE","API_KEY","WEBHOOK","CONNECTION_STRING","DATABASE_URL")
MASK="••••••••"


def is_secret_name(name:str)->bool:
    upper=name.upper()
    return any(marker in upper for marker in SECRET_MARKERS)


def encrypt_environment(values:dict[str,str])->str:
    payload=json.dumps({str(k):str(v) for k,v in values.items()},sort_keys=True,separators=(",",":")).encode("utf-8")
    nonce=os.urandom(12)
    aad=f"vtab-manager-config:{settings.config_key_id}".encode("utf-8")
    ciphertext=AESGCM(settings.encryption_key_bytes).encrypt(nonce,payload,aad)
    return json.dumps({"v":1,"kid":settings.config_key_id,"alg":"AES-256-GCM","nonce":base64.urlsafe_b64encode(nonce).decode("ascii"),"ciphertext":base64.urlsafe_b64encode(ciphertext).decode("ascii")},separators=(",",":"))


def decrypt_environment(value:str|None)->dict[str,str]:
    if not value:return {}
    try:
        envelope=json.loads(value)
        if isinstance(envelope,dict) and envelope.get("alg")=="AES-256-GCM":
            nonce=base64.urlsafe_b64decode(envelope["nonce"])
            ciphertext=base64.urlsafe_b64decode(envelope["ciphertext"])
            aad=f"vtab-manager-config:{envelope.get('kid') or settings.config_key_id}".encode("utf-8")
            decoded=AESGCM(settings.encryption_key_bytes).decrypt(nonce,ciphertext,aad)
            data=json.loads(decoded)
            return {str(k):str(v) for k,v in data.items()}
        # Non-destructive compatibility for pre-encryption JSON rows. They are
        # re-encrypted the next time the configuration is saved.
        if isinstance(envelope,dict):return {str(k):str(v) for k,v in envelope.items()}
    except Exception:
        return {}
    return {}


def mask_environment(values:dict[str,str])->dict[str,str]:
    return {key:(MASK if value and is_secret_name(key) else value) for key,value in values.items()}


def merge_masked_environment(existing:dict[str,str],incoming:dict[str,str])->dict[str,str]:
    merged=deepcopy(existing)
    for key,value in incoming.items():
        if value==MASK:continue
        if value in (None,""):merged.pop(key,None)
        else:merged[str(key)]=str(value)
    return merged
