const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Superficie DOM mínima para ejercitar los eventos del código real sin dependencias de navegador.
class Element {
    constructor() { this.children = []; this.attrs = {}; this.events = {}; this.value = ''; this.hidden = false; this._text = ''; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
    set innerHTML(value) { throw Error('Los datos de medicamentos deben escribirse como texto'); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; this._text = ''; }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    removeAttribute(name) { delete this.attrs[name]; }
    addEventListener(name, callback) { (this.events[name] ||= []).push(callback); }
    dispatch(name, extra = {}) {
        const event = { preventDefault() { this.defaultPrevented = true; }, ...extra };
        for (const callback of this.events[name] || []) callback(event);
        return event;
    }
    scrollIntoView() {}
}

function pantalla() {
    const ids = ['id_lote', 'id_cantidad', 'id_destino', 'buscar-lote-traslado', 'sugerencias-traslado',
        'resultados-traslado', 'saldo-traslado', 'confirmar-traslado', 'stock-bodega-traslado',
        'stock-anexo-traslado', 'stock-bodega-despues', 'stock-anexo-despues', 'stocks-traslado', 'stocks-anexos-traslado'];
    const elementos = Object.fromEntries(ids.map(id => [id, new Element()]));
    const lote = (id, nombre, cantidad, unidad, numero) => ({
        lote_id: id, cantidad, lote__medicamento__unidad_medida: unidad, lote__numero_lote: numero,
        lote__fecha_vencimiento: '2029-02-15', lote__medicamento__concentracion: '5 mg',
        lote__medicamento__articulo__nombre: nombre, lote__medicamento__articulo__codigo: `MED-${id}`,
    });
    const datos = [lote(1, 'Ácido fólico', 40, 'comprimido', 'A-1'), lote(2, 'Ácido fólico', 10, 'comprimido', 'B-2'),
        lote(3, 'Paracetamol <img src=x>', 3500, '', 'C-3')];
    elementos['stocks-traslado'].textContent = JSON.stringify(datos);
    elementos['stocks-anexos-traslado'].textContent = JSON.stringify([{ lote_id: 1, anexo_id: 9, cantidad: 8 }]);
    elementos['sugerencias-traslado'].hidden = true;
    const vacia = new Element();
    elementos.id_lote.options = [vacia, ...datos.map(dato => {
        const opcion = new Element();
        opcion.value = String(dato.lote_id);
        opcion.textContent = `${dato.lote__medicamento__articulo__nombre} MED-${dato.lote_id} 5 mg Lote ${dato.lote__numero_lote} Stock ${dato.cantidad}`;
        return opcion;
    })];
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/traslado_farmacia.js'), 'utf8'), {
        document: { getElementById: id => elementos[id], createElement: () => new Element() },
    });
    return elementos;
}

function escribir(elemento, texto) { elemento.value = texto; elemento.dispatch('input'); }

test('Sugerencias sin distinguir acentos, selección y stock de origen y destino', () => {
    const e = pantalla();
    escribir(e['buscar-lote-traslado'], 'acido');
    assert.equal(e['sugerencias-traslado'].children.length, 2);
    assert.match(e['sugerencias-traslado'].children[0].textContent, /Disponible: 40 comprimido/);
    assert.match(e['sugerencias-traslado'].children[0].textContent, /Vence 15\/02\/2029/);
    e['sugerencias-traslado'].children[0].dispatch('click');
    assert.equal(e.id_lote.value, '1');
    assert.equal(e['stock-bodega-traslado'].textContent, '40 comprimido');
    e.id_destino.value = '9'; e.id_destino.dispatch('change');
    escribir(e.id_cantidad, '12');
    assert.equal(e['stock-anexo-traslado'].textContent, '8 comprimido');
    assert.equal(e['stock-bodega-despues'].textContent, '28 comprimido');
    assert.equal(e['stock-anexo-despues'].textContent, '20 comprimido');
    assert.equal(e['confirmar-traslado'].disabled, false);
    e.id_destino.value = '10'; e.id_destino.dispatch('change');
    assert.equal(e['stock-anexo-traslado'].textContent, '0 comprimido');
    assert.equal(e['stock-anexo-despues'].textContent, '12 comprimido');
});

test('Las flechas y Enter eligen el lote correcto y Escape cierra las sugerencias', () => {
    const e = pantalla();
    escribir(e['buscar-lote-traslado'], 'acido');
    e['buscar-lote-traslado'].dispatch('keydown', { key: 'ArrowDown' });
    e['buscar-lote-traslado'].dispatch('keydown', { key: 'ArrowDown' });
    e['buscar-lote-traslado'].dispatch('keydown', { key: 'Enter' });
    assert.equal(e.id_lote.value, '2');
    assert.equal(e['stock-bodega-traslado'].textContent, '10 comprimido');
    escribir(e['buscar-lote-traslado'], 'acido');
    e['buscar-lote-traslado'].dispatch('keydown', { key: 'Escape' });
    assert.equal(e['sugerencias-traslado'].hidden, true);
    assert.equal(e['buscar-lote-traslado'].attrs['aria-expanded'], 'false');
});

test('Una nueva búsqueda elimina la selección anterior y avisa cuando no hay coincidencias', () => {
    const e = pantalla();
    e.id_lote.value = '1'; e.id_lote.dispatch('change');
    escribir(e.id_cantidad, '8');
    escribir(e['buscar-lote-traslado'], 'No existe');
    assert.equal(e.id_lote.value, '');
    assert.equal(e.id_cantidad.value, '');
    assert.equal(e['stock-bodega-traslado'].textContent, '—');
    assert.equal(e['confirmar-traslado'].disabled, true);
    assert.match(e['resultados-traslado'].textContent, /No hay coincidencias/);
});

test('Cantidades excesivas y unidades sin definir impiden confirmar y el contenido se muestra como texto', () => {
    const e = pantalla();
    e.id_lote.value = '1'; e.id_lote.dispatch('change');
    e.id_destino.value = '9'; e.id_destino.dispatch('change');
    escribir(e.id_cantidad, '41');
    assert.equal(e['confirmar-traslado'].disabled, true);
    assert.match(e['saldo-traslado'].textContent, /supera las existencias/);
    escribir(e['buscar-lote-traslado'], 'paracetamol');
    assert.match(e['sugerencias-traslado'].children[0].textContent, /<img src=x>/);
    e['sugerencias-traslado'].children[0].dispatch('click');
    escribir(e.id_cantidad, '1');
    assert.equal(e['confirmar-traslado'].disabled, true);
    assert.match(e['saldo-traslado'].textContent, /verifique la unidad/);
    assert.match(e['stock-bodega-traslado'].textContent, /unidad sin definir/);
});
