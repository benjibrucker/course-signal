"""Public read-only Vercel adapter; never serves local files or raw upstream data."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import json

import competitive_catalog as catalog
import server
from build_static import sanitize_public

ALLOWED_ORIGIN = 'https://benjibrucker.github.io'
RESULT_HEADER_FIELDS = {
    'event_id': 'X-Course-Signal-Event',
    'q': 'X-Course-Signal-Query',
    'limit': 'X-Course-Signal-Limit',
    'runner_id': 'X-Course-Signal-Runner',
}


def result_query_from_headers(route: str, headers) -> dict[str, list[str]] | None:
    """Recover a bounded Results request after Vercel consumes its query string."""
    expected = ({'event_id', 'q', 'limit'} if route == 'results/search'
                else {'event_id', 'runner_id'} if route == 'results/runner' else set())
    values: dict[str, str] = {}
    for field, name in RESULT_HEADER_FIELDS.items():
        found = headers.get_all(name) if hasattr(headers, 'get_all') else None
        if found is None:
            value = headers.get(name) if hasattr(headers, 'get') else None
            found = [] if value is None else [value]
        if len(found) > 1:
            raise server.ResultsRequestError('duplicate Results header')
        if found:
            values[field] = found[0]
    if not values:
        return None
    if set(values) != expected:
        raise server.ResultsRequestError('invalid Results headers')
    return {field: [value] for field, value in values.items()}


def response_for(
    route: str,
    query: dict[str, list[str]] | None = None,
    *,
    completed_cache=None,
    upstream_loader=None,
) -> tuple[int, dict]:
    query = query or {}
    if route in server.RESULTS_ROUTES:
        return server.results_response(
            route, query, completed_cache=completed_cache, upstream_loader=upstream_loader,
        )
    if route == 'health':
        return 200, {'status': 'ok', 'app': server.APP_NAME, 'version': server.APP_VERSION, 'delivery': 'live_api'}
    if route == 'catalog':
        try:
            q, year, limit = server.catalog_request_parameters(query)
            return 200, server.build_catalog_payload(q, year=year, limit=limit)
        except catalog.CatalogQueryError:
            return 400, {'status': 'error', 'message': 'Invalid catalog query'}
        except Exception:
            return 503, {'status': 'error', 'message': 'Race catalog unavailable; retry shortly'}
    if route == 'event':
        try:
            event_id, include_course = server.event_request_parameters(query)
            payload = sanitize_public(server.build_selected_event(event_id, include_course=include_course))
            payload['delivery'] = 'live_api'
            payload['refresh_expected_seconds'] = 15
            return 200, payload
        except (catalog.InvalidIdentifier, catalog.CatalogQueryError):
            return 400, {'status': 'error', 'message': 'Invalid event request'}
        except catalog.UnknownEvent:
            return 404, {'status': 'error', 'message': 'Event not found'}
        except Exception:
            return 503, {'status': 'error', 'message': 'Event data unavailable; retry shortly'}
    if route not in {'bootstrap', 'live'}:
        return 404, {'status': 'error', 'message': 'Not found'}
    try:
        payload = server.build_payload(include_courses=route == 'bootstrap')
        if payload.get('summary', {}).get('errors') or not payload.get('events'):
            return 503, {'status': 'error', 'message': 'Upstream timing incomplete; retain last known data'}
        if route == 'bootstrap' and any(len(e.get('course', {}).get('track_points', [])) < 2 for e in payload['events']):
            return 503, {'status': 'error', 'message': 'Course data incomplete; retry shortly'}
        payload = sanitize_public(payload)
        payload['delivery'] = 'live_api'
        payload['refresh_expected_seconds'] = 15
        return 200, payload
    except Exception:
        return 503, {'status': 'error', 'message': 'Timing service unavailable; retry shortly'}


class handler(BaseHTTPRequestHandler):
    def _is_results_request(self) -> bool:
        parsed = urlparse(self.path)
        if parsed.path in server.RESULTS_PATHS:
            return True
        route = parse_qs(parsed.query, keep_blank_values=True).get('route')
        return isinstance(route, list) and len(route) == 1 and route[0] in server.RESULTS_ROUTES

    def log_request(self, code='-', size='-'):
        if self._is_results_request():
            parsed = urlparse(self.path)
            safe_path = parsed.path if parsed.path in server.RESULTS_PATHS else '/api/results'
            self.log_message('"%s %s %s" %s %s', self.command, safe_path, self.request_version,
                             str(code), str(size))
            return
        super().log_request(code, size)

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', ALLOWED_ORIGIN)
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', ', '.join(RESULT_HEADER_FIELDS.values()))
        self.send_header('X-Content-Type-Options', 'nosniff')
        results = self._is_results_request()
        self.send_header('Cache-Control', 'no-store' if results or status != 200 else 'public, max-age=0, must-revalidate')
        self.send_header('Vercel-CDN-Cache-Control', 'no-store' if results or status != 200 else 'public, s-maxage=5')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        direct_route = parsed.path.removeprefix('/api/').strip('/')
        if direct_route in server.RESULTS_ROUTES:
            route = direct_route
            try:
                query = result_query_from_headers(route, self.headers) or server.parse_results_query(parsed.query)
            except server.ResultsRequestError:
                self._send(400, {'status': 'error', 'message': 'Invalid results request'})
                return
        else:
            route = query.pop('route', [''])[0]
            if not route:
                route = direct_route
            if route in server.RESULTS_ROUTES:
                try:
                    query = result_query_from_headers(route, self.headers)
                    if query is None:
                        query = server.parse_results_query(parsed.query, route_parameter=route)
                except server.ResultsRequestError:
                    self._send(400, {'status': 'error', 'message': 'Invalid results request'})
                    return
        self._send(*response_for(route, query))

    def do_OPTIONS(self):
        self._send(200, {'status': 'ok'})

    def do_POST(self):
        self._send(405, {'status': 'error', 'message': 'Read-only API'})

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST
