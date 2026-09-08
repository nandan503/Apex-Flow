#!/usr/bin/env python3
"""Render observability collector for APEX Flow.

Collects sanitized Render infrastructure diagnostics and canonical
application health for https://apex.viability.in, then writes a diagnostics
bundle (summary.json, render-services.json, render-deploys.json,
render-logs.txt, canonical-health.json, README.md). See
docs/RENDER_DIAGNOSTICS.md for the full contract.

Security contract enforced by this script:

* RENDER_API_TOKEN is read from the process environment and is never
  printed, logged or written to any artifact. All output passes through
  scripts/render_sanitize.py plus a literal token guard.
* Only GET requests are issued; the collector cannot mutate Render state.
* Render endpoints that return secret values (env-vars, secret-files,
  connection-info, credentials) are never called.
* The authenticated user's email from GET /v1/users is used only as an auth
  proof and is never written to artifacts.

Exit codes (also recorded in summary.json):

* 0 -- collection succeeded; deployment is live and all canonical health
       endpoints passed.
* 1 -- configuration/usage error (for example RENDER_API_TOKEN missing).
* 2 -- Render API authentication or connectivity failure (diagnostic
       collection failed; partial artifacts are still written).
* 3 -- collection succeeded but application health or deployment state
       failed (health endpoint failure, failed deploy, suspended service).
* 4 -- no Render service matches this repository.
* 5 -- production service identification is ambiguous.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:  # imported as a package (pytest) ...
    from scripts.render_sanitize import sanitize_json, sanitize_text, sanitize_token
except ImportError:  # ... or executed directly: python scripts/render_diagnostics.py
    from render_sanitize import sanitize_json, sanitize_text, sanitize_token

# Repository facts (see docs/OPERATIONS.md, "Canonical production login
# topology"): canonical origin, its Render origin hostname, and the git
# remote. Override with CLI flags if the deployment changes.
DEFAULT_REPO = 'nandan503/Apex-Flow'
DEFAULT_CANONICAL_BASE = 'https://apex.viability.in'
DEFAULT_RENDER_HOSTNAME = 'apex-flow-7mr9.onrender.com'
DEFAULT_API_BASE = 'https://api.render.com/v1'

# Fields dropped from service objects before writing artifacts: they reveal
# network posture that diagnostics do not need. (Env var values, secret files
# and connection info are never requested at all.)
DROPPED_SERVICE_FIELDS = {'ipAllowList', 'sshAddress'}

FAILED_DEPLOY_STATUSES = frozenset(
    {'build_failed', 'update_failed', 'pre_deploy_failed'})
NON_LIVE_DEPLOY_STATUSES = frozenset({'canceled', 'deactivated'})
IN_PROGRESS_DEPLOY_STATUSES = frozenset(
    {'created', 'queued', 'build_in_progress', 'update_in_progress',
     'pre_deploy_in_progress'})

# Log labels worth keeping in render-logs.txt.
KEEP_LOG_LABELS = frozenset(
    {'resource', 'instance', 'host', 'level', 'type', 'statusCode', 'method',
     'path', 'task', 'taskRun'})

MAX_LOG_LINES = 100
MAX_LOG_MESSAGE_CHARS = 1000
MAX_BODY_SNIPPET_CHARS = 400
HTTP_TIMEOUT_SECONDS = 20.0
HTTP_ATTEMPTS = 3
MAX_SERVICES_PAGES = 5


class RenderApiError(Exception):
    """A Render API call failed after retries (message is sanitized on use)."""


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def utc_now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _log_tag(labels):
    parts = []
    for label in labels or []:
        if not isinstance(label, dict):
            continue
        name, value = label.get('name'), label.get('value')
        if name in KEEP_LOG_LABELS and value is not None:
            parts.append(f'{name}={value}')
    return ' '.join(sorted(parts))


def normalize_repo_url(repo):
    """Normalize a git remote to ``host/owner/name`` for comparison."""
    if not repo or not isinstance(repo, str):
        return ''
    value = repo.strip().lower()
    if value.startswith('git@'):
        value = value[4:].replace(':', '/', 1)
    for scheme in ('https://', 'http://'):
        if value.startswith(scheme):
            value = value[len(scheme):]
            break
    if value.startswith('www.'):
        value = value[4:]
    value = value.rstrip('/')
    if value.endswith('.git'):
        value = value[:-4]
    return value


def repository_match_target(repo):
    """Build the normalized match target from a --repo argument.

    Accepts ``owner/name`` (GitHub shorthand, the repository default), a full
    git URL (``https://github.com/owner/name[.git]``) or an SCP-style remote
    (``git@github.com:owner/name.git``).
    """
    value = (repo or '').strip()
    if not value:
        return ''
    if '://' in value or value.startswith('git@'):
        return normalize_repo_url(value)
    if value.count('/') == 1:  # owner/name shorthand -> GitHub
        return normalize_repo_url(f'https://github.com/{value}')
    return normalize_repo_url(f'https://{value}')


def service_hostname(service):
    """Return the hostname of a service's Render URL, if any."""
    details = service.get('serviceDetails') or {}
    url = details.get('url')
    if not url:
        return ''
    try:
        return (urllib.parse.urlsplit(url).hostname or '').lower()
    except ValueError:
        return ''


def strip_dropped_fields(service):
    """Copy a service object, removing fields we deliberately do not keep."""
    cleaned = json.loads(json.dumps(service))
    details = cleaned.get('serviceDetails')
    if isinstance(details, dict):
        for field in DROPPED_SERVICE_FIELDS:
            details.pop(field, None)
    return cleaned


def http_get(url, params=None, headers=None, timeout=HTTP_TIMEOUT_SECONDS,
             attempts=HTTP_ATTEMPTS):
    """GET a URL, returning (status, parsed_json_or_None, body_snippet).

    Retries transient failures (429/5xx/network). Never logs request
    headers. The response body is capped at 1 MiB and the snippet at 2 KiB.
    """
    full_url = url
    if params:
        full_url = f'{url}?{urllib.parse.urlencode(params)}'
    last_error = None
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(min(2.0 * attempt, 5.0))
        try:
            request = urllib.request.Request(
                full_url, method='GET',
                headers={'Accept': 'application/json', **(headers or {})})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(1 << 20)
                return (response.status,
                        _parse_json(raw),
                        response.geturl(),
                        raw[:2048].decode('utf-8', 'replace'))
        except urllib.error.HTTPError as exc:
            body = exc.read(4096).decode('utf-8', 'replace')
            if exc.code == 429 or exc.code >= 500:
                last_error = f'HTTP {exc.code} from {_safe_url(full_url)}'
                continue
            # 4xx (other than 429) will not succeed on retry.
            raise RenderApiError(
                f'HTTP {exc.code} from {_safe_url(full_url)}: '
                f'{sanitize_text(body)[:200]}') from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            last_error = f'{type(exc).__name__} for {_safe_url(full_url)}'
    raise RenderApiError(last_error or 'request failed')


def _parse_json(raw):
    try:
        return json.loads(raw.decode('utf-8', 'replace'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _safe_url(url):
    """Strip any query string before showing a URL in output."""
    return url.split('?', 1)[0]


# ---------------------------------------------------------------------------
# Read-only Render API client
# ---------------------------------------------------------------------------


class RenderClient:
    """Read-only Render API v1 client (GET only, by design)."""

    def __init__(self, base_url=DEFAULT_API_BASE, token='', timeout=20.0,
                 attempts=HTTP_ATTEMPTS):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.attempts = attempts
        self._headers = {'Authorization': f'Bearer {token}'}

    def _get(self, path, params=None):
        status, data, _url, snippet = http_get(
            self.base_url + path, params=params, headers=self._headers,
            timeout=self.timeout, attempts=self.attempts)
        return status, data, snippet

    def whoami(self):
        """GET /v1/users -- proves the credential works. Email is discarded."""
        return self._get('/users')

    def list_services(self, limit=100):
        """GET /v1/services -- cursor-paginated, previews excluded."""
        services, cursor, pages = [], None, 0
        while pages < MAX_SERVICES_PAGES:
            params = {'limit': limit, 'includePreviews': 'false'}
            if cursor:
                params['cursor'] = cursor
            status, data, snippet = self._get('/services', params)
            page = data if isinstance(data, list) else []
            if status != 200:
                raise RenderApiError(f'HTTP {status} listing services')
            for item in page:
                if isinstance(item, dict) and isinstance(item.get('service'), dict):
                    services.append(item['service'])
                cursor = (item or {}).get('cursor') or cursor
            pages += 1
            if len(page) < limit:
                break
        return services

    def get_service(self, service_id):
        status, data, _snippet = self._get(
            f'/services/{urllib.parse.quote(service_id, safe="")}')
        if status != 200 or not isinstance(data, dict):
            raise RenderApiError(f'HTTP {status} retrieving service {service_id}')
        return data

    def list_deploys(self, service_id, limit=20):
        status, data, _snippet = self._get(
            f'/services/{urllib.parse.quote(service_id, safe="")}/deploys',
            {'limit': limit})
        if status != 200 or not isinstance(data, list):
            raise RenderApiError(f'HTTP {status} listing deploys for {service_id}')
        deploys = [item.get('deploy') for item in data
                   if isinstance(item, dict) and isinstance(item.get('deploy'), dict)]
        deploys.sort(key=lambda d: d.get('createdAt') or '', reverse=True)
        return deploys

    def list_instances(self, service_id):
        status, data, _snippet = self._get(
            f'/services/{urllib.parse.quote(service_id, safe="")}/instances')
        if status != 200 or not isinstance(data, list):
            raise RenderApiError(f'HTTP {status} listing instances for {service_id}')
        return [item for item in data if isinstance(item, dict)]

    def get_logs(self, owner_id, resource_id, start_epoch, end_epoch,
                 limit=MAX_LOG_LINES):
        """GET /v1/logs -- requires ownerId and resource; RFC3339 time range.

        The Render API parses startTime/endTime as RFC3339 timestamps, not
        epoch seconds (epochs produce HTTP 400 schema errors).
        """
        def rfc3339(epoch):
            return datetime.fromtimestamp(epoch, timezone.utc).strftime(
                '%Y-%m-%dT%H:%M:%SZ')
        params = {
            'ownerId': owner_id,
            'resource': resource_id,
            'startTime': rfc3339(start_epoch),
            'endTime': rfc3339(end_epoch),
            'direction': 'backward',
            'limit': limit,
        }
        status, data, _snippet = self._get('/logs', params)
        if status != 200 or not isinstance(data, dict):
            raise RenderApiError(f'HTTP {status} retrieving logs for {resource_id}')
        return data


# ---------------------------------------------------------------------------
# Production service identification
# ---------------------------------------------------------------------------


def identify_production_service(services, repo_full_name, render_hostname):
    """Find the production service for this repository.

    Primary criterion: the service's ``repo`` matches the repository remote.
    Fallback: the service whose Render URL hostname equals the hostname
    documented in docs/OPERATIONS.md. Returns (method, candidate_services).
    """
    target_repo = repository_match_target(repo_full_name)
    target_host = (render_hostname or '').strip().lower()

    repo_matches = [s for s in services
                    if normalize_repo_url(s.get('repo')) == target_repo]
    if len(repo_matches) == 1:
        return 'repository_url_match', repo_matches
    if len(repo_matches) > 1:
        host_matches = [s for s in repo_matches
                        if service_hostname(s) == target_host]
        if len(host_matches) == 1:
            return 'repository_url_match_plus_documented_hostname', host_matches
        web_main = [s for s in repo_matches
                    if s.get('type') == 'web_service'
                    and (s.get('branch') or 'main') == 'main']
        if len(web_main) == 1:
            return 'repository_url_match_plus_web_service_main_branch', web_main
        return 'ambiguous', repo_matches

    host_matches = [s for s in services
                    if service_hostname(s) == target_host]
    if len(host_matches) == 1:
        return 'documented_hostname_match', host_matches
    if len(host_matches) > 1:
        return 'ambiguous', host_matches
    return 'none', []


# ---------------------------------------------------------------------------
# Canonical health checks
# ---------------------------------------------------------------------------


def check_health_endpoint(url, timeout=HTTP_TIMEOUT_SECONDS):
    """GET a canonical URL and record status/latency/body (sanitized later)."""
    record = {
        'url': url,
        'ok': False,
        'status_code': None,
        'latency_ms': None,
        'content_type': None,
        'final_url': None,
        'body_snippet': None,
        'error': None,
    }
    started = time.monotonic()
    try:
        request = urllib.request.Request(url, method='GET',
                                         headers={'Accept': 'application/json, text/html'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(8192)
            record['status_code'] = response.status
            record['content_type'] = response.headers.get('Content-Type')
            record['final_url'] = response.geturl()
            record['body_snippet'] = raw[:MAX_BODY_SNIPPET_CHARS].decode(
                'utf-8', 'replace')
            record['ok'] = True
    except urllib.error.HTTPError as exc:
        # A response was received: the domain is reachable, the app answered.
        raw = exc.read(8192)
        record['status_code'] = exc.code
        record['content_type'] = exc.headers.get('Content-Type')
        record['final_url'] = exc.geturl()
        record['body_snippet'] = raw[:MAX_BODY_SNIPPET_CHARS].decode(
            'utf-8', 'replace')
        record['ok'] = True
        record['error'] = f'HTTP {exc.code}'
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        record['error'] = type(exc).__name__
    finally:
        record['latency_ms'] = round((time.monotonic() - started) * 1000)
    return record


def assess_health(canonical_base, timeout=HTTP_TIMEOUT_SECONDS):
    """Probe the canonical origin using THIS repository's real endpoints.

    /health/live and /health/ready exist in backend/app.py. The root path is
    the login page. A 200 is not sufficient: a provider interstitial can
    also return 200 HTML (observed in docs/OPERATIONS.md), so the body must
    contain the expected marker. /version does not exist in this repository
    and is deliberately not probed.
    """
    endpoints = [
        {'path': '/health/live', 'kind': 'liveness',
         'expect': 'HTTP 200 with a JSON body containing "live"'},
        {'path': '/health/ready', 'kind': 'readiness',
         'expect': 'HTTP 200 with a JSON body containing "ready"; '
                   'HTTP 503 means the database SELECT failed'},
        {'path': '/', 'kind': 'canonical_root',
         'expect': 'HTTP 200 HTML (login page served by Flask)'},
    ]
    results = []
    for spec in endpoints:
        record = check_health_endpoint(canonical_base + spec['path'], timeout)
        record['path'] = spec['path']
        record['kind'] = spec['kind']
        record['expectation'] = spec['expect']
        body = record.get('body_snippet') or ''
        if spec['kind'] == 'liveness':
            record['healthy'] = (record['status_code'] == 200
                                 and '"live"' in body)
        elif spec['kind'] == 'readiness':
            record['healthy'] = (record['status_code'] == 200
                                 and '"ready"' in body)
        else:
            record['healthy'] = record['status_code'] == 200
        results.append(record)

    def kind(kind_):
        for record in results:
            if record['kind'] == kind_:
                return record
        return {}

    liveness = 'healthy' if kind('liveness').get('healthy') else (
        'unhealthy' if kind('liveness').get('status_code') is not None
        else 'unknown')
    readiness = 'healthy' if kind('readiness').get('healthy') else (
        'unhealthy' if kind('readiness').get('status_code') is not None
        else 'unknown')
    root = kind('canonical_root')
    return {
        'canonical_base': canonical_base,
        'endpoints': results,
        'liveness': liveness,
        'readiness': readiness,
        # Reachable means: an HTTP response came back from the canonical
        # origin (any status). Serving means: the login page returned 200.
        'canonical_reachable': bool(root.get('status_code')),
        'canonical_serving': bool(root.get('healthy')),
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


class Emitter:
    """Sanitized stdout + GitHub Actions step-summary writer."""

    def __init__(self, token):
        self.token = token
        self._summary_path = os.environ.get('GITHUB_STEP_SUMMARY') or None

    def __call__(self, text=''):
        safe = sanitize_token(sanitize_text(text), self.token)
        print(safe, flush=True)
        if self._summary_path:
            with open(self._summary_path, 'a', encoding='utf-8') as handle:
                handle.write(safe + '\n')


def write_text_artifact(path, text, token):
    safe = sanitize_token(sanitize_text(text), token)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(safe + '\n', encoding='utf-8')


def write_json_artifact(path, payload, token):
    write_text_artifact(path, sanitize_json(payload, token), token)


def artifact_readme(context):
    lines = [
        '# APEX Flow Render diagnostics artifact',
        '',
        f'Generated: {context["generated_at"]} (UTC)',
        f'Source: {context["source"]}',
        f'Repository commit: {context["github_sha"] or "unknown"}'
        f' (ref {context["github_ref"] or "unknown"})',
        '',
        '## Files',
        '',
        '- `summary.json` -- start here: run metadata, service identification,'
        ' deployment state, commit correlation, health assessment, unknowns.',
        '- `render-services.json` -- every Render service visible to the API'
        ' token, sanitized, with repository-match flags and the production'
        ' service identification result.',
        '- `render-deploys.json` -- recent deploys and instances for the'
        ' identified production service(s).',
        '- `render-logs.txt` -- recent Render logs (bounded window and line'
        ' count) for the identified service(s), sanitized.',
        '- `canonical-health.json` -- HTTP probes of the canonical production'
        ' origin (https://apex.viability.in) against this repository\'s real'
        ' endpoints: /health/live, /health/ready and /.',
        '',
        '## Sanitization',
        '',
        'All content passed through `scripts/render_sanitize.py` (JSON key'
        ' redaction, credential-pattern redaction, literal token guard). The'
        ' Render API token never entered this artifact. Deliberately NOT'
        ' collected: Render environment variable values, secret files,'
        ' database connection info, custom header values, and authenticated'
        ' application request/response bodies. Service `ipAllowList` and'
        ' `sshAddress` fields are dropped. The authenticated user email from'
        ' the API auth check is discarded.',
        '',
        '## Interpretation',
        '',
        '- **Deployment failure**: latest deploy in a failed state'
        ' (build_failed / update_failed / pre_deploy_failed) or the service'
        ' is suspended. Infrastructure state only.',
        '- **Health failure**: /health/live or the canonical root did not'
        ' return the expected response. Application process state.',
        '- **Readiness failure**: /health/ready did not return 200 with'
        ' "ready" -- typically the database SELECT failed (HTTP 503).'
        ' Database dependency state.',
        '- **Diagnostic failure**: the collector itself could not complete'
        ' (exit codes 1/2/4/5); artifacts may be partial.',
        '',
        'A green run means *collection succeeded and everything probed was'
        ' healthy*. It does not certify outbox delivery, data integrity, or'
        ' end-to-end user journeys.',
        '',
    ]
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def collect(args, token, emit):
    generated_at = utc_now_iso()
    github_sha = os.environ.get('GITHUB_SHA') or None
    github_ref = (os.environ.get('GITHUB_REF_NAME')
                  or os.environ.get('GITHUB_REF') or None)
    context = {
        'generated_at': generated_at,
        'source': 'github_actions' if os.environ.get('GITHUB_ACTIONS') else 'local',
        'github_sha': github_sha,
        'github_ref': github_ref,
        'github_run_id': os.environ.get('GITHUB_RUN_ID'),
        'github_repository': os.environ.get('GITHUB_REPOSITORY'),
        'collector': 'scripts/render_diagnostics.py',
    }
    state = {
        'context': context,
        'collection_errors': [],
        'warnings': [],
        'authenticated': False,
        'services': [],
        'identification': {'method': 'not_attempted', 'candidates': []},
        'per_service': [],
        'health': None,
    }
    client = RenderClient(args.api_base, token, timeout=args.timeout)

    # --- 1. Verify authentication -----------------------------------------
    auth_status = None
    try:
        auth_status, _user, _snippet = client.whoami()
        # _user contains the account email/name: used as proof only, never
        # stored or written to artifacts.
        state['authenticated'] = auth_status == 200
        if state['authenticated']:
            emit(f'Render API authentication verified (HTTP {auth_status}).')
        else:
            state['collection_errors'].append(
                f'GET /v1/users returned HTTP {auth_status}')
    except RenderApiError as exc:
        state['collection_errors'].append(f'GET /v1/users failed: {exc}')

    # --- 2. Discover services ---------------------------------------------
    listing_ok = False
    try:
        state['services'] = client.list_services()
        listing_ok = True
        emit(f'Discovered {len(state["services"])} Render service(s).')
        if not state['authenticated']:
            # The users endpoint may be restricted for some key types; a
            # successful services listing still proves the credential works.
            state['authenticated'] = True
            state['warnings'].append(
                'GET /v1/users did not return 200, but service listing '
                'succeeded; treating the credential as valid.')
    except RenderApiError as exc:
        state['collection_errors'].append(f'GET /v1/services failed: {exc}')
        emit(f'ERROR: could not list Render services: {sanitize_text(str(exc))}')

    # --- 3. Identify the production service --------------------------------
    candidates = []
    method = 'not_attempted'
    if listing_ok:
        method, candidates = identify_production_service(
            state['services'], args.repo, args.render_hostname)
        state['identification'] = {
            'method': method,
            'criteria': {
                'repository': args.repo,
                'documented_render_hostname': args.render_hostname,
            },
            'candidate_service_ids': [s.get('id') for s in candidates],
        }
        emit(f'Production service identification: {method} '
             f'({len(candidates)} candidate(s)).')

    # --- 4. Per-service detail, deploys, instances, logs --------------------
    for service in candidates[:3]:
        entry = {'service_id': service.get('id'),
                 'service_name': service.get('name')}
        try:
            entry['service'] = client.get_service(service['id'])
        except (RenderApiError, KeyError) as exc:
            entry['service_error'] = sanitize_text(str(exc))
            entry['service'] = service
        try:
            entry['deploys'] = client.list_deploys(service['id'])
        except (RenderApiError, KeyError) as exc:
            entry['deploys_error'] = sanitize_text(str(exc))
            entry['deploys'] = []
        try:
            entry['instances'] = client.list_instances(service['id'])
        except (RenderApiError, KeyError) as exc:
            entry['instances_error'] = sanitize_text(str(exc))
            entry['instances'] = []
        owner_id = (entry.get('service') or {}).get('ownerId')
        now = time.time()
        entry['logs'] = {'window_minutes': args.lookback_minutes, 'logs': []}
        if owner_id:
            try:
                logs_payload = client.get_logs(
                    owner_id, service['id'],
                    now - args.lookback_minutes * 60, now)
                entry['logs'] = {
                    'window_minutes': args.lookback_minutes,
                    'has_more': bool(logs_payload.get('hasMore')),
                    'logs': logs_payload.get('logs') or [],
                }
            except RenderApiError as exc:
                entry['logs_error'] = sanitize_text(str(exc))
        else:
            entry['logs_error'] = 'service has no ownerId; logs unavailable'
        state['per_service'].append(entry)

    # --- 5. Canonical health -----------------------------------------------
    state['health'] = assess_health(args.canonical_base, timeout=args.timeout)

    return state


# ---------------------------------------------------------------------------
# Assessment and artifacts
# ---------------------------------------------------------------------------


def newest_deploy(deploys):
    """Newest deploy (list already sorted newest-first by createdAt)."""
    return deploys[0] if deploys else None


def deployment_assessment(service, deploys):
    latest = newest_deploy(deploys)
    suspended = service.get('suspended') == 'suspended'
    suspenders = [s.get('suspenderType') for s in service.get('suspenders') or []
                  if isinstance(s, dict)]
    state = 'unknown'
    if suspended:
        state = 'suspended'
    elif latest is None:
        state = 'no_deploys'
    else:
        status = latest.get('status')
        if status in FAILED_DEPLOY_STATUSES:
            state = 'failed'
        elif status == 'live':
            state = 'live'
        elif status in IN_PROGRESS_DEPLOY_STATUSES:
            state = 'in_progress'
        elif status in NON_LIVE_DEPLOY_STATUSES:
            state = 'not_live'
    return {
        'state': state,
        'suspended': suspended,
        'suspenders': suspenders,
        'latest_deploy': latest,
    }


def commit_correlation(github_sha, deploys):
    latest = newest_deploy(deploys)
    render_commit = ((latest or {}).get('commit') or {}).get('id')
    if not github_sha or not render_commit:
        verdict = 'unknown'
    elif render_commit.lower().startswith(github_sha.lower()) or \
            github_sha.lower().startswith(render_commit.lower()):
        verdict = 'match'
    else:
        verdict = 'mismatch'
    return {
        'github_commit': github_sha,
        'render_deployed_commit': render_commit,
        # This repository deliberately exposes no /version endpoint, so the
        # application cannot report its own version. Adding one is a separate
        # application workstream (see docs/RENDER_DIAGNOSTICS.md).
        'application_reported_version': None,
        'application_version_endpoint_exists': False,
        'verdict': verdict,
    }


def error_like_log_lines(entry):
    counted = 0
    for log in entry.get('logs', {}).get('logs') or []:
        for label in log.get('labels') or []:
            if isinstance(label, dict) and label.get('name') == 'level' \
                    and str(label.get('value', '')).lower() in (
                        'error', 'critical', 'fatal', 'err'):
                counted += 1
                break
    return counted


def build_summary(state, args, exit_code, failure_kind):
    context = state['context']
    health = state['health'] or {}
    deployment = None
    correlation = None
    log_stats = {'services_with_logs': 0, 'error_like_lines': 0,
                 'has_more': False}
    for entry in state['per_service']:
        deployment = deployment_assessment(entry.get('service') or {},
                                            entry.get('deploys') or [])
        correlation = commit_correlation(context['github_sha'],
                                         entry.get('deploys') or [])
        logs = entry.get('logs') or {}
        if logs.get('logs'):
            log_stats['services_with_logs'] += 1
            log_stats['error_like_lines'] += error_like_log_lines(entry)
            log_stats['has_more'] = log_stats['has_more'] or bool(
                logs.get('has_more'))
        break  # summary reflects the first (primary) identified service

    health_ok = (health.get('liveness') == 'healthy'
                 and health.get('readiness') == 'healthy'
                 and health.get('canonical_reachable'))
    deployment_state = deployment['state'] if deployment else 'not_evaluated'

    unknowns = [
        'Application-reported version is unavailable: this repository exposes '
        'no /version endpoint by design. Commit correlation relies on Render '
        'deploy metadata only.',
        'Health probes traverse the full canonical path (Cloudflare to the '
        'Render origin); they do not test direct origin reachability.',
        'Readiness is a single database SELECT; it does not certify outbox '
        'delivery, migrations, or end-to-end user journeys.',
    ]
    if log_stats['has_more']:
        unknowns.append(
            'More Render logs exist than were collected (collection is '
            'bounded to the lookback window and 100 lines per service).')
    if not state['per_service']:
        unknowns.append(
            'No per-service deployment detail could be collected (see '
            'identification method and collection errors).')

    reasons = []
    method = state['identification'].get('method')
    if state['collection_errors']:
        reasons.append('Render API collection errors occurred.')
    if method == 'none':
        reasons.append('No Render service matches this repository.')
    elif method == 'ambiguous':
        reasons.append('Production service identification is ambiguous.')
    if health.get('liveness') != 'healthy':
        reasons.append('Liveness probe did not return the expected response.')
    if health.get('readiness') != 'healthy':
        reasons.append('Readiness probe did not return the expected response.')
    if not health.get('canonical_reachable'):
        reasons.append('Canonical domain did not answer.')
    if deployment_state in ('failed', 'suspended'):
        reasons.append(f'Deployment state is {deployment_state}.')

    if not state['services'] and state['collection_errors']:
        # Core discovery failed: health may still be probed, but we cannot
        # attribute deployment state.
        overall = 'unknown'
    elif method in ('none', 'ambiguous', 'not_attempted'):
        # Health is reported separately, but without a unique production
        # service we cannot attribute deployment state.
        overall = 'unknown'
    elif not health_ok or deployment_state in ('failed', 'suspended'):
        overall = 'unhealthy'
    else:
        # health_ok is true; deployment_state is live, in_progress,
        # not_live, no_deploys or not_evaluated and is reported separately.
        overall = 'healthy'

    identification = dict(state['identification'])
    identification['ambiguous'] = state['identification'].get('method') == 'ambiguous'

    return {
        'schema': 'apex-flow/render-diagnostics/1',
        'generated_at': context['generated_at'],
        'run': context,
        'collection': {
            'status': ('failed' if not state['services'] and
                       state['collection_errors'] else
                       'partial' if state['collection_errors'] else 'succeeded'),
            'errors': state['collection_errors'],
            'warnings': state['warnings'],
        },
        'render_api': {
            'base_url': args.api_base,
            'authenticated': state['authenticated'],
            'note': 'Read-only GET requests only.',
        },
        'services': {
            'total': len(state['services']),
            'identification': identification,
        },
        'deployment': deployment,
        'commit_correlation': correlation,
        'health': health,
        'logs': log_stats,
        'overall': {
            'status': overall,
            'health': ('healthy' if health_ok else
                       'unknown' if not state['health'] else 'unhealthy'),
            'deployment': deployment_state,
            'reasons': reasons,
        },
        'unknowns': unknowns,
        'exit_code': exit_code,
        'failure_kind': failure_kind,
    }


def write_artifacts(state, args, token, summary):
    out = Path(args.output)
    context = state['context']

    # summary.json
    write_json_artifact(out / 'summary.json', summary, token)

    # render-services.json
    services_payload = {
        'generated_at': context['generated_at'],
        'api_base': args.api_base,
        'total_services': len(state['services']),
        'services': [dict(strip_dropped_fields(s),
                          matches_repository=(normalize_repo_url(s.get('repo'))
                                              == repository_match_target(
                                                  args.repo)))
                     for s in state['services']],
        'production_service_identification': state['identification'],
        'collection_errors': state['collection_errors'],
        'notes': [
            'Sanitized via scripts/render_sanitize.py. ipAllowList and '
            'sshAddress are dropped; environment variable values, secret '
            'files and connection info are never requested.',
        ],
    }
    write_json_artifact(out / 'render-services.json', services_payload, token)

    # render-deploys.json
    deploys_payload = {
        'generated_at': context['generated_at'],
        'services': [],
        'notes': [
            'Deploys are sorted newest-first. "live" means Render finished '
            'the deploy successfully; it is not proof of application health.',
        ],
    }
    for entry in state['per_service']:
        service = strip_dropped_fields(entry.get('service') or {})
        deploys_payload['services'].append({
            'service': service,
            'deploys': entry.get('deploys') or [],
            'instances': entry.get('instances') or [],
            'errors': {k: v for k, v in entry.items() if k.endswith('_error')},
        })
    write_json_artifact(out / 'render-deploys.json', deploys_payload, token)

    # render-logs.txt
    lines = [
        f'# Render logs collected {context["generated_at"]} (UTC)',
        f'# Window: last {args.lookback_minutes} minutes, direction=backward,'
        f' max {MAX_LOG_LINES} lines per service.',
        '# Sanitized via scripts/render_sanitize.py; messages truncated to'
        f' {MAX_LOG_MESSAGE_CHARS} characters.',
        '',
    ]
    for entry in state['per_service']:
        lines.append(f'## service {entry.get("service_name")} '
                     f'({entry.get("service_id")})')
        logs = (entry.get('logs') or {}).get('logs') or []
        if entry.get('logs_error'):
            lines.append(f'# logs unavailable: {entry["logs_error"]}')
        if not logs:
            lines.append('# (no log lines returned in the window)')
        for log in logs[:MAX_LOG_LINES]:
            message = (log.get('message') or '').strip()
            if len(message) > MAX_LOG_MESSAGE_CHARS:
                message = message[:MAX_LOG_MESSAGE_CHARS] + '...[truncated]'
            tag = _log_tag(log.get('labels'))
            prefix = f'{log.get("timestamp", "")} [{tag}]' if tag \
                else log.get('timestamp', '')
            lines.append(f'{prefix} {message}'.rstrip())
        if (entry.get('logs') or {}).get('has_more'):
            lines.append('# (more logs exist in the window than were fetched)')
        lines.append('')
    write_text_artifact(out / 'render-logs.txt', '\n'.join(lines), token)

    # canonical-health.json
    write_json_artifact(out / 'canonical-health.json', state['health'], token)

    # README.md
    write_text_artifact(out / 'README.md', artifact_readme(context), token)


def print_human_summary(summary, emit):
    emit()
    emit('## Render diagnostics result')
    emit()
    emit(f'* Collection: **{summary["collection"]["status"]}**'
         + (f' -- {len(summary["collection"]["errors"])} error(s)'
            if summary['collection']['errors'] else ''))
    identification = summary['services']['identification']
    emit(f'* Service identification: **{identification["method"]}**')
    deployment = summary.get('deployment') or {}
    if deployment:
        emit(f'* Deployment: **{deployment["state"]}**'
             + (' (suspended)' if deployment.get('suspended') else ''))
    correlation = summary.get('commit_correlation') or {}
    if correlation:
        emit(f'* Commit correlation: **{correlation["verdict"]}** '
             f'(github `{(correlation["github_commit"] or "unknown")[:12]}` vs '
             f'render `{(correlation["render_deployed_commit"] or "unknown")[:12]}`)')
    health = summary.get('health') or {}
    emit(f'* Liveness: **{health.get("liveness")}** | '
         f'Readiness: **{health.get("readiness")}** | '
         f'Canonical reachable: **{health.get("canonical_reachable")}**')
    emit()
    emit('| Endpoint | Status | Latency | Healthy |')
    emit('|---|---|---|---|')
    for record in health.get('endpoints') or []:
        emit(f'| `{record["path"]}` | {record.get("status_code")} | '
             f'{record.get("latency_ms")} ms | {record.get("healthy")} |')
    emit()
    if summary['overall']['reasons']:
        emit('Reasons:')
        for reason in summary['overall']['reasons']:
            emit(f'* {reason}')
        emit()
    emit(f'Overall: **{summary["overall"]["status"]}** '
         f'(exit {summary["exit_code"]}, kind: {summary["failure_kind"]}). '
         'Green means "collection succeeded and everything probed was '
         'healthy" -- it is not a general production certificate.')
    emit()


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Collect sanitized Render diagnostics for APEX Flow.')
    parser.add_argument('--output', default='diagnostics',
                        help='diagnostics output directory (default: diagnostics)')
    try:
        default_lookback = int(os.environ.get(
            'RENDER_LOG_LOOKBACK_MINUTES', '90'))
    except ValueError:
        default_lookback = 90
    parser.add_argument('--lookback-minutes', type=int,
                        default=default_lookback,
                        help='Render log lookback window in minutes '
                             '(default: 90, env RENDER_LOG_LOOKBACK_MINUTES)')
    parser.add_argument('--repo', default=DEFAULT_REPO,
                        help='repository used to match Render services '
                             f'(default: {DEFAULT_REPO})')
    parser.add_argument('--canonical-base', default=DEFAULT_CANONICAL_BASE,
                        help='canonical production origin '
                             f'(default: {DEFAULT_CANONICAL_BASE})')
    parser.add_argument('--render-hostname', default=DEFAULT_RENDER_HOSTNAME,
                        help='Render origin hostname documented in '
                             'docs/OPERATIONS.md '
                             f'(default: {DEFAULT_RENDER_HOSTNAME})')
    parser.add_argument('--api-base', default=DEFAULT_API_BASE,
                        help='Render API base URL (default: '
                             f'{DEFAULT_API_BASE})')
    parser.add_argument('--timeout', type=float, default=HTTP_TIMEOUT_SECONDS,
                        help='per-request timeout in seconds (default: 20)')
    args = parser.parse_args(argv)
    if not 5 <= args.lookback_minutes <= 1440:
        parser.error('--lookback-minutes must be between 5 and 1440')
    if not args.canonical_base.startswith(('http://', 'https://')):
        parser.error('--canonical-base must be an http(s) URL')
    if not args.api_base.startswith(('http://', 'https://')):
        parser.error('--api-base must be an http(s) URL')
    return args


def decide_exit(state):
    """Map collected state to (exit_code, failure_kind)."""
    if not state['services'] and state['collection_errors']:
        return 2, 'render_api'
    method = state['identification'].get('method')
    if method == 'none':
        return 4, 'no_matching_service'
    if method == 'ambiguous':
        return 5, 'ambiguous_service'
    health = state['health'] or {}
    health_ok = (health.get('liveness') == 'healthy'
                 and health.get('readiness') == 'healthy'
                 and health.get('canonical_reachable'))
    deployment = None
    for entry in state['per_service']:
        deployment = deployment_assessment(entry.get('service') or {},
                                            entry.get('deploys') or [])
        break
    deployment_bad = deployment is not None and deployment['state'] in (
        'failed', 'suspended')
    if not health_ok or deployment_bad:
        return 3, 'health_or_deployment'
    return 0, 'none'


def main(argv=None):
    args = parse_args(argv)
    token = (os.environ.get('RENDER_API_TOKEN') or '').strip()
    emit = Emitter(token)
    if not token:
        emit('ERROR: RENDER_API_TOKEN is not set. In GitHub Actions it must '
             'come from the Production environment secret '
             'secrets.RENDER_API_TOKEN.')
        return 1
    state = collect(args, token, emit)
    exit_code, failure_kind = decide_exit(state)
    summary = build_summary(state, args, exit_code, failure_kind)
    write_artifacts(state, args, token, summary)
    print_human_summary(summary, emit)
    emit(f'Artifacts written to {args.output}/ '
         '(sanitized; raw API responses were never written to disk).')
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
