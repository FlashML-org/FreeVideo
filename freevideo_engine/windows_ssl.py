"""Run by ComfyUI before it imports its server (and aiohttp), through the prestartup loader the launcher
writes: keep one unreadable Windows root certificate from stopping the start."""
import ssl
import sys
import warnings


def tolerate_unreadable_store_certificates():
    """Windows hands Python every trusted root as one block, and OpenSSL rejects the whole block when a
    single certificate in it does not parse. Without this, creating any default TLS context raises
    "[ASN1] nested asn1 error" and ComfyUI quits on its first import of aiohttp.

    Only after that failure are the certificates loaded one by one, skipping the ones OpenSSL cannot
    read. Roots installed by an administrator stay trusted, and a store without a bad certificate takes
    exactly the same path as before."""
    original = getattr(ssl.SSLContext, '_load_windows_store_certs', None)
    if sys.platform != 'win32' or original is None or getattr(original, 'freevideo_tolerant', False):
        return False

    def load(self, storename, purpose):
        try:
            return original(self, storename, purpose)
        except ssl.SSLError:
            pass
        # Read again on every context, so a root an administrator removes later is not trusted from memory.
        certificates = []
        try:
            probe = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            for certificate, encoding, trust in ssl.enum_certificates(storename):
                if encoding != 'x509_asn' or not (trust is True or purpose.oid in trust):
                    continue
                try:
                    probe.load_verify_locations(cadata=certificate)
                except ssl.SSLError:
                    continue
                certificates.append(certificate)
        except PermissionError:
            warnings.warn('unable to enumerate Windows certificate store')
        loaded = bytearray(b''.join(certificates))
        if loaded:
            self.load_verify_locations(cadata=loaded)
        return loaded

    load.freevideo_tolerant = True
    ssl.SSLContext._load_windows_store_certs = load
    return True


try:
    tolerate_unreadable_store_certificates()
except Exception as error:
    print('[FreeVideo] Windows certificate store check skipped: %s' % error)
