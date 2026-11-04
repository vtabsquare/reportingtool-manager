from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException

from .config import settings


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_agent_token() -> str:
    return secrets.token_urlsafe(36)


class OperatorIdentity(str):
    """A database-compatible actor string carrying its authorization role."""
    role:str
    def __new__(cls,actor:str,role:str):
        value=str.__new__(cls,actor);value.role=role;return value

    @property
    def actor(self)->str:return str(self)


def require_operator(x_vtab_manager_key: str | None = Header(default=None)) -> OperatorIdentity:
    if not x_vtab_manager_key:
        raise HTTPException(status_code=401, detail="A valid VTAB Manager operator key is required.")
    for candidate,metadata in settings.operator_keys.items():
        if hmac.compare_digest(x_vtab_manager_key,candidate):
            return OperatorIdentity(metadata["actor"],metadata["role"])
    raise HTTPException(status_code=401, detail="A valid VTAB Manager operator key is required.")


def authorize(identity:OperatorIdentity,roles:set[str])->None:
    if identity.role not in roles:
        raise HTTPException(status_code=403,detail="This operator role is not authorized for the requested action.")


def verify_client_admin(expected_hash:str|None,supplied_token:str|None)->None:
    if not expected_hash or not supplied_token or not hmac.compare_digest(expected_hash,token_hash(supplied_token)):
        raise HTTPException(status_code=403,detail="Client administrator authorization is required.")


def verify_agent(expected_hash: str, supplied_token: str | None) -> None:
    if not supplied_token or not hmac.compare_digest(expected_hash, token_hash(supplied_token)):
        raise HTTPException(status_code=401, detail="Invalid client connector token.")

