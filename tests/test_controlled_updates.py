from __future__ import annotations

import hashlib
import hmac
import json
import unittest
import uuid
from pathlib import Path

from fastapi import HTTPException

from manager import main
from manager.config import settings
from manager.contracts import ClientConfigurationUpdate, ClientCreate
from manager.database import Database, utc_now
from manager.security import OperatorIdentity


class FakeRequest:
    def __init__(self, body: bytes): self._body = body
    async def body(self): return self._body


def run_immediate(coroutine):
    """Execute an async function that only awaits immediately-resolved test doubles."""
    try: coroutine.send(None)
    except StopIteration as completed: return completed.value
    raise AssertionError("Test coroutine unexpectedly performed asynchronous I/O")


class ControlledUpdateTests(unittest.TestCase):
    def setUp(self):
        runtime=Path(__file__).parent/"_runtime";runtime.mkdir(exist_ok=True)
        self.path=runtime/f"controlled-{uuid.uuid4().hex}.db";main.db=Database(self.path);main.db.initialize()
        self.admin=OperatorIdentity("test-admin","super_admin")

    def tearDown(self): self.path.unlink(missing_ok=True)

    def test_legacy_configuration_is_migrated_encrypted_masked_and_versioned(self):
        client=main.create_client(ClientCreate(name="Config Client",code="config-client"),actor="test")["client"]
        secret="never-store-me-in-plaintext"
        main.db.execute("INSERT INTO client_infrastructure(id,client_id,provider,component,config_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",("infra",client["id"],"render","services",json.dumps({"region":"oregon","environment_variables":{"API_SECRET":secret}}),"active",utc_now(),utc_now()))
        main.db.initialize()
        raw=main.db.one("SELECT * FROM client_infrastructure WHERE id='infra'")
        self.assertNotIn(secret,raw["environment_variables_json"])
        current=main.get_client_configuration(client["id"],self.admin)
        self.assertEqual(current["infrastructure"][0]["environment_variables"]["API_SECRET"],"••••••••")
        updated=main.update_client_configuration(client["id"],ClientConfigurationUpdate(infrastructure_id="infra",provider_config={"region":"frankfurt"},environment_variables={"API_SECRET":"••••••••"},change_summary="Move managed region"),self.admin)
        self.assertEqual(updated["infrastructure"][0]["config_version"],2)
        self.assertEqual(len(updated["versions"]),1)

    def test_webhook_signature_and_delivery_deduplication(self):
        old_secret,old_repo=settings.github_webhook_secret,settings.github_repository_services
        object.__setattr__(settings,"github_webhook_secret","webhook-test-secret")
        object.__setattr__(settings,"github_repository_services","https://github.com/vtab/services")
        try:
            payload={"ref":"refs/heads/main","after":"a"*40,"head_commit":{"message":"safe release"},"repository":{"html_url":"https://github.com/vtab/services"}}
            body=json.dumps(payload,separators=(",", ":")).encode();signature="sha256="+hmac.new(b"webhook-test-secret",body,hashlib.sha256).hexdigest()
            first=run_immediate(main.github_webhook(FakeRequest(body),signature,"push","delivery-1"))
            second=run_immediate(main.github_webhook(FakeRequest(body),signature,"push","delivery-1"))
            self.assertFalse(first["duplicate"]);self.assertTrue(second["duplicate"])
            with self.assertRaises(HTTPException) as invalid:
                run_immediate(main.github_webhook(FakeRequest(body),"sha256=bad","push","delivery-2"))
            self.assertEqual(invalid.exception.status_code,401)
        finally:
            object.__setattr__(settings,"github_webhook_secret",old_secret);object.__setattr__(settings,"github_repository_services",old_repo)


if __name__=="__main__":unittest.main()
