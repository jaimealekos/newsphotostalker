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

  // Filas que se arrastran y se guardan: los grupos y las búsquedas. Las filas
  // "empty" (el «arrastra búsquedas hasta aquí» de un grupo vacío) NO entran
  // aquí: son solo una diana donde soltar, no una fila del panel.
  function rows() {
    return Array.prototype.slice.call(
      body.querySelectorAll('tr[data-type="group"], tr[data-type="search"]')
    );
  }

  function say(text, kind) {
    if (!status) return;
    status.textContent = text;
    status.className = "order-hint small " + (kind === "error" ? "order-error" : "muted");
  }

  // Un grupo vacío enseña su diana; en cuanto tiene algo dentro, sobra. Se
  // recalcula tras cada suelta para que el hueco no se quede ahí mintiendo.
  function refreshEmpties() {
    var all = Array.prototype.slice.call(body.querySelectorAll("tr[data-type]"));
    var lleno = false;
    var pendiente = null;
    all.forEach(function (row) {
      var tipo = row.dataset.type;
      if (tipo === "group") {
        if (pendiente) pendiente.hidden = lleno;
        pendiente = null;
        lleno = false;
      } else if (tipo === "search") {
        lleno = true;
      } else if (tipo === "empty") {
        pendiente = row;
      }
    });
    if (pendiente) pendiente.hidden = lleno;
  }

  // El orden, el grupo de cada búsqueda y los nombres de los grupos se guardan
  // de una vez: el grupo NO viaja como un campo suyo, sale de la posición en la
  // lista (pertenece al último grupo que queda por encima). Así arrastrar entre
  // bloques es todo el gesto que hace falta.
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
        say("Guardado ✓");
      })
      .catch(function () {
        say("No se pudo guardar", "error");
      });
  }

  function saveSoon() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 400);
  }

  body.addEventListener("dragstart", function (e) {
    var row = e.target.closest('tr[data-type="group"], tr[data-type="search"]');
    if (!row) return;
    dragged = row;
    row.classList.add("dragging");
    // Arrastrar un grupo se lleva sus búsquedas: mover la carpeta y dejar el
    // contenido donde estaba no es lo que espera nadie.
    if (row.dataset.type === "group") {
      dragged.bloque = [];
      var next = row.nextElementSibling;
      while (next && next.dataset.type !== "group") {
        dragged.bloque.push(next);
        next = next.nextElementSibling;
      }
    }
    e.dataTransfer.effectAllowed = "move";
    // Firefox no arranca el arrastre sin datos en el portapapeles del evento.
    e.dataTransfer.setData("text/plain", row.dataset.id);
  });

  body.addEventListener("dragover", function (e) {
    if (!dragged) return;
    e.preventDefault();
    var over = e.target.closest("tr[data-type]");
    if (!over || over === dragged) return;
    if (dragged.bloque && dragged.bloque.indexOf(over) !== -1) return;

    // Mitad de arriba: la fila arrastrada va antes; mitad de abajo, después.
    var box = over.getBoundingClientRect();
    var after = e.clientY > box.top + box.height / 2;
    var ref = after ? over.nextSibling : over;

    // Una búsqueda no puede quedar por encima del primer grupo: en el panel no
    // hay sitio fuera de un grupo. Se deja caer justo debajo de esa cabecera.
    if (dragged.dataset.type === "search") {
      var primero = body.querySelector('tr[data-type="group"]');
      if (primero && ref === primero) ref = primero.nextSibling;
      if (!after && over === primero) ref = primero.nextSibling;
    }

    body.insertBefore(dragged, ref);
    // El grupo arrastra su bloque detrás, en el mismo orden.
    if (dragged.bloque) {
      var ancla = dragged;
      dragged.bloque.forEach(function (fila) {
        ancla.parentNode.insertBefore(fila, ancla.nextSibling);
        ancla = fila;
      });
    }
    refreshEmpties();
  });

  body.addEventListener("drop", function (e) { e.preventDefault(); });

  body.addEventListener("dragend", function () {
    if (!dragged) return;
    dragged.classList.remove("dragging");
    delete dragged.bloque;
    dragged = null;
    refreshEmpties();
    save();
  });

  // Escribir el nombre de un grupo guarda solo (sin recargar), y mientras se
  // escribe el arrastre se desactiva para poder seleccionar texto.
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

  refreshEmpties();
})();
