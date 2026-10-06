/* Escaneo de QR con la cámara del navegador, como alternativa al lector
 * USB (no lo reemplaza). Requiere HTTPS o localhost -- los navegadores
 * bloquean getUserMedia en http:// sobre cualquier otra dirección, así
 * que si esto no funciona en el teléfono, lo primero que hay que revisar
 * es que se entró con https:// y no http://.
 *
 * Reutiliza el mismo <input> y <form> que ya usa el lector USB / tecleo
 * manual: al decodificar un QR, solo llena el input y envía el form, sin
 * duplicar ninguna lógica de negocio (esa vive en escaneo.py).
 */
function iniciarEscanerCamara(opciones) {
  const { botonActivar, botonCancelar, video, canvas, contenedor, input, form, mensaje } = opciones;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  let stream = null;
  let activo = false;
  let pausadoHasta = 0;
  let ultimoCodigo = null;
  let ultimoEn = 0;

  function mostrarError(texto) {
    mensaje.textContent = texto;
    mensaje.hidden = false;
  }

  function detener() {
    activo = false;
    if (stream) {
      stream.getTracks().forEach((pista) => pista.stop());
      stream = null;
    }
    contenedor.hidden = true;
  }

  function tick() {
    if (!activo) return;
    if (video.readyState === video.HAVE_ENOUGH_DATA) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const imagen = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const codigo = jsQR(imagen.data, imagen.width, imagen.height);
      const ahora = Date.now();
      if (codigo && codigo.data && ahora > pausadoHasta) {
        // Modo continuo: la cámara sigue encendida para el siguiente equipo.
        // Se ignora el mismo código por 3 s para no registrarlo dos veces.
        if (codigo.data === ultimoCodigo && ahora - ultimoEn < 3000) {
          pausadoHasta = ahora + 500;
        } else {
          ultimoCodigo = codigo.data;
          ultimoEn = ahora;
          pausadoHasta = ahora + 1500;
          input.value = codigo.data;
          if (form.requestSubmit) form.requestSubmit(); else form.submit();
        }
      }
    }
    requestAnimationFrame(tick);
  }

  async function activar() {
    mensaje.hidden = true;

    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      mostrarError("Este navegador no puede usar la cámara aquí. Revisa que entraste con https:// (no http://).");
      return;
    }

    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } } });
    } catch (err) {
      mostrarError("No se pudo abrir la cámara: " + err.message);
      return;
    }

    video.srcObject = stream;
    try {
      await video.play();
    } catch (err) {
      mostrarError("No se pudo iniciar el video de la cámara: " + err.message);
      detener();
      return;
    }

    contenedor.hidden = false;
    activo = true;
    requestAnimationFrame(tick);
  }

  botonActivar.addEventListener("click", activar);
  botonCancelar.addEventListener("click", detener);
}
