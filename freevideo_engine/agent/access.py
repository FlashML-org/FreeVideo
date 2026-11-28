"""Peer and browser-origin policy, independent of bearer authentication."""
import ipaddress
from urllib.parse import urlsplit

from .jobs import JobError
from .. import proxy_identity
from ..origin_scheme import effective_scheme


def peer_address(request):
    peer = request.transport.get_extra_info('peername') if request.transport else None
    try:
        address = ipaddress.ip_address(peer[0].split('%')[0])
        address = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
        if address.is_loopback and proxy_identity.trusted(request):
            address = ipaddress.ip_address(request.headers.get(proxy_identity.PEER, '').split('%')[0])
        return address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
    except (TypeError, ValueError, IndexError):
        return None


def lan_enabled():
    from ..comfy_bridge import installation_root
    from ..desktop_runtime import launcher_root
    from ..launcher_settings import Store, read
    # The launcher's switch saves into the launcher folder, the page's switch into the installation. Access needs
    # every copy of one of the two to say yes, and the most recently saved copy anywhere to say yes too, so a switch
    # turned off in one place stays off even when another place still holds an older yes.
    try:
        stores = [Store(root()) for root in (installation_root, launcher_root)]
        copies = [read(path) for store in stores for path in store.paths]
        newest = max(copies, key=lambda saved: saved.get('saved_at_ns', 0))
        return newest.get('lan_access') is True and any(store.currently_allows('lan_access') for store in stores)
    except (OSError, ValueError):
        return False


def check(request, *, local=False, lan=False):
    address = peer_address(request)
    proxied = proxy_identity.HEADER in request.headers
    # Only explicitly listed browser routes may use a verified proxy device.
    local_only = local and not (proxied and lan and bool(proxy_identity.device(request)))
    if (proxied and (not proxy_identity.trusted(request) or local_only or not lan_enabled())
            or address is None or not address.is_loopback and (local_only or not address.is_private or not lan_enabled())):
        raise JobError('local_only', 'Peer is not permitted')
    socket = request.transport.get_extra_info('sockname') if request.transport else None
    # Why the request was refused, in facts of this computer only (never what the request itself named): the page says
    # what to open. 'port' carries the port FreeVideo answers on.
    reason, listening = 'name', None
    try:
        host = urlsplit(request.scheme + '://' + request.host)
        allowed = {socket[0].split('%')[0]} if socket else set()
        if address.is_loopback:
            allowed.update(('localhost', '127.0.0.1', '::1'))
        scheme = request.scheme
        if proxied:
            proxy_origin = urlsplit(request.headers.get(proxy_identity.ORIGIN, ''))
            if (host.hostname, host.port) == (proxy_origin.hostname, proxy_origin.port):
                allowed.add(proxy_origin.hostname)
                socket = (proxy_origin.hostname, proxy_origin.port)
                scheme = proxy_origin.scheme
        if host.hostname not in allowed:
            raise ValueError
        if host.port not in (None, socket[1]):
            # The port this computer answers on; not through the LAN proxy, where it would be the loopback port behind
            # it, which another device can neither reach nor needs.
            reason, listening = 'port', None if proxied else socket[1]
            raise ValueError
        reason = 'origin'
        if len(request.headers.getall('Origin', [])) > 1:
            raise ValueError
        origin = request.headers.get('Origin')
        if origin is not None:
            parsed = urlsplit(origin)
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            wanted = scheme if proxied else effective_scheme(request, parsed.scheme)
            own_port = host.port or (443 if wanted == 'https' else 80)
            if (parsed.scheme != wanted or parsed.hostname != host.hostname or port != own_port
                    or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
                raise ValueError
    except (ValueError, TypeError, IndexError):
        detail = dict(reason=reason, **({'port': listening} if isinstance(listening, int) else {}))
        raise JobError('origin_forbidden', 'Origin or Host does not identify this instance', details=[detail]) from None
