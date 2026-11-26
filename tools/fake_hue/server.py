"""The fake bridge's endpoints and HTTPS.

The Hue API calls that sonoff2hue makes: ``GET /api/0/config`` (anonymous), pairing with ``POST /api``, and with the
``hue-application-key`` header ``GET /clip/v2/resource/{light,room,zone,grouped_light,bridge}``, one of them by its id
(``GET /clip/v2/resource/{type}/{id}``), ``PUT /clip/v2/resource/{light,grouped_light}/{id}`` with
``{"on":{"on":true}}``, and ``POST /clip/v2/resource/zone``, which creates a zone of lights. The test endpoints under
``/_fake/`` need no key; ``rename`` and ``delete`` change a light, room or zone as the Hue app does.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
import ssl
import tempfile
from typing import Any

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .bridge import BRIDGE_ID, LINK_BUTTON_WINDOW, NAME, FakeBridge, RefusedError

_LOGGER = logging.getLogger("fake_hue")

HELP = """Fake Hue bridge: test-only endpoints
  POST /_fake/link-button  press the link button: pairing is open for 30 seconds
  GET  /_fake/lights       the lights, rooms and zones, on or off
  GET  /_fake/puts         every PUT to a light or grouped light, in order: what it switched, or why not
  GET  /_fake/posts        every POST of a zone, in order: the zone and its lights, or why not
  GET  /_fake/requests     every request to the Hue API, with its status
  POST /_fake/reset        all lights off, the zones that POSTs created gone, the logs cleared, link button released;
                           application keys stay
  POST /_fake/rename/{id}?name=<name>
                           rename the light, room or zone with that id
  POST /_fake/delete/{id}  delete the light, room or zone with that id, as the Hue app does

"""

_APP_KEY_HEADER = "hue-application-key"
_BRIDGE = web.AppKey("bridge", FakeBridge)


def create_app(bridge: FakeBridge) -> web.Application:
    app = web.Application(middlewares=[_record_requests])
    app[_BRIDGE] = bridge
    app.router.add_get("/api/0/config", _public_config)
    app.router.add_post("/api", _pair)
    app.router.add_get("/clip/v2/resource/{type}", _get_resources)
    app.router.add_get("/clip/v2/resource/{type}/{id}", _get_resource)
    app.router.add_put("/clip/v2/resource/{type}/{id}", _put)
    app.router.add_post("/clip/v2/resource/zone", _create_zone)
    app.router.add_get("/_fake/", _help)
    app.router.add_post("/_fake/link-button", _press_link_button)
    app.router.add_get("/_fake/lights", _lights)
    app.router.add_get("/_fake/puts", _puts)
    app.router.add_get("/_fake/posts", _posts)
    app.router.add_get("/_fake/requests", _requests)
    app.router.add_post("/_fake/reset", _reset)
    app.router.add_post("/_fake/rename/{id}", _rename)
    app.router.add_post("/_fake/delete/{id}", _delete)
    return app


@web.middleware
async def _record_requests(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    """Every request to the Hue API, with the status it got; requests to the test endpoints aren't recorded."""
    if request.path.startswith("/_fake"):
        return await handler(request)

    bridge = request.app[_BRIDGE]
    fault = bridge.take_fault(request.method, request.path)
    if fault is not None:
        await asyncio.sleep(fault.delay)
        response: web.StreamResponse = web.Response(
            status=fault.status, text=fault.body, content_type=fault.content_type, charset="utf-8"
        )
    else:
        try:
            response = await handler(request)
        except web.HTTPException as err:
            response = err
    bridge.record_request(request.method, request.path_qs, response.status)
    _LOGGER.info("%s %s: %s", request.method, request.path_qs, response.status)
    if isinstance(response, web.HTTPException):
        raise response
    return response


async def _public_config(_: web.Request) -> web.Response:
    return _json(FakeBridge.public_config())


async def _pair(request: web.Request) -> web.Response:
    """Creates an application key while the link button is pressed. The answers are a real bridge's, always with status
    200: ``[{"success":{"username":"...","clientkey":"..."}}]``, or an error such as type 101."""
    bridge = request.app[_BRIDGE]
    try:
        body = json.loads(await request.text())
    except ValueError:
        return _pairing_error(2, "/", "body contains invalid JSON")

    device_type = body.get("devicetype") if isinstance(body, dict) else None
    if not isinstance(device_type, str) or not device_type.strip():
        return _pairing_error(5, "/", "invalid/missing parameters in body")
    # "<application, at most 20 characters>#<device, at most 19>"
    if len(device_type) > 40:
        return _pairing_error(7, "/devicetype", f"invalid value, {device_type}, for parameter, devicetype")

    keys = bridge.try_pair()
    if keys is None:
        return _pairing_error(101, "", "link button not pressed")

    success: dict[str, Any] = {"username": keys[0]}
    if isinstance(body, dict) and body.get("generateclientkey") is True:
        success["clientkey"] = keys[1]
    _LOGGER.info("Paired with %s: issued a new application key.", device_type)
    return _json([{"success": success}])


