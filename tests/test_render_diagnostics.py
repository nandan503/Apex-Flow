"""Offline end-to-end tests for the Render diagnostics collector.

Runs scripts/render_diagnostics.main() against a local mock Render API and a
mock canonical origin (stdlib http.server on an ephemeral loopback port).
No real Render credentials, network or database are involved. All
credential-like strings are fixtures for asserting redaction.

These tests are deliberately dependency-free (stdlib unittest) so they run
both under pytest in CI and standalone via ``python -m unittest``.
"""

from __future__ import annotations

import http.server
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from scripts import render_diagnostics

TOKEN = 'test-render-token-0123456789abcdef'  # gitleaks:allow
ACCOUNT_EMAIL = 'ops@example.invalid'  # fixture; must never reach artifacts
COMMIT_SHA = 'a' * 40
OTHER_SHA = 'b' * 40
SSH_VALUE = 'ssh-apex-flow-7mr9.onrender.com'  # dropped-field fixture

SERVICE = {
    'id': 'srv-apexflow',
    'name': 'apex-flow-web',
    'type': 'web_service',
    'repo': 'https://github.com/nandan503/Apex-Flow.git',
    'branch': 'main',
    'ownerId': 'tea-test',
    'autoDeploy': 'yes',
    'suspended': 'not_suspended',
    'suspenders': [],
    'createdAt': '2026-01-01T00:00:00Z',
    'updatedAt': '2026-09-08T00:00:00Z',
    'serviceDetails': {
        'url': 'https://apex-flow-7mr9.onrender.com',
        'healthCheckPath': '/health/live',
        'numInstances': 2,
        'ipAllowList': [],
        'sshAddress': SSH_VALUE,
    },
}

DEPLOY = {
    'id': 'dep-1',
    'commit': {'id': COMMIT_SHA, 'message': 'Merge pull request #2',
               'createdAt': '2026-09-07T00:00:00Z'},
    'status': 'live',
    'trigger': 'new_commit',
    'createdAt': '2026-09-07T00:00:00Z',
    'finishedAt': '2026-09-07T00:01:00Z',
}

LOGS = {
    'hasMore': False,
    'nextStartTime': '2026-09-08T12:00:00Z',
    'nextEndTime': '2026-09-08T13:30:00Z',
    'logs': [
        {'id': 'log-1', 'timestamp': '2026-09-08T13:00:00Z',
         'message': 'request_complete request_id=1f0b8f64-6e5e-4f1a-9d6a-2f7f6b3a1c11 '
                    'endpoint=api.login method=POST status=200 duration_ms=42',
         'labels': [{'name': 'level', 'value': 'info'},
                    {'name': 'type', 'value': 'app'}]},
        {'id': 'log-2', 'timestamp': '2026-09-08T13:01:00Z',
         'message': 'worker failed to connect '
                    'postgres://apex_app:hunter2secret@dpg-abc.render.com/apex',  # gitleaks:allow
         'labels': [{'name': 'level', 'value': 'error'},
                    {'name': 'type', 'value': 'app'}]},
    ],
}

UNRELATED_SERVICE = dict(SERVICE, id='srv-other', name='other-web',
                         repo='https://github.com/someone/else.git',
                         serviceDetails={'url': 'https://other.onrender.com'})


class MockServer(http.server.BaseHTTPRequestHandler):
    """Mock Render API + canonical origin. Behavior via class attributes."""

    ready_status = 200
    ready_body = {'status': 'ready'}
    services_payload = None   # defaults to [SERVICE]
    deploys_payload = None    # defaults to [DEPLOY]
    auth_failure = False      # applies to /v1/* routes only

    def log_message(self, fmt, *args):  # silence request logging
        pass

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = self.path.split('?')[0].rstrip('/')
        if self.auth_failure and route.startswith('/v1/'):
            self._json(401, {'message': 'invalid api token'})
        elif route == '/v1/users':
            self._json(200, {'email': ACCOUNT_EMAIL, 'name': 'Ops'})
        elif route == '/v1/services':
            payload = self.services_payload or [SERVICE]
            self._json(200, [{'service': s, 'cursor': 'c'} for s in payload])
        elif route == '/v1/services/srv-apexflow/deploys':
            payload = self.deploys_payload or [DEPLOY]
            self._json(200, [{'deploy': d, 'cursor': 'd'} for d in payload])
        elif route == '/v1/services/srv-apexflow/instances':
            self._json(200, [{'id': 'inst-1',
                              'createdAt': '2026-09-07T00:00:00Z'}])
        elif route == '/v1/logs':
            self._json(200, LOGS)
        elif route == '/health/live':
            self._json(200, {'status': 'live'})
        elif route == '/health/ready':
            self._json(self.ready_status, self.ready_body)
        elif route == '':
            self._json(200, {'message': 'mock root'})
        else:
            self._json(404, {'message': 'not found'})


