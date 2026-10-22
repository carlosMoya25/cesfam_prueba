(() => {
    const medicamento = document.getElementById("id_medicamento");
    const lote = document.getElementById("id_lote");
    const buscar = document.getElementById("buscar-medicamento");
    const ayuda = document.getElementById("lote-ayuda");
    const resultados = document.getElementById("medicamento-resultados");
    const guardar = document.getElementById("registrar-entrada");
    const destino = document.getElementById("id_anexo");
    const cantidad = document.getElementById("id_cantidad");
    const vistaPrevia = document.getElementById("farmacia-entrada-saldo");
    const stocks = JSON.parse(document.getElementById("stocks-entrada").textContent);
    const saldos = new Map(stocks.map(item => [`${item.lote_id}:${item.anexo_id}`, item.cantidad]));
    const unidades = JSON.parse(document.getElementById("unidades-medicamentos").textContent);
    const lotes = JSON.parse(document.getElementById("lotes-disponibles").textContent);
    const medicamentos = Array.from(medicamento.options)
        .filter(option => option.value)
        .map(option => ({ id: option.value, nombre: option.textContent }));
    const normalizar = texto => texto.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es").trim();

    function actualizarBoton() {
        const recibidas = Number(cantidad.value);
        const cantidadValida = cantidad.value !== "" && Number.isSafeInteger(recibidas) && recibidas > 0;
        guardar.disabled = !medicamento.value || !lote.value || !destino.value || !cantidadValida || !unidades[medicamento.value];
        if (medicamento.value && !unidades[medicamento.value]) {
            vistaPrevia.textContent = "Complete la unidad de stock en la ficha del medicamento antes de registrar la entrada.";
            return;
        }
        if (!medicamento.value || !lote.value || !destino.value) {
            vistaPrevia.textContent = "Seleccione medicamento, lote y ubicación para consultar las existencias.";
            return;
        }
        const actual = saldos.get(`${lote.value}:${destino.value}`) || 0;
        vistaPrevia.textContent = `Stock actual de este lote en destino: ${actual} ${unidades[medicamento.value] || "(unidad sin definir)"}.`;
        if (cantidadValida) vistaPrevia.textContent += ` Se reciben ${recibidas}. Saldo previsto: ${actual + recibidas}.`;
    }

    function cargarLotes(conservar = false) {
        const anterior = conservar ? lote.value : "";
        const disponibles = lotes.filter(item => String(item.medicamento_id) === medicamento.value);
        lote.replaceChildren();
        lote.required = disponibles.length > 0;
        if (!medicamento.value) {
            lote.add(new Option("Seleccione primero un medicamento", ""));
            ayuda.textContent = "Elija el medicamento para cargar sus lotes activos sin vencer.";
        } else if (disponibles.length === 0) {
            lote.add(new Option("Sin lotes disponibles", ""));
            ayuda.textContent = "Este medicamento no tiene lotes activos sin vencer. Registre el lote antes de ingresar existencias.";
        } else {
            if (disponibles.length > 1) lote.add(new Option("Seleccione el lote recibido", ""));
            disponibles.forEach(item => lote.add(new Option(item.numero_lote, String(item.id))));
            if (disponibles.length === 1) {
                lote.value = String(disponibles[0].id);
                ayuda.textContent = "Lote cargado automáticamente: es el único disponible para este medicamento.";
            } else {
                if (disponibles.some(item => String(item.id) === anterior)) lote.value = anterior;
                ayuda.textContent = "Hay varios lotes. Seleccione el que corresponde al medicamento recibido.";
            }
        }
        actualizarBoton();
    }

    buscar.addEventListener("input", () => {
        const anterior = medicamento.value;
        const coincidencias = medicamentos.filter(item => normalizar(item.nombre).includes(normalizar(buscar.value)));
        medicamento.replaceChildren(new Option("Seleccione un medicamento", ""));
        coincidencias.forEach(item => medicamento.add(new Option(item.nombre, item.id)));
        if (coincidencias.some(item => item.id === anterior)) medicamento.value = anterior;
        resultados.textContent = `${coincidencias.length} medicamento(s) encontrado(s). Seleccione uno en la lista.`;
        if (medicamento.value !== anterior) cargarLotes();
    });
    medicamento.addEventListener("change", () => cargarLotes());
    lote.addEventListener("change", actualizarBoton);
    destino.addEventListener("change", actualizarBoton);
    cantidad.addEventListener("input", actualizarBoton);
    resultados.textContent = "Puede buscar por nombre o elegir directamente un medicamento de la lista.";
    cargarLotes(true);
})();
