/* Escaneo rápido: envía cada código sin recargar la página, muestra un
 * resultado grande (verde/rojo), suena un pitido distinto para éxito y error,
 * actualiza el contador y la lista de últimos escaneos, y deja el cursor
 * listo para el siguiente. Si algo falla (sin JS, sesión vencida), el
 * formulario sigue funcionando como un envío normal.
 */
(function () {
  let audioCtx = null;

  function pitido(ok) {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const tonos = ok ? [[880, 0, 0.12]] : [[220, 0, 0.16], [180, 0.2, 0.2]];
      tonos.forEach(([freq, inicio, dur]) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = ok ? "sine" : "square";
        osc.frequency.value = freq;
        gain.gain.value = 0.15;
        osc.connect(gain).connect(audioCtx.destination);
        osc.start(audioCtx.currentTime + inicio);
        osc.stop(audioCtx.currentTime + inicio + dur);
      });
    } catch (e) { /* sin audio: no pasa nada */ }
  }

  window.iniciarScanAjax = function (op) {
    const { form, input, banner, contador, historial } = op;

    function mostrar(ok, texto) {
      banner.className = "resultado-scan " + (ok ? "ok" : "error");
      banner.textContent = texto;
      banner.hidden = false;
      pitido(ok);
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
        mostrar(datos.ok, datos.mensaje);
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
        mostrar(false, "No se pudo registrar (¿sin conexión?). Intenta de nuevo.");
      } finally {
        input.focus();
      }
    });
  };
})();
