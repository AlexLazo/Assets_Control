from pathlib import Path

from app import create_app

app = create_app()

CERT_PATH = Path(app.instance_path) / "cert.pem"
KEY_PATH = Path(app.instance_path) / "key.pem"

if __name__ == "__main__":
    if CERT_PATH.exists() and KEY_PATH.exists():
        print(f"Sirviendo por HTTPS con el certificado de {CERT_PATH}")
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True, ssl_context=(str(CERT_PATH), str(KEY_PATH)))
    else:
        print("No hay certificado (instance/cert.pem) -- sirviendo por HTTP simple.")
        print("El lector USB y el uso normal funcionan igual, pero la cámara del teléfono no va a funcionar")
        print("hasta correr: python scripts/generar_certificado.py")
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
