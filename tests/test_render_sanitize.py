"""Unit tests for the Render diagnostics sanitization layer.

These tests are deliberately dependency-free (stdlib unittest) so they run
both under pytest in CI and standalone via ``python -m unittest``. They do
not require PostgreSQL or any application dependency.

All credential-like strings below are low-entropy fixtures for testing
redaction; none is a real secret.
"""

from __future__ import annotations

import json
import unittest

from scripts.render_sanitize import redact_json, sanitize_json, sanitize_text, sanitize_token


class SanitizeTextTests(unittest.TestCase):
    def test_authorization_header_redacted(self):
        line = 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890'  # gitleaks:allow
        out = sanitize_text(line)
        self.assertNotIn('abcdefghijklmnopqrstuvwxyz1234567890', out)
        self.assertIn('Authorization', out)
        self.assertIn('[REDACTED]', out)

    def test_bearer_token_in_prose_redacted(self):
        out = sanitize_text('request used bearer abcdefghijklmnopqrstuvwxyz12 token')  # gitleaks:allow
        self.assertNotIn('abcdefghijklmnopqrstuvwxyz12', out)
        self.assertIn('[REDACTED]', out)

    def test_render_api_key_redacted(self):
        secret = 'rnd_0123456789012345678901234567890'  # gitleaks:allow
        out = sanitize_text(f'api call with {secret} failed')
        self.assertNotIn(secret, out)
        self.assertIn('[REDACTED]', out)

    def test_postgres_url_credentials_redacted_host_preserved(self):
        out = sanitize_text('postgres://apex_app:hunter2secret@dpg-abc.render.com:5432/apex?sslmode=verify-full')  # gitleaks:allow
        self.assertNotIn('hunter2secret', out)
        self.assertNotIn('apex_app:', out)
        self.assertIn('postgres://[REDACTED]@dpg-abc.render.com:5432/apex', out)

    def test_redis_url_credentials_redacted(self):
        out = sanitize_text('redis://default:passw0rd123@redis-xyz.render.com:6379')  # gitleaks:allow
        self.assertNotIn('passw0rd123', out)
        self.assertIn('redis://[REDACTED]@redis-xyz.render.com:6379', out)

    def test_jwt_redacted(self):
        token = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1'  # gitleaks:allow
        out = sanitize_text(f'cookie jar had {token} inside')
        self.assertNotIn(token, out)

    def test_private_key_block_redacted(self):
        block = '-----BEGIN RSA PRIVATE KEY-----\nMIIB0123456789abcdef\n-----END RSA PRIVATE KEY-----'
        out = sanitize_text(f'error: {block} rejected')
        self.assertNotIn('MIIB0123456789abcdef', out)
        self.assertNotIn('BEGIN RSA PRIVATE KEY', out)

    def test_password_assignment_redacted(self):
        out = sanitize_text('psql: password=hunter2secret for role apex_app')  # gitleaks:allow
        self.assertNotIn('hunter2secret', out)
        self.assertIn('password=[REDACTED]', out)

    def test_secret_json_assignment_redacted(self):
        out = sanitize_text('config had "token": "abcdef123456" set')  # gitleaks:allow
        self.assertNotIn('abcdef123456', out)
        self.assertIn('[REDACTED]', out)

    def test_cookie_headers_redacted(self):
        out = sanitize_text('Set-Cookie: session=deadbeefcafe0123456789; Path=/')  # gitleaks:allow
        self.assertNotIn('deadbeefcafe0123456789', out)
        out2 = sanitize_text('cookie: apex=feedface0123456789abcd')  # gitleaks:allow
        self.assertNotIn('feedface0123456789abcd', out2)

    def test_provider_token_prefixes_redacted(self):
        for sample in ('ghp_0123456789012345678901234567890123456789',  # gitleaks:allow
                       'AKIA0123456789ABCDEF',  # gitleaks:allow
                       'xoxb-01234567890123-01234567890123',  # gitleaks:allow
                       'sk-live-0123456789012345678901234567890'):  # gitleaks:allow
            out = sanitize_text(f'leaked {sample} in logs')
            self.assertNotIn(sample, out, sample)

    def test_commit_sha_preserved(self):
        line = 'deploy 0e7eea1651ee408d8a78243175b63b8e1174f5ad is live'
        self.assertEqual(sanitize_text(line), line)

    def test_gunicorn_request_log_preserved(self):
        line = ('request_complete request_id=1f0b8f64-6e5e-4f1a-9d6a-2f7f6b3a1c11 '
                'endpoint=api.login method=POST status=200 duration_ms=42')
        self.assertEqual(sanitize_text(line), line)

    def test_health_body_preserved(self):
        body = '{"status": "live"}'
        self.assertEqual(sanitize_text(body), body)

    def test_plain_url_preserved(self):
        url = 'https://apex.viability.in/health/ready'
        self.assertEqual(sanitize_text(f'probed {url} ok'), f'probed {url} ok')

    def test_idempotent(self):
        dirty = ('Authorization: Bearer abcdefghijklmnopqrstuvwxyz12 '  # gitleaks:allow
                 'postgres://u:hunter2secret@h.example.com/db password=abc12345')  # gitleaks:allow
        once = sanitize_text(dirty)
        self.assertEqual(sanitize_text(once), once)


