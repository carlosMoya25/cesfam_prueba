(() => {
    const menus = Array.from(document.querySelectorAll('.inventory-dropdown'));
    function cerrarMenus(excepto = null) {
        menus.forEach(menu => { if (menu !== excepto) menu.open = false; });
    }
    menus.forEach(menu => {
        menu.addEventListener('toggle', () => {
            if (!menu.open) return;
            cerrarMenus(menu);
            if (!menu.classList.contains('inventory-row-dropdown')) return;
            const ancla = menu.querySelector('summary').getBoundingClientRect();
            const opciones = menu.querySelector('.inventory-dropdown-menu');
            const ancho = opciones.offsetWidth;
            const alto = opciones.offsetHeight;
            const cabeAbajo = ancla.bottom + alto + 12 < window.innerHeight;
            opciones.style.top = `${Math.max(8, cabeAbajo ? ancla.bottom + 4 : ancla.top - alto - 4)}px`;
            opciones.style.left = `${Math.max(8, Math.min(ancla.right - ancho, window.innerWidth - ancho - 8))}px`;
            opciones.style.maxHeight = `${window.innerHeight - 16}px`;
            opciones.style.overflowY = 'auto';
        });
    });
    document.addEventListener('click', event => {
        if (!event.target.closest('.inventory-dropdown')) cerrarMenus();
    });
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        const activo = menus.find(menu => menu.open);
        if (activo) { activo.open = false; activo.querySelector('summary').focus(); }
    });
    window.addEventListener('resize', () => cerrarMenus());
    document.querySelectorAll('.inventory-table-scroll').forEach(tabla => {
        tabla.addEventListener('scroll', () => cerrarMenus());
    });
    window.addEventListener('scroll', () => cerrarMenus(), {passive: true});
})();
