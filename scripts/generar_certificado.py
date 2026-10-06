"""Genera un certificado TLS autofirmado para poder servir la app por
HTTPS -- necesario para que el navegador del teléfono permita usar la
cámara (getUserMedia solo funciona en "contexto seguro": https:// o
http://localhost, nunca http:// sobre una IP de red como 10.51.109.8).

Incluye como SAN (Subject Alternative Name) localhost, 127.0.0.1 y la IP
real de la PC en la red local, detectada automáticamente. Válido 10 años
-- se corre una sola vez (o de nuevo si la IP de la PC cambia).

Uso:
    python scripts/generar_certificado.py
"""
from __future__ import annotations

import datetime
import ipaddress
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

BASE_DIR = Path(__file__).resolve().parent.parent
CERT_PATH = BASE_DIR / "instance" / "cert.pem"
KEY_PATH = BASE_DIR / "instance" / "key.pem"


def detectar_ip_local() -> str:
    """IP de esta PC en la red local. No hace falta que el destino
    responda -- abrir un socket UDP solo obliga al sistema operativo a
    elegir qué interfaz de red usaría, y de ahí se lee la IP local."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    CERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ip_local = detectar_ip_local()

    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip_local)])
    ahora = datetime.datetime.now(datetime.timezone.utc)

    nombres_alternativos = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address(ip_local)),
    ]

    certificado = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(days=1))
        .not_valid_after(ahora + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(nombres_alternativos), critical=False)
        .sign(clave, hashes.SHA256())
    )

    KEY_PATH.write_bytes(
        clave.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_PATH.write_bytes(certificado.public_bytes(serialization.Encoding.PEM))

    print(f"Certificado generado para IP {ip_local} (válido 10 años).")
    print(f"  {CERT_PATH}")
    print(f"  {KEY_PATH}")
    print("\nLa próxima vez que arranques el servidor (iniciar_servidor.bat), va a servir por HTTPS.")
    print(f"Entra escribiendo https:// explícito, ej: https://{ip_local}:5000")
    print("El navegador va a avisar 'conexión no privada' la primera vez en cada dispositivo -- es normal")
    print("con un certificado autofirmado; se acepta una vez con \"Avanzado -> Continuar\".")


if __name__ == "__main__":
    main()
