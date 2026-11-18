"""Restricted REA website inspection for Titan."""
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REA_VERSION = "6.7.0"
CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
CDP_ENDPOINT = f"http://{CDP_HOST}:{CDP_PORT}"
CDP_VERSION_URL = f"{CDP_ENDPOINT}/json/version"
MAX_INSPECTION_CHARS = 3_500
_TARGET_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_LOCK = threading.Lock()
_BROWSER_STARTED_BY_TITAN = False


class ReaInspectionError(RuntimeError):
    """A user-facing error from the restricted REA inspection flow."""


def _validate_page_url(value):
    if not isinstance(value, str):
        raise ReaInspectionError("Give me a complete http:// or https:// page URL.")
    value = value.strip()
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as err:
        raise ReaInspectionError(f"That page URL is invalid: {err}") from err
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ReaInspectionError("I can inspect only an explicit http:// or https:// page URL.")
    if parsed.username or parsed.password:
        raise ReaInspectionError("For safety, page URLs containing login credentials are not allowed.")
    if parsed.query or parsed.fragment:
        raise ReaInspectionError(
            "For privacy, remove the URL query string or fragment and try the page URL again."
        )

    hostname = parsed.hostname.rstrip(".").casefold()
    if not hostname or "%" in hostname:
        raise ReaInspectionError("That page URL has an unsupported host name.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None

    loopback_host = hostname == "localhost" or hostname.endswith(".localhost")
    if address is not None:
        if not address.is_loopback and not address.is_global:
            raise ReaInspectionError("Only public hosts or a loopback development server are allowed.")
    elif not loopback_host:
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError as err:
            raise ReaInspectionError("That page URL has an invalid international host name.") from err
        try:
            resolved = {
                ipaddress.ip_address(item[4][0].split("%", 1)[0])
                for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as err:
            raise ReaInspectionError(f"I couldn't resolve the requested page host: {err}") from err
        if not resolved or any(not item.is_global for item in resolved):
            raise ReaInspectionError(
                "That host does not resolve only to public addresses. Local development sites must use localhost or a loopback IP."
            )

    if port is not None and not 1 <= port <= 65535:
        raise ReaInspectionError("The page URL's port must be between 1 and 65535.")
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    origin = f"{parsed.scheme.casefold()}://{display_host}"
    default_port = 80 if parsed.scheme.casefold() == "http" else 443
    if port is not None and port != default_port:
        origin += f":{port}"
    normalized_url = f"{origin}{parsed.path or '/'}"
    return normalized_url, origin


def _chrome_executable():
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ReaInspectionError(
        "I couldn't find Chrome or Edge. Install a Chrome-family browser to use REA website inspection."
    )


def _debug_browser_version():
    try:
        with urllib.request.urlopen(CDP_VERSION_URL, timeout=1) as response:
            version = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    if not isinstance(version, dict):
        raise ReaInspectionError("The debugging endpoint returned an invalid browser response.")
    socket_url = version.get("webSocketDebuggerUrl", "")
    expected_prefix = f"ws://{CDP_HOST}:{CDP_PORT}/devtools/browser/"
    if not socket_url.startswith(expected_prefix):
        raise ReaInspectionError(
            f"Port {CDP_PORT} is responding, but it is not Titan's expected loopback Chrome debugging endpoint."
        )
    return version


def _open_isolated_browser(page_url):
    global _BROWSER_STARTED_BY_TITAN
    configured_profile = os.environ.get("TITAN_REA_PROFILE_DIR")
    profile = (
        Path(configured_profile).expanduser()
        if configured_profile
        else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Titan" / "rea-browser-profile"
    )
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        raise ReaInspectionError(f"I couldn't prepare Titan's isolated browser profile: {err}") from err
    executable = _chrome_executable()
    version = _debug_browser_version()
    if version is not None and not _BROWSER_STARTED_BY_TITAN:
        raise ReaInspectionError(
            f"Port {CDP_PORT} is already in use by a browser Titan did not start. "
            "Close the other debugging browser and try again."
        )

    arguments = [
        str(executable),
        f"--remote-debugging-address={CDP_HOST}",
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-extensions",
        page_url,
    ]
    try:
        subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except OSError as err:
        raise ReaInspectionError(f"I couldn't open the isolated browser: {err}") from err

    if version is None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            version = _debug_browser_version()
            if version is not None:
                _BROWSER_STARTED_BY_TITAN = True
                break
            time.sleep(0.25)
        if version is None:
            raise ReaInspectionError(
                "The isolated browser did not expose its loopback debugging port. "
                "Close it if it opened, then check that port 9222 is available."
            )


def _rea_launcher():
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if not npx:
        raise ReaInspectionError("Node.js and npm are required to start the REA command.")
    npx_directory = Path(npx).resolve().parent
    node = npx_directory / "node.exe"
    npx_cli = npx_directory / "node_modules" / "npm" / "bin" / "npx-cli.js"
    if not node.is_file() or not npx_cli.is_file():
        raise ReaInspectionError("I couldn't locate the Node.js/npm launcher needed for REA.")
    return node, npx_cli


def _run_rea(command, arguments):
    node, npx_cli = _rea_launcher()
    invocation = [
        str(node),
        str(npx_cli),
        "--yes",
        f"rea-agents@{REA_VERSION}",
        command,
        *arguments,
        "--format",
        "json",
    ]
    try:
        completed = subprocess.run(
            invocation,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as err:
        raise ReaInspectionError(
            f"REA's {command} operation timed out. The page or browser may be busy; try again."
        ) from err
    except OSError as err:
        raise ReaInspectionError(f"I couldn't start REA: {err}") from err

    output = completed.stdout.strip()
    try:
        result = json.loads(output)
    except json.JSONDecodeError as err:
        detail = completed.stderr.strip()[-500:]
        if not detail:
            detail = output[-500:] or "REA returned no output."
        raise ReaInspectionError(f"REA returned an unreadable result: {detail}") from err
    if completed.returncode != 0:
        message = result.get("message") if isinstance(result, dict) else ""
        reason = result.get("details", {}).get("reason") if isinstance(result, dict) else ""
        detail = message or reason or completed.stderr.strip()[-500:] or "REA could not inspect that page."
        raise ReaInspectionError(str(detail))
    if not isinstance(result, dict):
        raise ReaInspectionError("REA returned an unexpected page-analysis result.")
    return result


def _normalized_result(result, expected_keys):
    candidates = [result]
    for key in ("normalized_result", "normalizedResult", "result", "data", "evidence"):
        value = result.get(key)
        if isinstance(value, dict):
            candidates.append(value)
            for nested_key in ("normalized_result", "normalizedResult", "result", "data"):
                nested = value.get(nested_key)
                if isinstance(nested, dict):
                    candidates.append(nested)
    return next((item for item in candidates if expected_keys.issubset(item)), None)


def _clean_url(value):
    if not isinstance(value, str):
        return value
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"}:
            return f"{parsed.scheme}:" if parsed.scheme else parsed.path
        hostname = parsed.hostname
        if not hostname:
            return parsed.path
        host = f"[{hostname}]" if ":" in hostname else hostname
        authority = host + (f":{parsed.port}" if parsed.port is not None else "")
        return urllib.parse.urlunsplit((parsed.scheme, authority, parsed.path, "", ""))
    except ValueError:
        return value.split("?", 1)[0].split("#", 1)[0]


def _public_evidence(listing, inspection):
    targets = _normalized_result(listing, {"targets"})
    page = _normalized_result(inspection, {"target", "dom", "scripts", "network"})
    if targets is None or page is None:
        raise ReaInspectionError("REA's response did not contain the expected browser evidence.")

    def target_view(target):
        return {
            "target_id": target.get("target_id"),
            "title": target.get("title"),
            "url": _clean_url(target.get("url")),
            "origin": target.get("origin"),
        }

    requests = page.get("network", {}).get("requests", [])
    return {
        "page": target_view(page.get("target", {})),
        "dom": {
            "total_nodes": page.get("dom", {}).get("total_nodes"),
            "node_types": [
                node.get("node_name")
                for node in page.get("dom", {}).get("nodes", [])[:40]
            ],
        },
        "scripts": {
            "total": page.get("scripts", {}).get("total"),
            "items": [
                {
                    "url": _clean_url(item.get("url")),
                    "type": item.get("type"),
                }
                for item in page.get("scripts", {}).get("items", [])[:20]
            ],
        },
        "network": {
            "requests_observed": len(requests),
            "prior_activity_available": page.get("network", {}).get("prior_activity_available", False),
            "requests": [
                {
                    "url": _clean_url(item.get("url")),
                    "method": item.get("method"),
                    "status": item.get("status"),
                    "mime_type": item.get("mime_type"),
                    "resource_type": item.get("resource_type"),
                }
                for item in requests[:20]
            ],
        },
        "completeness": page.get("completeness"),
        "limitations": page.get("limitations", []),
    }


def inspect_website(url):
    """Open one explicitly requested URL in an isolated browser and inspect it passively."""
    page_url, origin = _validate_page_url(url)
    with _LOCK:
        _open_isolated_browser(page_url)
        listing = _run_rea(
            "list-browser-targets",
            [CDP_ENDPOINT, "--allowed-origins", origin],
        )
        target_list = _normalized_result(listing, {"targets"})
        if target_list is None:
            raise ReaInspectionError("REA could not list the requested browser page.")

        target = next(
            (
                item for item in target_list["targets"]
                if item.get("origin") == origin
                and item.get("url") == page_url
                and item.get("type") == "page"
            ),
            None,
        )
        if target is None:
            raise ReaInspectionError(
                "I couldn't find the exact requested URL in the isolated browser. "
                "If the page redirected to another origin, ask me to inspect that URL explicitly."
            )
        target_id = target.get("target_id", "")
        if not isinstance(target_id, str) or not _TARGET_ID_RE.fullmatch(target_id):
            raise ReaInspectionError("REA returned an invalid browser target identifier.")

        inspection = _run_rea(
            "inspect-web-page",
            [
                CDP_ENDPOINT,
                target_id,
                "--allowed-origins",
                origin,
                "--observation-ms",
                "500",
            ],
        )

    evidence = _public_evidence(listing, inspection)
    return json.dumps(evidence, ensure_ascii=True, separators=(",", ":"))[:MAX_INSPECTION_CHARS]