async def _get_resources(request: web.Request) -> web.Response:
    bridge = request.app[_BRIDGE]
    if not bridge.is_authorized(_app_key(request)):
        return _unauthorized()
    resource_type = request.match_info["type"]
    data = bridge.get_resources(resource_type)
    if data is None:
        return _clip_error(404, f"The fake bridge has no resources of type '{resource_type}'.")
    return _json({"errors": [], "data": data})


async def _get_resource(request: web.Request) -> web.Response:
    bridge = request.app[_BRIDGE]
    if not bridge.is_authorized(_app_key(request)):
        return _unauthorized()
    resource_type, resource_id = request.match_info["type"], request.match_info["id"]
    data = bridge.get_resources(resource_type)
    if data is None:
        return _clip_error(404, f"The fake bridge has no resources of type '{resource_type}'.")
    found = [item for item in data if item["id"] == resource_id]
    if not found:
        return _clip_error(404, f"no {resource_type} with the id '{resource_id}'")
    return _json({"errors": [], "data": found})


async def _create_zone(request: web.Request) -> web.Response:
    """Creates a zone of lights, and answers with its reference, as a real bridge does:
    ``{"data":[{"rid":"<zone id>","rtype":"zone"}],"errors":[]}``."""
    bridge = request.app[_BRIDGE]
    path = request.path
    if not bridge.is_authorized(_app_key(request)):
        bridge.record_rejected_post(path, 403, "unauthorized user")
        return _unauthorized()
    try:
        body = json.loads(await request.text())
    except ValueError:
        return _reject_post(bridge, path, 400, "body contains invalid JSON")
    try:
        zone_id = bridge.create_zone(body)
    except RefusedError as err:
        return _reject_post(bridge, path, 400, str(err))
    return _json({"data": [{"rid": zone_id, "rtype": "zone"}], "errors": []})


async def _put(request: web.Request) -> web.Response:
    bridge = request.app[_BRIDGE]
    path = request.path
    if not bridge.is_authorized(_app_key(request)):
        bridge.record_rejected_put(path, 403, "unauthorized user")
        return _unauthorized()

    try:
        body = json.loads(await request.text())
    except ValueError:
        return _reject_put(bridge, path, 400, "body contains invalid JSON")
    on = body.get("on") if isinstance(body, dict) else None
    value = on.get("on") if isinstance(on, dict) else None
    if not isinstance(value, bool):
        return _reject_put(bridge, path, 400, "the body has no boolean on.on")

    resource_type, resource_id = request.match_info["type"], request.match_info["id"]
    if not bridge.try_set_on(resource_type, resource_id, value):
        return _reject_put(bridge, path, 404, f"no {resource_type} with the id '{resource_id}'")
    return _json({"data": [{"rid": resource_id, "rtype": resource_type}], "errors": []})


async def _help(_: web.Request) -> web.Response:
    return _text(HELP)


async def _lights(request: web.Request) -> web.Response:
    return _text(request.app[_BRIDGE].describe_lights())


async def _puts(request: web.Request) -> web.Response:
    return _text(request.app[_BRIDGE].describe_puts())


async def _posts(request: web.Request) -> web.Response:
    return _text(request.app[_BRIDGE].describe_posts())


async def _requests(request: web.Request) -> web.Response:
    return _text(request.app[_BRIDGE].describe_requests())


async def _press_link_button(request: web.Request) -> web.Response:
    bridge = request.app[_BRIDGE]
    if bridge.link_button_always_pressed:
        return _text("The link button is always pressed (FAKE_HUE_LINK_BUTTON=pressed).\n")
    bridge.press_link_button()
    _LOGGER.info("The link button was pressed: pairing is open for %g seconds.", LINK_BUTTON_WINDOW)
    return _text(f"The link button is pressed: pairing is open for {LINK_BUTTON_WINDOW:g} seconds.\n")


async def _reset(request: web.Request) -> web.Response:
    request.app[_BRIDGE].reset()
    _LOGGER.info(
        "Reset: all lights are off, the zones that POSTs created are gone, the logs are empty and the link button is "
        "released."
    )
    return web.Response(status=204)


async def _rename(request: web.Request) -> web.Response:
    """Renames a light, room or zone: 200 with what it was and its new name, 400 without a name or with one that a real
    bridge would refuse, 404 for an unknown id."""
    resource_id = request.match_info["id"]
    name = request.query.get("name", "")
    if not name.strip():
        return _text("Give the new name as ?name=<name>.\n", 400)
    try:
        described = request.app[_BRIDGE].rename(resource_id, name)
    except RefusedError as err:
        return _text(f"Not renamed: {err}.\n", 400)
    if described is None:
        return _text(f"No light, room or zone has the id '{resource_id}'.\n", 404)
    _LOGGER.info("Renamed %s to '%s'.", described, name)
    return _text(f"Renamed {described} to '{name}'.\n")