class IdentificationTests(unittest.TestCase):
    """Pure-function tests for repo matching and service identification."""

    def test_repository_match_target_forms(self):
        expected = 'github.com/nandan503/apex-flow'
        self.assertEqual(
            render_diagnostics.repository_match_target('nandan503/Apex-Flow'),
            expected)
        self.assertEqual(
            render_diagnostics.repository_match_target(
                'https://github.com/nandan503/Apex-Flow'),
            expected)
        self.assertEqual(
            render_diagnostics.repository_match_target(
                'https://github.com/nandan503/Apex-Flow.git'),
            expected)
        self.assertEqual(
            render_diagnostics.repository_match_target(
                'git@github.com:nandan503/Apex-Flow.git'),
            expected)
        self.assertEqual(
            render_diagnostics.repository_match_target(''),
            '')

    def test_identify_by_repository_url(self):
        method, candidates = render_diagnostics.identify_production_service(
            [SERVICE, UNRELATED_SERVICE], 'nandan503/Apex-Flow',
            'apex-flow-7mr9.onrender.com')
        self.assertEqual(method, 'repository_url_match')
        self.assertEqual([c['id'] for c in candidates], ['srv-apexflow'])

    def test_identify_none(self):
        method, candidates = render_diagnostics.identify_production_service(
            [UNRELATED_SERVICE], 'nandan503/Apex-Flow',
            'apex-flow-7mr9.onrender.com')
        self.assertEqual(method, 'none')
        self.assertEqual(candidates, [])

    def test_identify_ambiguous(self):
        twin = dict(SERVICE, id='srv-twin')
        method, candidates = render_diagnostics.identify_production_service(
            [SERVICE, twin], 'nandan503/Apex-Flow',
            'apex-flow-7mr9.onrender.com')
        self.assertEqual(method, 'ambiguous')
        self.assertEqual(len(candidates), 2)

    def test_identify_disambiguates_by_hostname(self):
        staging = dict(SERVICE, id='srv-staging',
                       serviceDetails={'url': 'https://apex-staging.onrender.com'})
        method, candidates = render_diagnostics.identify_production_service(
            [SERVICE, staging], 'nandan503/Apex-Flow',
            'apex-flow-7mr9.onrender.com')
        self.assertEqual(method, 'repository_url_match_plus_documented_hostname')
        self.assertEqual([c['id'] for c in candidates], ['srv-apexflow'])


class RenderDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(
            ('127.0.0.1', 0), MockServer)
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name) / 'diagnostics'
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.tmp.cleanup)

    def _run(self, env=None):
        base_env = {
            'RENDER_API_TOKEN': TOKEN,
            'GITHUB_SHA': COMMIT_SHA,
            'GITHUB_REF_NAME': 'main',
            'GITHUB_ACTIONS': 'true',
            'GITHUB_STEP_SUMMARY': '',
        }
        base_env.update(env or {})
        with mock.patch.dict(os.environ, base_env, clear=True):
            argv = ['--output', str(self.output),
                    '--api-base', f'{self.base}/v1',
                    '--canonical-base', self.base,
                    '--timeout', '5']
            code = render_diagnostics.main(argv)
        return code

    def _artifact_text(self, name):
        return (self.output / name).read_text(encoding='utf-8')

    def _all_artifact_text(self):
        return '\n'.join(path.read_text(encoding='utf-8')
                         for path in sorted(self.output.iterdir())
                         if path.is_file())

    def test_healthy_run_exits_zero_and_writes_sanitized_artifacts(self):
        code = self._run()
        self.assertEqual(code, 0)
        expected_files = {'summary.json', 'render-services.json',
                          'render-deploys.json', 'render-logs.txt',
                          'canonical-health.json', 'README.md'}
        self.assertEqual({p.name for p in self.output.iterdir()},
                         expected_files)

        summary = json.loads(self._artifact_text('summary.json'))
        self.assertTrue(summary['render_api']['authenticated'])
        self.assertEqual(summary['services']['identification']['method'],
                         'repository_url_match')
        self.assertEqual(summary['deployment']['state'], 'live')
        self.assertEqual(summary['commit_correlation']['verdict'], 'match')
        self.assertEqual(summary['commit_correlation']
                         ['render_deployed_commit'], COMMIT_SHA)
        self.assertEqual(summary['health']['liveness'], 'healthy')
        self.assertEqual(summary['health']['readiness'], 'healthy')
        self.assertEqual(summary['overall']['status'], 'healthy')
        self.assertEqual(summary['exit_code'], 0)
        self.assertFalse(summary['commit_correlation']
                         ['application_version_endpoint_exists'])
        self.assertTrue(any('no /version endpoint' in item
                            for item in summary['unknowns']))

        all_text = self._all_artifact_text()
        # The API token and the account email must never appear.
        self.assertNotIn(TOKEN, all_text)
        self.assertNotIn(ACCOUNT_EMAIL, all_text)
        # Log credentials must be redacted, structure preserved.
        self.assertNotIn('hunter2secret', all_text)  # gitleaks:allow
        self.assertIn('postgres://[REDACTED]@dpg-abc.render.com/apex', all_text)
        self.assertIn('request_complete', all_text)
        self.assertIn('endpoint=api.login', all_text)
        # Commit correlation data must survive sanitization.
        self.assertIn(COMMIT_SHA, all_text)
        # Dropped posture fields must not be present.
        services = json.loads(self._artifact_text('render-services.json'))
        details = services['services'][0]['serviceDetails']
        self.assertNotIn('sshAddress', details)
        self.assertNotIn('ipAllowList', details)
        self.assertNotIn(SSH_VALUE, all_text)

    def test_readiness_failure_exits_three(self):
        MockServer.ready_status = 503
        MockServer.ready_body = {'error': 'Database unavailable; retry'}
        try:
            code = self._run()
        finally:
            MockServer.ready_status = 200
            MockServer.ready_body = {'status': 'ready'}
        self.assertEqual(code, 3)
        summary = json.loads(self._artifact_text('summary.json'))
        self.assertEqual(summary['health']['readiness'], 'unhealthy')
        self.assertEqual(summary['health']['liveness'], 'healthy')
        self.assertEqual(summary['overall']['status'], 'unhealthy')
        self.assertEqual(summary['failure_kind'], 'health_or_deployment')
        # Deployment correlation is still collected for diagnosis.
        self.assertEqual(summary['deployment']['state'], 'live')
        health = json.loads(self._artifact_text('canonical-health.json'))
        ready = [e for e in health['endpoints'] if e['kind'] == 'readiness'][0]
        self.assertEqual(ready['status_code'], 503)
        self.assertFalse(ready['healthy'])

    def test_deploy_failed_exits_three(self):
        MockServer.deploys_payload = [dict(DEPLOY, status='build_failed')]
        try:
            code = self._run()
        finally:
            MockServer.deploys_payload = None
        self.assertEqual(code, 3)
        summary = json.loads(self._artifact_text('summary.json'))
        self.assertEqual(summary['deployment']['state'], 'failed')
        self.assertEqual(summary['overall']['deployment'], 'failed')
        self.assertEqual(summary['failure_kind'], 'health_or_deployment')

    def test_suspended_service_exits_three(self):
        suspended = dict(SERVICE, suspended='suspended',
                         suspenders=[{'suspenderType': 'user'}])
        MockServer.services_payload = [suspended]
        try:
            code = self._run()
        finally:
            MockServer.services_payload = None
        self.assertEqual(code, 3)
        summary = json.loads(self._artifact_text('summary.json'))
        self.assertEqual(summary['deployment']['state'], 'suspended')
        self.assertEqual(summary['deployment']['suspenders'], ['user'])

    def test_auth_failure_exits_two_and_writes_partial_artifacts(self):
        MockServer.auth_failure = True
        try:
            code = self._run()
        finally:
            MockServer.auth_failure = False
        self.assertEqual(code, 2)
        summary = json.loads(self._artifact_text('summary.json'))
        self.assertEqual(summary['failure_kind'], 'render_api')
        self.assertFalse(summary['render_api']['authenticated'])
        self.assertEqual(summary['collection']['status'], 'failed')
        self.assertTrue(summary['collection']['errors'])
        # Health probes still ran and are reported.
        self.assertEqual(summary['health']['liveness'], 'healthy')

    def test_token_echoed_by_api_is_stripped_from_artifacts(self):
        # If an API error body ever echoes the credential, the literal token
        # guard must keep it out of every artifact.
        class EchoServer(MockServer):
            def do_GET(self):
                route = self.path.split('?')[0].rstrip('/')
                if route.startswith('/v1/'):
                    self._json(401, {'message': f'invalid token {TOKEN}'})
                else:
                    MockServer.do_GET(self)

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), EchoServer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        base = f'http://127.0.0.1:{server.server_address[1]}'
        env = {'RENDER_API_TOKEN': TOKEN, 'GITHUB_SHA': COMMIT_SHA,
               'GITHUB_STEP_SUMMARY': ''}
        with mock.patch.dict(os.environ, env, clear=True):
            code = render_diagnostics.main(
                ['--output', str(self.output), '--api-base', f'{base}/v1',
                 '--canonical-base', base, '--timeout', '5'])
        self.assertEqual(code, 2)
        all_text = self._all_artifact_text()
        self.assertNotIn(TOKEN, all_text)
        self.assertIn('[REDACTED]', all_text)

    def test_no_matching_service_exits_four(self):
        MockServer.services_payload = [UNRELATED_SERVICE]
        try:
            code = self._run()
        finally:
            MockServer.services_payload = None
        self.assertEqual(code, 4)
        summary = json.loads(self._artifact_text('summary.json'))
        self.assertEqual(summary['failure_kind'], 'no_matching_service')
        self.assertEqual(summary['overall']['status'], 'unknown')
        services = json.loads(self._artifact_text('render-services.json'))
        self.assertEqual(services['total_services'], 1)
        self.assertFalse(services['services'][0]['matches_repository'])

    def test_ambiguous_services_exit_five(self):
        twin = dict(SERVICE, id='srv-apexflow2', name='apex-flow-twin')
        MockServer.services_payload = [SERVICE, twin]
        try:
            code = self._run()
        finally:
            MockServer.services_payload = None
        self.assertEqual(code, 5)
        summary = json.loads(self._artifact_text('summary.json'))
        self.assertEqual(summary['failure_kind'], 'ambiguous_service')
        self.assertTrue(summary['services']['identification']['ambiguous'])
        self.assertEqual(
            summary['services']['identification']['candidate_service_ids'],
            ['srv-apexflow', 'srv-apexflow2'])

    def test_commit_mismatch_is_reported(self):
        code = self._run(env={'GITHUB_SHA': OTHER_SHA})
        self.assertEqual(code, 0)  # mismatch alone is not a health failure
        summary = json.loads(self._artifact_text('summary.json'))
        self.assertEqual(summary['commit_correlation']['verdict'], 'mismatch')
        self.assertEqual(summary['commit_correlation']['github_commit'],
                         OTHER_SHA)

    def test_hostname_fallback_identification(self):
        service_no_repo = dict(SERVICE, repo=None)
        MockServer.services_payload = [service_no_repo, UNRELATED_SERVICE]
        try:
            code = self._run()
        finally:
            MockServer.services_payload = None
        self.assertEqual(code, 0)
        summary = json.loads(self._artifact_text('summary.json'))
        self.assertEqual(summary['services']['identification']['method'],
                         'documented_hostname_match')

    def test_missing_token_exits_one(self):
        with mock.patch.dict(os.environ, {'RENDER_API_TOKEN': ''}, clear=True):
            code = render_diagnostics.main(
                ['--output', str(self.output), '--api-base', self.base,
                 '--canonical-base', self.base])
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