class RedactJsonTests(unittest.TestCase):
    def test_secret_keys_redacted_nested(self):
        payload = {
            'serviceDetails': {'password': 'hunter2secret', 'url': 'https://srv.example.com'},  # gitleaks:allow
            'nested': [{'api_key': 'abcdef123456', 'name': 'kept'}],  # gitleaks:allow
        }
        out = redact_json(payload)
        self.assertEqual(out['serviceDetails']['password'], '[REDACTED]')
        self.assertEqual(out['serviceDetails']['url'], 'https://srv.example.com')
        self.assertEqual(out['nested'][0]['api_key'], '[REDACTED]')
        self.assertEqual(out['nested'][0]['name'], 'kept')

    def test_camel_case_secret_key_redacted(self):
        payload = {'registryCredential': {'id': 'crc-1', 'name': 'ghcr'}}
        out = redact_json(payload)
        self.assertEqual(out['registryCredential'], '[REDACTED]')

    def test_render_service_fields_preserved(self):
        payload = {
            'id': 'srv-abc123', 'name': 'apex-flow', 'type': 'web_service',
            'repo': 'https://github.com/nandan503/Apex-Flow.git', 'branch': 'main',
            'suspended': 'not_suspended',
            'serviceDetails': {'healthCheckPath': '/health/live',
                               'numInstances': 2, 'plan': 'starter'},
        }
        self.assertEqual(redact_json(payload), payload)

    def test_string_values_pass_through_text_layer(self):
        payload = {'message': 'connect postgres://apex:hunter2secret@db.example.com/x'}  # gitleaks:allow
        out = redact_json(payload)
        self.assertNotIn('hunter2secret', json.dumps(out))


class CombinedPipelineTests(unittest.TestCase):
    def test_sanitize_json_removes_literal_token(self):
        payload = {'error': 'request test-token-abcdefghijklmnop failed', 'status': 500}  # gitleaks:allow
        out = sanitize_json(payload, token='test-token-abcdefghijklmnop')  # gitleaks:allow
        self.assertNotIn('test-token-abcdefghijklmnop', out)  # gitleaks:allow
        self.assertIn('[REDACTED]', out)
        # Result must still be valid JSON.
        parsed = json.loads(out)
        self.assertEqual(parsed['status'], 500)

    def test_sanitize_token_requires_meaningful_length(self):
        self.assertEqual(sanitize_token('short', 'short'), 'short')
        self.assertEqual(sanitize_token('has longvalue-1234 here', 'longvalue-1234'),
                         'has [REDACTED] here')

    def test_sanitize_json_output_is_stable_json(self):
        payload = {'b': 1, 'a': {'c': [1, 2, {'password': 'x'}]}}  # gitleaks:allow
        parsed = json.loads(sanitize_json(payload))
        self.assertEqual(parsed['a']['c'][2]['password'], '[REDACTED]')
        self.assertEqual(parsed['b'], 1)


if __name__ == '__main__':
    unittest.main()
