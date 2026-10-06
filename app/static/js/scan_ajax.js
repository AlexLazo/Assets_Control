/* Escaneo rápido: envía cada código sin recargar la página y avisa el
 * resultado de tres formas a la vez, para que en el teléfono nunca haya duda
 * de si se registró:
 *   - un aviso flotante grande arriba de la pantalla (visible aunque la
 *     cámara ocupe todo el espacio),
 *   - un pitido distinto para éxito / aviso / error,
 *   - vibración (Android).
 * Además, window.avisoLeido() da un "tic" inmediato apenas la cámara lee un
 * QR, antes de que el servidor responda. Si algo falla (sin JS, sesión
 * vencida), el formulario sigue funcionando como un envío normal.
 */
(function () {
  let audioCtx = null;

  // Los navegadores del teléfono mantienen el audio bloqueado hasta que el
  // usuario toca la pantalla; por eso se crea y reanuda en el primer toque o
  // tecla (y de nuevo en cada uno por si el sistema lo vuelve a suspender).
  function desbloquear() {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === "suspended") audioCtx.resume();
    } catch (e) { /* sin audio */ }
  }
  ["pointerdown", "touchstart", "click", "keydown"].forEach((ev) =>
    window.addEventListener(ev, desbloquear, { passive: true })
  );

  function tono(freq, inicio, dur, tipo, vol) {
    if (!audioCtx) return;
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = tipo;
    osc.frequency.value = freq;
    gain.gain.value = vol;
    osc.connect(gain).connect(audioCtx.destination);
    osc.start(audioCtx.currentTime + inicio);
    osc.stop(audioCtx.currentTime + inicio + dur);
  }

  function vibrar(patron) {
    try { if (navigator.vibrate) navigator.vibrate(patron); } catch (e) { /* sin vibración */ }
  }

  function pitido(nivel) {
    desbloquear();
    if (nivel === "ok") {
      tono(880, 0, 0.14, "sine", 0.3);
      vibrar(90);
    } else if (nivel === "aviso") {
      tono(660, 0, 0.12, "sine", 0.3);
      tono(990, 0.16, 0.16, "sine", 0.3);
      vibrar([90, 70, 90]);
    } else {
      tono(200, 0, 0.2, "square", 0.25);
      tono(150, 0.26, 0.28, "square", 0.25);
      vibrar([300, 120, 300]);
    }
  }

  // "Tic" instantáneo al leer el QR con la cámara.
  window.avisoLeido = function () {
    desbloquear();
    tono(1400, 0, 0.06, "sine", 0.25);
    vibrar(30);
  };

  let toast = null;
  let toastTimer = null;
  function mostrarToast(nivel, texto) {
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "toast-scan";
      document.body.appendChild(toast);
    }
    toast.className = "toast-scan " + nivel;
    toast.textContent = texto;
    toast.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.style.display = "none"; }, nivel === "ok" ? 2500 : 6000);
  }

  window.iniciarScanAjax = function (op) {
    const { form, input, banner, contador, historial } = op;

    function mostrar(nivel, texto) {
      banner.className = "resultado-scan " + nivel;
      banner.textContent = texto;
      banner.hidden = false;
      mostrarToast(nivel, texto);
      pitido(nivel);
    }

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const codigo = input.value.trim();
      if (!codigo) return;
      // Se arma el FormData y se limpia el campo al instante, sin
      // deshabilitarlo: así un lector que escanea muy rápido el siguiente
      // equipo no pierde caracteres mientras el anterior se está enviando.
      const cuerpo = new FormData(form);
      input.value = "";
      try {
        const resp = await fetch(form.action || window.location.href, {
          method: "POST",
          body: cuerpo,
          headers: { "X-Requested-With": "fetch" },
        });
        if (resp.redirected && resp.url.includes("/login")) {
          window.location.reload();
          return;
        }
        const datos = await resp.json();
        mostrar(datos.nivel || (datos.ok ? "ok" : "error"), datos.mensaje);
        if (datos.contador !== undefined && contador) contador.textContent = datos.contador;
        if (datos.ok && datos.fila && historial) {
          const tr = document.createElement("tr");
          datos.fila.forEach((celda) => {
            const td = document.createElement("td");
            td.textContent = celda;
            tr.appendChild(td);
          });
          historial.prepend(tr);
          while (historial.children.length > 10) historial.lastElementChild.remove();
        }
      } catch (e) {
        mostrar("error", "No se pudo registrar (¿sin conexión?). Intenta de nuevo.");
      } finally {
        input.focus();
      }
    });
  };
})();
