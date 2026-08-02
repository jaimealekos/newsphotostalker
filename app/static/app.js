// Light progressive enhancement for the control panel.
(function () {
  var onDashboard = window.location.pathname === "/";
  var ordering = onDashboard && /[?&]order=1(&|$)/.test(window.location.search);

  // Auto-refresh the dashboard so run status/counters update without a manual
  // reload. En modo edición NO: recargar en mitad de un arrastre perdería el
  // orden a medio colocar.
  if (onDashboard && !ordering) {
    var hidden = false;
    document.addEventListener("visibilitychange", function () { hidden = document.hidden; });
    setInterval(function () {
      if (!hidden) window.location.reload();
    }, 20000);
  }

  // Ficha de foto: se ve a 1024 y un clic la lleva a su tamaño real. El pie
  // dice de qué tamaño es la guardada, que no es igual en todas las agencias.
  var photo = document.getElementById("asset-img");
  if (photo) {
    var hint = document.getElementById("zoom-hint");
    var describe = function () {
      if (!hint || !photo.naturalWidth) return;
      var real = photo.naturalWidth + "×" + photo.naturalHeight + " px";
      // Nunca se amplía: si la foto cabe entera, ya está a tamaño real y no hay
      // nada que ofrecer. Solo cuando el ancho de la página la reduce tiene
      // sentido el clic. El estado «ampliada» va primero: ahí el ancho coincide
      // con el nativo y si no, no habría manera de saber cómo volver.
      if (photo.classList.contains("full")) {
        hint.textContent = real + " · a tamaño real, clic para ajustar al ancho";
      } else if (photo.clientWidth >= photo.naturalWidth) {
        hint.textContent = real;
      } else {
        hint.textContent = real + " · reducida para caber, clic para verla entera";
      }
    };
    photo.addEventListener("click", function () {
      photo.classList.toggle("full");
      describe();
    });
    if (photo.complete) describe();
    photo.addEventListener("load", describe);
  }

  if (!ordering) return;

  var table = document.getElementById("panel");
  var status = document.getElementById("order-status");
  if (!table) return;
  var body = table.tBodies[0];
  var dragged = null;
  var saveTimer = null;

  function rows() {
    return Array.prototype.slice.call(body.querySelectorAll("tr[data-type]"));
  }

  function say(text, kind) {
    if (!status) return;
    status.textContent = text;
    status.className = "order-hint small " + (kind === "error" ? "order-error" : "muted");
  }

  // El orden y los títulos de los separadores se guardan de una vez: así
  // arrastrar y renombrar comparten una sola petición.
  function save() {
    var items = rows().map(function (row) {
      var item = { type: row.dataset.type, id: parseInt(row.dataset.id, 10) };
      var input = row.querySelector(".sep-input");
      if (input) item.label = input.value;
      return item;
    });
    say("Guardando…");
    fetch("/panel/order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: items })
    })
      .then(function (res) {
        if (!res.ok) throw new Error(res.status);
        say("Orden guardado ✓");
      })
      .catch(function () {
        say("No se pudo guardar el orden", "error");
      });
  }

  function saveSoon() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 400);
  }

  body.addEventListener("dragstart", function (e) {
    var row = e.target.closest("tr[data-type]");
    if (!row) return;
    dragged = row;
    row.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    // Firefox no arranca el arrastre sin datos en el portapapeles del evento.
    e.dataTransfer.setData("text/plain", row.dataset.id);
  });

  body.addEventListener("dragover", function (e) {
    if (!dragged) return;
    e.preventDefault();
    var over = e.target.closest("tr[data-type]");
    if (!over || over === dragged) return;
    // Mitad de arriba: la fila arrastrada va antes; mitad de abajo, después.
    var box = over.getBoundingClientRect();
    var after = e.clientY > box.top + box.height / 2;
    body.insertBefore(dragged, after ? over.nextSibling : over);
  });

  body.addEventListener("drop", function (e) { e.preventDefault(); });

  body.addEventListener("dragend", function () {
    if (!dragged) return;
    dragged.classList.remove("dragging");
    dragged = null;
    save();
  });

  // Escribir en el título de un separador guarda solo (sin recargar), y
  // mientras se escribe el arrastre se desactiva para poder seleccionar texto.
  body.addEventListener("input", function (e) {
    if (e.target.classList.contains("sep-input")) saveSoon();
  });
  body.addEventListener("mousedown", function (e) {
    if (!e.target.classList.contains("sep-input")) return;
    var row = e.target.closest("tr[data-type]");
    if (row) row.draggable = false;
  });
  document.addEventListener("mouseup", function () {
    rows().forEach(function (row) { row.draggable = true; });
  });
})();
