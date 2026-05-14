import base64
import ipaddress
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Optional, Tuple

from fastapi import HTTPException, Request
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

RP_NAME = "Reading List"
USER_NAME = "user"
SESSION_COOKIE = "rl_session"


def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def b64u_decode(s: str) -> bytes:
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def get_rp_info(request: Request) -> Tuple[str, str]:
    host = request.url.hostname or "localhost"
    origin = f"{request.url.scheme}://{request.url.netloc}"
    return host, origin


def _require_valid_rp_id(host: str) -> None:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return  # not an IP → valid RP ID
    raise HTTPException(
        400,
        f"WebAuthn does not allow IP addresses as relying-party IDs. "
        f"Open the app at http://localhost:<port> instead of http://{host}:<port>.",
    )


class AuthStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        # Pending challenges live in memory only — restart drops them, which is fine.
        self._pending_reg_challenge: Optional[bytes] = None
        self._pending_reg_user_id: Optional[bytes] = None
        self._pending_auth_challenge: Optional[bytes] = None

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    def is_registered(self) -> bool:
        return "credential" in self._load()

    def has_session(self, token: Optional[str]) -> bool:
        if not token:
            return False
        return token in self._load().get("sessions", [])

    def revoke_session(self, token: str) -> None:
        with self._lock:
            d = self._load()
            if not d:
                return
            d["sessions"] = [s for s in d.get("sessions", []) if s != token]
            self._save(d)

    def begin_registration(self, request: Request) -> str:
        with self._lock:
            if self.is_registered():
                raise HTTPException(409, "passkey already set up — delete the auth file to re-register")
            rp_id, _ = get_rp_info(request)
            _require_valid_rp_id(rp_id)
            user_id = secrets.token_bytes(16)
            options = generate_registration_options(
                rp_id=rp_id,
                rp_name=RP_NAME,
                user_id=user_id,
                user_name=USER_NAME,
                user_display_name=USER_NAME,
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.REQUIRED,
                    user_verification=UserVerificationRequirement.PREFERRED,
                ),
            )
            self._pending_reg_challenge = options.challenge
            self._pending_reg_user_id = user_id
            return options_to_json(options)

    def complete_registration(self, request: Request, credential: dict) -> str:
        with self._lock:
            if self.is_registered():
                raise HTTPException(409, "passkey already set up")
            if self._pending_reg_challenge is None:
                raise HTTPException(400, "no pending registration — call /auth/register/begin first")
            rp_id, origin = get_rp_info(request)
            try:
                verification = verify_registration_response(
                    credential=credential,
                    expected_challenge=self._pending_reg_challenge,
                    expected_rp_id=rp_id,
                    expected_origin=origin,
                )
            except Exception as e:
                raise HTTPException(400, f"registration verification failed: {e}")

            transports = credential.get("response", {}).get("transports") or []
            d = self._load()
            d["user_id"] = b64u_encode(self._pending_reg_user_id or b"")
            d["rp_id"] = rp_id
            d["credential"] = {
                "id": b64u_encode(verification.credential_id),
                "public_key": b64u_encode(verification.credential_public_key),
                "sign_count": verification.sign_count,
                "transports": transports,
            }
            token = secrets.token_urlsafe(32)
            d.setdefault("sessions", []).append(token)
            self._save(d)
            self._pending_reg_challenge = None
            self._pending_reg_user_id = None
            return token

    def begin_authentication(self, request: Request) -> str:
        with self._lock:
            if not self.is_registered():
                raise HTTPException(404, "no passkey set up")
            d = self._load()
            cred = d["credential"]
            rp_id, _ = get_rp_info(request)
            _require_valid_rp_id(rp_id)
            allow = [PublicKeyCredentialDescriptor(id=b64u_decode(cred["id"]))]
            options = generate_authentication_options(
                rp_id=rp_id,
                allow_credentials=allow,
                user_verification=UserVerificationRequirement.PREFERRED,
            )
            self._pending_auth_challenge = options.challenge
            return options_to_json(options)

    def complete_authentication(self, request: Request, credential: dict) -> str:
        with self._lock:
            if not self.is_registered():
                raise HTTPException(404, "no passkey set up")
            if self._pending_auth_challenge is None:
                raise HTTPException(400, "no pending authentication — call /auth/login/begin first")
            d = self._load()
            cred = d["credential"]
            rp_id, origin = get_rp_info(request)
            try:
                verification = verify_authentication_response(
                    credential=credential,
                    expected_challenge=self._pending_auth_challenge,
                    expected_rp_id=rp_id,
                    expected_origin=origin,
                    credential_public_key=b64u_decode(cred["public_key"]),
                    credential_current_sign_count=cred["sign_count"],
                )
            except Exception as e:
                raise HTTPException(401, f"authentication failed: {e}")
            cred["sign_count"] = verification.new_sign_count
            token = secrets.token_urlsafe(32)
            d.setdefault("sessions", []).append(token)
            self._save(d)
            self._pending_auth_challenge = None
            return token
