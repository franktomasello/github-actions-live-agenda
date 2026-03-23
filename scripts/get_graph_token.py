#!/usr/bin/env python3
"""
Obtain a Microsoft Graph refresh token for the Live Agenda app.

This runs a one-time interactive OAuth2 flow:
  1. Opens your browser to Microsoft's consent page
  2. You sign in and grant Calendars.Read permission
  3. A local server catches the redirect and exchanges the code for tokens
  4. Prints the refresh token to paste into Cloudflare env vars

Prerequisites:
  - Register an app at https://portal.azure.com → Azure Active Directory
    → App registrations → New registration
  - Add redirect URI: http://localhost:3847/callback  (Web platform)
  - Create a client secret under Certificates & secrets
  - Under API permissions, add Microsoft Graph → Delegated → Calendars.Read

Usage:
  python scripts/get_graph_token.py \\
      --client-id  YOUR_CLIENT_ID \\
      --client-secret YOUR_CLIENT_SECRET \\
      --tenant-id  YOUR_TENANT_ID
"""

from __future__ import annotations

import argparse
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser


REDIRECT_PORT = 3847
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
SCOPES = "offline_access Calendars.Read"


def exchange_code(code: str, client_id: str, client_secret: str, tenant_id: str) -> dict:
    """Exchange authorization code for tokens."""
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "scope": SCOPES,
    }).encode()

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main() -> None:
    parser = argparse.ArgumentParser(description="Obtain a Microsoft Graph refresh token")
    parser.add_argument("--client-id", required=True, help="Azure AD app client ID")
    parser.add_argument("--client-secret", required=True, help="Azure AD app client secret")
    parser.add_argument("--tenant-id", required=True, help="Azure AD tenant ID (or 'common')")
    args = parser.parse_args()

    state = secrets.token_urlsafe(32)
    auth_result: dict = {}
    error_result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass  # silence request logs

        def do_GET(self) -> None:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

            if "error" in qs:
                error_result["error"] = qs["error"][0]
                error_result["description"] = qs.get("error_description", [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h2>Authorization failed.</h2><p>You can close this tab.</p>")
                threading.Thread(target=self.server.shutdown).start()
                return

            code = qs.get("code", [None])[0]
            returned_state = qs.get("state", [None])[0]

            if not code or returned_state != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid callback")
                return

            try:
                tokens = exchange_code(code, args.client_id, args.client_secret, args.tenant_id)
                auth_result.update(tokens)
            except Exception as exc:
                error_result["error"] = str(exc)

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h2>&#9989; Success!</h2>"
                b"<p>Refresh token obtained. Return to your terminal.</p>"
                b"<p>You can close this tab.</p>"
            )
            threading.Thread(target=self.server.shutdown).start()

    # Build authorization URL
    auth_params = urllib.parse.urlencode({
        "client_id": args.client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "response_mode": "query",
        "scope": SCOPES,
        "state": state,
    })
    auth_url = f"https://login.microsoftonline.com/{args.tenant_id}/oauth2/v2.0/authorize?{auth_params}"

    print()
    print("Opening browser for Microsoft sign-in...")
    print(f"  (If it doesn't open, visit: {auth_url})")
    print()

    webbrowser.open(auth_url)

    server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)
    server.serve_forever()

    if error_result:
        print(f"ERROR: {error_result.get('error')}")
        print(f"       {error_result.get('description', '')}")
        sys.exit(1)

    refresh_token = auth_result.get("refresh_token")
    if not refresh_token:
        print("ERROR: No refresh token in response. Ensure 'offline_access' scope is granted.")
        print("       Full response:", json.dumps(auth_result, indent=2))
        sys.exit(1)

    print("=" * 60)
    print("SUCCESS — Add these to your Cloudflare Pages environment:")
    print("=" * 60)
    print()
    print(f"  MS_CLIENT_ID      = {args.client_id}")
    print(f"  MS_CLIENT_SECRET  = {args.client_secret}")
    print(f"  MS_TENANT_ID      = {args.tenant_id}")
    print(f"  MS_REFRESH_TOKEN  = {refresh_token}")
    print()
    print("Set these as encrypted environment variables in:")
    print("  Cloudflare Dashboard → Pages → your project → Settings → Environment variables")
    print()


if __name__ == "__main__":
    main()
