(() => {
    const form = document.getElementById("inv-movimiento");
    const operacion = form.dataset.operacion;
    const origen = document.getElementById(operacion === "transferencia" ? "id_anexo_origen" : "id_anexo");
    const destino = document.getElementById("id_anexo_destino");
    const articulo = document.getElementById("id_articulo");
    const cantidad = document.getElementById("id_cantidad");
    const tipo = document.getElementById("id_tipo_ajuste");
    const buscar = document.getElementById("inv-buscar");
    const resumen = document.getElementById("inv-saldo");
    const aviso = document.getElementById("inv-aviso");
    const resultados = document.getElementById("inv-resultados");
    const guardar = document.getElementById("inv-guardar");
    const articulos = JSON.parse(document.getElementById("inv-articulos").textContent);
    const stocks = JSON.parse(document.getElementById("inv-stocks").textContent);
    const saldos = new Map(stocks.map(item => [`${item.anexo_id}:${item.articulo_id}`, item.cantidad]));
    const saldo = (ubicacion, item) => saldos.get(`${ubicacion}:${item}`) || 0;
    const resta = () => operacion === "salida" || operacion === "transferencia" || (operacion === "ajuste" && tipo.value === "negativo");
    const normalizar = texto => texto.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es").trim();

    function actualizarResumen() {
        aviso.textContent = "";
        cantidad.removeAttribute("max");
        guardar.disabled = !origen.value || !articulo.value || (destino && !destino.value);
        if (!origen.value || !articulo.value) {
            resumen.textContent = "Seleccione ubicación y artículo para consultar el stock.";
            return;
        }
        const actual = saldo(origen.value, articulo.value);
        const unidades = Number(cantidad.value);
        const valido = cantidad.value !== "" && Number.isSafeInteger(unidades) && unidades > 0;
        if (resta()) cantidad.max = String(actual);
        resumen.textContent = `Stock actual: ${actual} unidades.`;
        if (valido) {
            const nuevo = actual + (resta() ? -unidades : unidades);
            resumen.textContent += ` ${resta() ? "Se descuentan" : "Se agregan"} ${unidades}. Saldo previsto: ${nuevo}.`;
        }
        if (operacion === "transferencia" && destino.value) {
            const actualDestino = saldo(destino.value, articulo.value);
            resumen.textContent += ` En destino: ${actualDestino}${valido ? ` → ${actualDestino + unidades}` : ""} unidades.`;
        }
        if (destino && destino.value === origen.value) {
            aviso.textContent = "El origen y el destino deben ser ubicaciones diferentes.";
        } else if (valido && resta() && unidades > actual) {
            aviso.textContent = `Stock insuficiente. Hay ${actual} unidades en la ubicación de origen.`;
        }
        guardar.disabled = guardar.disabled || !valido || Boolean(aviso.textContent);
    }

    function filtrarArticulos() {
        const anterior = articulo.value;
        const texto = normalizar(buscar.value);
        const opciones = articulos.filter(item =>
            normalizar(`${item.nombre} ${item.codigo || ""}`).includes(texto)
            && (!resta() || !origen.value || saldo(origen.value, item.id) > 0)
        );
        articulo.replaceChildren(new Option("Seleccione un artículo", ""));
        opciones.forEach(item => articulo.add(new Option(item.codigo ? `${item.nombre} (${item.codigo})` : item.nombre, String(item.id))));
        if (opciones.some(item => String(item.id) === anterior)) articulo.value = anterior;
        resultados.textContent = opciones.length
            ? `${opciones.length} artículo(s) encontrado(s).${resta() && origen.value ? " Solo se muestran artículos con stock en origen." : ""}`
            : "No hay artículos que coincidan. Revise la búsqueda o la ubicación de origen.";
        actualizarResumen();
    }
    buscar.addEventListener("input", filtrarArticulos);
    origen.addEventListener("change", filtrarArticulos);
    if (destino) destino.addEventListener("change", actualizarResumen);
    if (tipo) tipo.addEventListener("change", filtrarArticulos);
    articulo.addEventListener("change", actualizarResumen);
    cantidad.addEventListener("input", actualizarResumen);
    filtrarArticulos();
})();
