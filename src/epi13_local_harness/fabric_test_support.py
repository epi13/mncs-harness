"""Shared ephemeral Fabric fixtures for tests and the E2E gauntlet.

Moved out of ``tests/test_persistent_fabric.py`` so the checked-in
gauntlet (``e2e_gauntlet.py``) can spin the same proven persistent-service
pattern without duplicating TLS setup. Test-only in spirit, product-grade
in behavior: one-day throwaway certificates, loopback only.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def ephemeral_certificates(root: Path, openssl: str) -> dict[str, Path]:
    """Mint a throwaway CA plus server/client certificates under ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = root / "ca.key", root / "ca.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(ca_key),
            "-out",
            str(ca_cert),
            "-subj",
            "/CN=Harness Fabric ephemeral CA",
            "-days",
            "1",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    result: dict[str, Path] = {"ca": ca_cert}
    for name in ("server", "client"):
        key, csr, cert = root / f"{name}.key", root / f"{name}.csr", root / f"{name}.pem"
        subprocess.run(
            [
                openssl,
                "req",
                "-new",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(csr),
                "-subj",
                f"/CN=Harness Fabric {name}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                openssl,
                "x509",
                "-req",
                "-in",
                str(csr),
                "-CA",
                str(ca_cert),
                "-CAkey",
                str(ca_key),
                "-CAcreateserial",
                "-out",
                str(cert),
                "-days",
                "1",
                "-sha256",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result[name] = cert
        result[f"{name}_key"] = key
    return result
