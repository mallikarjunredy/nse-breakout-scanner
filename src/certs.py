"""Fixes a common Windows issue where corporate networks (proxies,
antivirus, DLP tools) intercept TLS with a locally-trusted root CA that
isn't in Python's bundled `certifi` store. `requests` usually still works
via other means, but yfinance's HTTP backend (`curl_cffi`) fails with
CERTIFICATE_VERIFY_FAILED / "unable to get local issuer certificate".

This builds a merged CA bundle (the machine's trusted Windows roots +
the standard certifi bundle) once, caches it under data/, and points
CURL_CA_BUNDLE / SSL_CERT_FILE / REQUESTS_CA_BUNDLE at it so every HTTP
library used here trusts the same roots. It's a no-op on non-Windows
platforms and never overrides an env var the user already set.
"""

import base64
import os
import platform

from . import config

_BUNDLE_PATH = config.DATA_DIR / "ca_bundle.pem"


def _der_to_pem(der_bytes: bytes) -> str:
    b64 = base64.b64encode(der_bytes).decode("ascii")
    body = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n"


def _build_bundle() -> None:
    import ssl
    import certifi

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    seen = set()
    pem_blocks = []
    for store in ("ROOT", "CA"):
        try:
            for der, encoding, _trust in ssl.enum_certificates(store):
                if encoding == "x509_asn" and der not in seen:
                    seen.add(der)
                    pem_blocks.append(_der_to_pem(der))
        except Exception:
            continue

    with open(certifi.where(), "r", encoding="ascii") as f:
        certifi_bundle = f.read()

    with open(_BUNDLE_PATH, "w", encoding="ascii") as f:
        f.write("".join(pem_blocks))
        f.write(certifi_bundle)


def ensure_ca_bundle() -> str | None:
    """Builds (if needed) and activates the merged CA bundle. Returns the
    bundle path, or None if not applicable (non-Windows).
    """
    if platform.system() != "Windows":
        return None

    if not _BUNDLE_PATH.exists():
        try:
            _build_bundle()
        except Exception:
            return None

    path = str(_BUNDLE_PATH)
    os.environ.setdefault("CURL_CA_BUNDLE", path)
    os.environ.setdefault("SSL_CERT_FILE", path)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", path)
    return path