async def _delete(request: web.Request) -> web.Response:
    """Deletes a light, room or zone: 200 with what it was, 404 for an unknown id."""
    resource_id = request.match_info["id"]
    described = request.app[_BRIDGE].delete(resource_id)
    if described is None:
        return _text(f"No light, room or zone has the id '{resource_id}'.\n", 404)
    _LOGGER.info("Deleted %s.", described)
    return _text(f"Deleted {described}.\n")


def _app_key(request: web.Request) -> str | None:
    values = request.headers.getall(_APP_KEY_HEADER, [])
    return values[0] if len(values) == 1 else None


def _reject_put(bridge: FakeBridge, path: str, status: int, reason: str) -> web.Response:
    bridge.record_rejected_put(path, status, reason)
    return _clip_error(status, reason)


def _reject_post(bridge: FakeBridge, path: str, status: int, reason: str) -> web.Response:
    bridge.record_rejected_post(path, status, reason)
    return _clip_error(status, reason)


def _unauthorized() -> web.Response:
    # A real bridge answers a missing or unknown application key with 403 and this description.
    return _clip_error(403, "unauthorized user")


def _clip_error(status: int, description: str) -> web.Response:
    return _json({"errors": [{"description": description}], "data": []}, status)


def _pairing_error(error_type: int, address: str, description: str) -> web.Response:
    return _json([{"error": {"type": error_type, "address": address, "description": description}}])


def _json(body: Any, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(body, separators=(",", ":")), status=status, content_type="application/json", charset="utf-8"
    )


def _text(text: str, status: int = 200) -> web.Response:
    return web.Response(text=text, status=status, content_type="text/plain", charset="utf-8")


# HTTPS.


def create_certificate(bridge_id: str = BRIDGE_ID, now: datetime | None = None) -> tuple[bytes, bytes]:
    """A self-signed ECDSA P-256 server certificate and its key, in PEM. Like a real bridge's, it is issued to the
    bridge id rather than to an address, so sonoff2hue trusts it only by the SHA-256 it pins when it pairs."""
    now = now or datetime.now(UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, bridge_id.lower()),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, NAME),
        ]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return certificate.public_bytes(serialization.Encoding.PEM), key_pem


def fingerprint(certificate_pem: bytes) -> str:
    """The fingerprint sonoff2hue pins: the SHA-256 of the certificate in upper-case hex."""
    der = x509.load_pem_x509_certificate(certificate_pem).public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest().upper()


def ssl_context(certificate_pem: bytes, key_pem: bytes) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    with tempfile.TemporaryDirectory() as folder:
        certificate_file, key_file = Path(folder, "tls.crt"), Path(folder, "tls.key")
        certificate_file.write_bytes(certificate_pem)
        key_file.write_bytes(key_pem)
        context.load_cert_chain(certificate_file, key_file)
    return context


@dataclass
class RunningBridge:
    """A fake bridge that serves HTTPS until :meth:`stop`."""

    bridge: FakeBridge
    runner: web.AppRunner
    server: asyncio.Server
    """The listening socket's server, which hands each connection to the runner's aiohttp server."""
    address: str
    """``host:port``, as sonoff2hue's bridge address."""
    fingerprint: str
    certificate_pem: bytes

    async def stop(self) -> None:
        """Stops serving and closes every connection: a client that connects meanwhile, such as the integration while a
        test ends, is refused or disconnected, and the event loop reports nothing, also in the debug mode of Home
        Assistant's tests."""
        # asyncio accepts a connection in one turn of the loop and makes its transport in the next, which fails when the
        # server closed in between: an AssertionError in Server._attach in Python 3.14.10 (gh-109564). So the listening
        # sockets aren't read any more, and the connections accepted until then get their transports in one more turn.
        loop = asyncio.get_running_loop()
        for listening in self.server.sockets:
            loop.remove_reader(listening.fileno())
        await asyncio.sleep(0)
        self.server.close()
        # aiohttp closes the connections it serves, after their requests in progress. The others, such as one whose TLS
        # handshake hasn't finished, are aborted here: a client that left such a handshake later would be reported.
        await self.runner.cleanup()
        self.server.abort_clients()
        await self.server.wait_closed()


async def start(bridge: FakeBridge, bind: str = "127.0.0.1", port: int = 0) -> RunningBridge:
    """Serves ``bridge`` over HTTPS on ``bind`` and ``port`` (0: a free port), with a new certificate."""
    certificate_pem, key_pem = create_certificate()
    runner = web.AppRunner(create_app(bridge), access_log=None)
    await runner.setup()
    handler = runner.server
    if handler is None:  # made by setup(), as aiohttp's own TCPSite expects
        raise RuntimeError("aiohttp's runner has no server after its setup.")
    try:
        # What aiohttp's TCPSite does, but the bridge keeps the server, for its stop.
        server = await asyncio.get_running_loop().create_server(
            handler, bind, port, ssl=ssl_context(certificate_pem, key_pem)
        )
    except BaseException:
        await runner.cleanup()
        raise
    host, actual_port = server.sockets[0].getsockname()[:2]
    return RunningBridge(bridge, runner, server, f"{host}:{actual_port}", fingerprint(certificate_pem), certificate_pem)
