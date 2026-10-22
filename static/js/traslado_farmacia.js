(() => {
    const lote = document.getElementById("id_lote");
    const cantidad = document.getElementById("id_cantidad");
    const destino = document.getElementById("id_destino");
    const buscar = document.getElementById("buscar-lote-traslado");
    const lista = document.getElementById("sugerencias-traslado");
    const resultados = document.getElementById("resultados-traslado");
    const saldo = document.getElementById("saldo-traslado");
    const guardar = document.getElementById("confirmar-traslado");
    const bodega = document.getElementById("stock-bodega-traslado");
    const anexo = document.getElementById("stock-anexo-traslado");
    const bodegaDespues = document.getElementById("stock-bodega-despues");
    const anexoDespues = document.getElementById("stock-anexo-despues");
    const stocks = new Map(JSON.parse(document.getElementById("stocks-traslado").textContent).map(item => [String(item.lote_id), item]));
    const anexos = new Map(JSON.parse(document.getElementById("stocks-anexos-traslado").textContent).map(item => [`${item.lote_id}:${item.anexo_id}`, item.cantidad]));
    const opciones = Array.from(lote.options).filter(opcion => opcion.value && stocks.get(opcion.value)?.cantidad > 0);
    const normalizar = texto => texto.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es").trim();
    const numero = valor => valor.toLocaleString("es-CL");
    let coincidencias = [];
    let activa = -1;

    function cerrar() {
        lista.hidden = true;
        buscar.setAttribute("aria-expanded", "false");
        buscar.removeAttribute("aria-activedescendant");
        activa = -1;
    }

    function activar(indice, desplazar = false) {
        activa = indice;
        Array.from(lista.children).forEach((item, posicion) => item.setAttribute("aria-selected", String(posicion === indice)));
        const item = lista.children[indice];
        if (item) {
            buscar.setAttribute("aria-activedescendant", item.id);
            if (desplazar) item.scrollIntoView({ block: "nearest" });
        }
    }

    function textoSeleccionado(id) {
        const stock = stocks.get(id);
        return stock ? `${stock.lote__medicamento__articulo__nombre} ${stock.lote__medicamento__concentracion} · Lote ${stock.lote__numero_lote}` : "";
    }

    function seleccionar(id) {
        lote.value = id;
        buscar.value = textoSeleccionado(id);
        resultados.textContent = "Medicamento y lote seleccionados. Revise las existencias antes de confirmar.";
        cerrar();
        actualizar();
    }

    function sugerir() {
        const palabras = normalizar(buscar.value).split(/\s+/).filter(Boolean);
        const todas = opciones.filter(opcion => palabras.every(palabra => normalizar(opcion.textContent).includes(palabra)));
        coincidencias = todas.slice(0, 12);
        lista.replaceChildren();
        activa = -1;
        buscar.removeAttribute("aria-activedescendant");
        coincidencias.forEach((opcion, indice) => {
            const stock = stocks.get(opcion.value);
            const item = document.createElement("li");
            item.id = `sugerencia-traslado-${indice}`;
            item.setAttribute("role", "option");
            item.setAttribute("aria-selected", "false");
            const nombre = document.createElement("strong");
            nombre.textContent = `${stock.lote__medicamento__articulo__nombre} ${stock.lote__medicamento__concentracion}`;
            const existencias = document.createElement("span");
            existencias.textContent = `Disponible: ${numero(stock.cantidad)} ${stock.lote__medicamento__unidad_medida || '(unidad sin definir)'}`;
            const detalle = document.createElement("small");
            const fecha = stock.lote__fecha_vencimiento.split("-").reverse().join("/");
            detalle.textContent = `${stock.lote__medicamento__articulo__codigo || 'Sin código'} · Lote ${stock.lote__numero_lote} · Vence ${fecha}`;
            item.append(nombre, existencias, detalle);
            item.addEventListener("pointerdown", evento => evento.preventDefault());
            item.addEventListener("pointermove", () => activar(indice));
            item.addEventListener("click", () => seleccionar(opcion.value));
            lista.append(item);
        });
        lista.hidden = coincidencias.length === 0;
        buscar.setAttribute("aria-expanded", String(coincidencias.length > 0));
        resultados.textContent = todas.length
            ? `${todas.length} coincidencias.${todas.length > 12 ? ' Mostrando las primeras 12; escriba más para precisar la búsqueda.' : ' Use las flechas y Enter o seleccione una sugerencia.'}`
            : "No hay coincidencias con stock disponible en Bodega Central.";
    }

    function actualizar() {
        [bodega, anexo, bodegaDespues, anexoDespues].forEach(elemento => { elemento.textContent = "—"; });
        guardar.disabled = true;
        const stock = stocks.get(lote.value);
        if (!stock) {
            cantidad.max = "2147483647";
            saldo.textContent = "Seleccione una sugerencia o elija medicamento y lote para consultar sus existencias.";
            return;
        }
        const unidad = stock.lote__medicamento__unidad_medida;
        const formato = valor => `${numero(valor)}${unidad ? ` ${unidad}` : ' (unidad sin definir)'}`;
        const disponible = stock.cantidad;
        const saldoDestino = destino.value ? (anexos.get(`${lote.value}:${destino.value}`) || 0) : null;
        bodega.textContent = formato(disponible);
        if (saldoDestino !== null) anexo.textContent = formato(saldoDestino);
        cantidad.max = String(disponible);
        if (!unidad) {
            saldo.textContent = "Complete y verifique la unidad de stock en la ficha del medicamento antes de trasladar estas existencias.";
            return;
        }
        const enviadas = Number(cantidad.value);
        if (!cantidad.value || !Number.isSafeInteger(enviadas) || enviadas <= 0) {
            saldo.textContent = "Indique una cantidad entera positiva y el anexo de destino para ver los saldos finales.";
            return;
        }
        if (enviadas > disponible) {
            saldo.textContent = "La cantidad supera las existencias disponibles en Bodega Central.";
            return;
        }
        bodegaDespues.textContent = formato(disponible - enviadas);
        if (saldoDestino === null) {
            saldo.textContent = "Seleccione el anexo de destino para ver su stock y el saldo después del traslado.";
            return;
        }
        if (saldoDestino + enviadas > 2147483647) {
            saldo.textContent = "El traslado superaría el máximo de existencias permitido en el anexo.";
            return;
        }
        anexoDespues.textContent = formato(saldoDestino + enviadas);
        saldo.textContent = "Revise el medicamento, lote, destino, cantidad y documento antes de confirmar el traslado.";
        guardar.disabled = false;
    }

    buscar.addEventListener("input", () => {
        // Una búsqueda nueva no debe conservar silenciosamente otro lote seleccionado.
        if (lote.value) cantidad.value = "";
        lote.value = "";
        actualizar();
        sugerir();
    });
    buscar.addEventListener("focus", () => { if (!lote.value) sugerir(); });
    buscar.addEventListener("blur", cerrar);
    buscar.addEventListener("keydown", evento => {
        if (evento.key === "Escape") {
            cerrar();
        } else if (evento.key === "ArrowDown" || evento.key === "ArrowUp") {
            evento.preventDefault();
            if (lista.hidden) sugerir();
            if (coincidencias.length) {
                const siguiente = evento.key === "ArrowDown" ? (activa + 1) % coincidencias.length
                    : (activa <= 0 ? coincidencias.length - 1 : activa - 1);
                activar(siguiente, true);
            }
        } else if (evento.key === "Enter" && !lista.hidden) {
            evento.preventDefault();
            const opcion = coincidencias[activa < 0 ? 0 : activa];
            if (opcion) seleccionar(opcion.value);
        } else if (evento.key === "Tab") {
            cerrar();
        }
    });
    lote.addEventListener("change", () => seleccionar(lote.value));
    cantidad.addEventListener("input", actualizar);
    destino.addEventListener("change", actualizar);
    if (lote.value) buscar.value = textoSeleccionado(lote.value);
    actualizar();
})();
