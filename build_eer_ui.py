"""
Generador del visor UI EER para el caso LeBaron.

Produce:
  - output/eer/visor.html     Visor autocontenido con tablas y exports
  - output/eer/data_eer.json  Datos del EER para el visor

El visor muestra:
  - Diagrama EER (tablas como cajas con FK visual)
  - Click en tabla -> ver detalle + export
  - Vista detalle: registros paginados, busqueda
  - Boton "Exportar" -> descarga JSON/CSV de la tabla
"""
import os
import sys
import json
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_eer import EER_DIR, EER_SCHEMA, poblar_desde_grafo, cargar_trazabilidad_y_hallazgos, construir_grafo
from pseudonymizer import cargar_tabla
from config import ALIAS_FILE


# ────────────────────────────────────────────────────────────────────────
# HTML template del visor
# ────────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Visor EER - Caso LeBaron</title>
<style>
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; height:100%; overflow:hidden;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f172a; color: #e2e8f0; display:flex; flex-direction:column; }

  header { padding:12px 20px; background:#1e293b; border-bottom:1px solid #334155;
    flex-shrink:0; display:flex; justify-content:space-between; align-items:center; }
  header h1 { font-size:18px; margin:0; color:#38bdf8; font-weight:600; }
  header .meta { font-size:12px; color:#94a3b8; }

  .container { flex:1; min-height:0; overflow:hidden; display:flex; }

  /* sidebar con lista de tablas */
  aside { width:280px; background:#1e293b; border-right:1px solid #334155;
    overflow-y:auto; flex-shrink:0; padding:12px; }
  aside h2 { margin:0 0 8px; font-size:13px; color:#94a3b8; font-weight:600;
    text-transform:uppercase; letter-spacing:0.5px; }

  .tabla-btn { display:flex; justify-content:space-between; align-items:center;
    padding:8px 10px; background:#0f172a; border:1px solid #334155; border-radius:6px;
    margin-bottom:4px; cursor:pointer; transition:all 0.15s; font-size:13px; }
  .tabla-btn:hover { border-color:#38bdf8; background:#082f49; }
  .tabla-btn.activa { background:#0c4a6e; border-color:#0ea5e9; }
  .tabla-btn .nombre { font-weight:500; }
  .tabla-btn .count { font-size:11px; color:#64748b; padding:2px 8px;
    background:#0f172a; border-radius:10px; }
  .tabla-btn.entidad { border-left:3px solid #3b82f6; }
  .tabla-btn.relacion { border-left:3px solid #a78bfa; }

  .search-box { width:100%; padding:8px 10px; background:#0f172a;
    border:1px solid #334155; border-radius:6px; color:#e2e8f0;
    font-size:12px; margin-bottom:12px; }

  /* panel principal */
  main { flex:1; min-width:0; overflow:hidden; display:flex; flex-direction:column; }

  .toolbar { padding:10px 16px; background:#0f172a; border-bottom:1px solid #334155;
    display:flex; gap:8px; align-items:center; flex-shrink:0; }
  .toolbar .title { font-size:15px; font-weight:600; color:#e2e8f0; flex:1; }
  .toolbar button { padding:6px 14px; background:#1e293b; border:1px solid #334155;
    color:#e2e8f0; border-radius:6px; cursor:pointer; font-size:12px; }
  .toolbar button:hover { border-color:#38bdf8; }
  .toolbar button.primary { background:#0c4a6e; border-color:#0ea5e9; }

  .content { flex:1; overflow:auto; padding:16px; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th { background:#1e293b; color:#94a3b8; font-weight:600; text-align:left;
    padding:8px 10px; border-bottom:2px solid #475569; position:sticky; top:0; }
  td { padding:7px 10px; border-bottom:1px solid #1e293b; }
  tr:nth-child(even) td { background:#0f172a; }
  tr:hover td { background:#082f49; }
  td.id { font-family:ui-monospace,monospace; color:#38bdf8; font-size:11px; }
  td.display-name { color:#e2e8f0; font-size:11px;
    max-width:260px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  td.null { color:#475569; font-style:italic; }
  td.json-val { font-family:ui-monospace,monospace; font-size:10px;
    max-width:300px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  td.trazabilidad { color:#7dd3fc; font-size:10.5px; font-family:ui-monospace,monospace;
    max-width:340px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  td.trazabilidad a.pdf-link, .campo-valor a.pdf-link { color:#38bdf8; text-decoration:none; }
  td.trazabilidad a.pdf-link:hover, .campo-valor a.pdf-link:hover { text-decoration:underline; color:#7dd3fc; }

  /* Diagrama EER */
  .eer-diagram { display:flex; flex-wrap:wrap; gap:14px; padding:8px; }
  .eer-tabla { background:#1e293b; border:2px solid #334155; border-radius:8px;
    padding:0; min-width:240px; font-size:12px; }
  .eer-tabla.entidad { border-color:#3b82f6; }
  .eer-tabla.relacion { border-color:#a78bfa; background:#1a1f3a; }
  .eer-tabla .nombre-tabla { padding:8px 12px; background:#334155; font-weight:600;
    color:#e2e8f0; border-radius:6px 6px 0 0; font-size:13px; }
  .eer-tabla.entidad .nombre-tabla { background:#1e3a8a; }
  .eer-tabla.relacion .nombre-tabla { background:#4c1d95; }
  .eer-tabla table { width:100%; }
  .eer-tabla th { background:transparent; color:#94a3b8; padding:4px 12px;
    border-bottom:1px solid #475569; font-size:11px; }
  .eer-tabla td { padding:4px 12px; font-size:11px; border-bottom:1px solid #1f2937; }
  .eer-tabla .pk { color:#fbbf24; font-weight:600; }
  .eer-tabla .fk { color:#a78bfa; }
  .eer-tabla .action { padding:8px; text-align:center; }
  .eer-tabla .action button { padding:4px 12px; background:#0ea5e9;
    color:#0f172a; border:none; border-radius:4px; cursor:pointer; font-weight:600; }

  /* Stats panel */
  .stats { padding:8px 14px; background:#1e293b; border-radius:6px; margin-bottom:12px;
    font-size:11px; color:#cbd5e1; line-height:1.5; }
  .stats b { color:#38bdf8; }

  /* pagination */
  .pagination { display:flex; gap:4px; align-items:center; padding:10px 0; }
  .pagination button { padding:4px 10px; background:#1e293b; border:1px solid #334155;
    color:#e2e8f0; border-radius:4px; cursor:pointer; }
  .pagination button.active { background:#0ea5e9; color:#0f172a; }
  .pagination button:disabled { opacity:0.4; cursor:not-allowed; }

  .empty { padding:40px; text-align:center; color:#64748b; font-style:italic; }

  /* Selector de columnas */
  .columnas-menu { position:absolute; top:calc(100% + 4px); right:0; z-index:50;
    background:#1e293b; border:1px solid #334155; border-radius:8px; padding:10px;
    min-width:220px; max-height:340px; overflow-y:auto; box-shadow:0 8px 24px rgba(0,0,0,0.4); }
  .columnas-menu .col-item { display:flex; align-items:center; gap:8px; padding:5px 4px;
    font-size:12px; cursor:pointer; border-radius:4px; }
  .columnas-menu .col-item:hover { background:#0f172a; }
  .columnas-menu .col-actions { display:flex; gap:6px; margin-bottom:8px;
    padding-bottom:8px; border-bottom:1px solid #334155; }
  .columnas-menu .col-actions button { flex:1; padding:4px 8px; font-size:11px;
    background:#0f172a; border:1px solid #334155; color:#e2e8f0; border-radius:4px; cursor:pointer; }

  /* Fila clickeable */
  tbody tr { cursor:pointer; }

  /* Modal de detalle */
  .modal-overlay { position:fixed; inset:0; background:rgba(0,0,0,0.6);
    display:flex; align-items:center; justify-content:center; z-index:100; }
  .modal-box { background:#1e293b; border:1px solid #334155; border-radius:10px;
    width:min(720px, 92vw); max-height:82vh; display:flex; flex-direction:column;
    box-shadow:0 20px 60px rgba(0,0,0,0.6); }
  .modal-header { padding:14px 18px; border-bottom:1px solid #334155;
    display:flex; justify-content:space-between; align-items:center;
    font-size:15px; font-weight:600; color:#38bdf8; flex-shrink:0; }
  .modal-close { background:none; border:none; color:#94a3b8; font-size:16px;
    cursor:pointer; padding:4px 8px; border-radius:4px; }
  .modal-close:hover { background:#0f172a; color:#e2e8f0; }
  .modal-body { padding:16px 18px; overflow-y:auto; }
  .modal-field { margin-bottom:12px; }
  .modal-field .campo-nombre { font-size:11px; color:#64748b; text-transform:uppercase;
    letter-spacing:0.5px; margin-bottom:3px; font-weight:600; }
  .modal-field .campo-valor { font-size:13px; color:#e2e8f0; line-height:1.5;
    word-break:break-word; background:#0f172a; padding:8px 10px; border-radius:6px;
    border:1px solid #1f2937; }
  .modal-field .campo-valor.mono { font-family:ui-monospace,monospace; font-size:11.5px; }
  .modal-field .campo-valor.vacio { color:#475569; font-style:italic; }
  .modal-field .campo-valor a.rel-link { color:#7dd3fc; text-decoration:none; cursor:pointer; }
  .modal-field .campo-valor a.rel-link:hover { text-decoration:underline; }
</style>
</head>
<body>
<header>
  <h1>🗂 Visor EER — Caso LeBaron</h1>
  <div class="meta">
    <span id="meta-timestamp"></span> |
    <span id="meta-total"></span>
  </div>
</header>

<div class="container">
  <aside>
    <h2>Entidades</h2>
    <div id="lista-entidades"></div>
    <h2 style="margin-top:14px">Relaciones</h2>
    <div id="lista-relaciones"></div>
    <h2 style="margin-top:14px">Vista</h2>
    <button class="tabla-btn" style="width:100%;background:#082f49"
      onclick="mostrarDiagrama()">📊 Diagrama EER completo</button>
    <button class="tabla-btn" style="width:100%;background:#082f49;margin-top:4px"
      onclick="exportarTodo()">💾 Exportar TODO (ZIP)</button>
  </aside>

  <main>
    <div class="toolbar">
      <div class="title" id="titulo-tabla">Selecciona una tabla del panel izquierdo</div>
      <input type="text" id="buscar" placeholder="Filtrar filas..."
        style="padding:6px 10px;background:#0f172a;border:1px solid #334155;
        color:#e2e8f0;border-radius:6px;font-size:12px;width:200px">
      <div style="position:relative">
        <button onclick="toggleColumnasMenu()">☰ Columnas</button>
        <div id="columnas-menu" class="columnas-menu" style="display:none"></div>
      </div>
      <button onclick="exportarJSON()">📥 JSON</button>
      <button onclick="exportarCSV()">📥 CSV</button>
      <button class="primary" onclick="exportarSQL()">📥 SQL backup</button>
    </div>
    <div class="content" id="content-area">
      <div class="empty">👈 Selecciona una tabla para ver sus datos.</div>
    </div>
  </main>
</div>

<!-- Modal de detalle de fila -->
<div id="modal-overlay" class="modal-overlay" style="display:none" onclick="cerrarModalSiFondo(event)">
  <div class="modal-box">
    <div class="modal-header">
      <span id="modal-titulo">Detalle</span>
      <button class="modal-close" onclick="cerrarModal()">✕</button>
    </div>
    <div class="modal-body" id="modal-body"></div>
  </div>
</div>

<script>
const EER_DATA = __DATA__;

let tablaActiva = null;
let filasFiltradas = null;
let paginaActual = 1;
const TAMANO_PAGINA = 50;
let columnasOcultasPorTabla = {};   // { tabla: Set(columnas ocultas) }
let colsActuales = [];              // columnas de la tabla activa (para el menu)

// poblar sidebar
function poblarSidebar() {
  const ents = Object.entries(EER_DATA.tablas).filter(([k,v]) => !k.startsWith('rel_'));
  const rels = Object.entries(EER_DATA.tablas).filter(([k,v]) => k.startsWith('rel_'));

  const elE = document.getElementById('lista-entidades');
  elE.innerHTML = '';
  for (const [k, info] of ents) {
    const btn = document.createElement('div');
    btn.className = 'tabla-btn entidad';
    btn.innerHTML = `<span class="nombre">${k}</span><span class="count">${info.filas}</span>`;
    btn.onclick = () => cargarTabla(k);
    elE.appendChild(btn);
  }

  const elR = document.getElementById('lista-relaciones');
  elR.innerHTML = '';
  for (const [k, info] of rels) {
    const btn = document.createElement('div');
    btn.className = 'tabla-btn relacion';
    btn.innerHTML = `<span class="nombre">${k}</span><span class="count">${info.filas}</span>`;
    btn.onclick = () => cargarTabla(k);
    elR.appendChild(btn);
  }
}

// cargar tabla y mostrar sus filas
async function cargarTabla(tabla) {
  tablaActiva = tabla;
  paginaActual = 1;
  filasFiltradas = null;
  document.querySelectorAll('.tabla-btn').forEach(b => b.classList.remove('activa'));
  event && event.target && event.target.closest('.tabla-btn') && event.target.closest('.tabla-btn').classList.add('activa');

  const data = await fetch('tablas/' + tabla + '.json').then(r => r.json());
  EER_DATA.datos[tabla] = data;

  const info = EER_DATA.tablas[tabla];
  document.getElementById('titulo-tabla').textContent =
    `${tabla} — ${data.length} filas`;

  renderTabla();
}

// Convertir nombre de archivo a link de Google Drive si existe en el mapeo
function archivoLink(archivo) {
  const pdfMap = EER_DATA.pdf_mapping || {};
  // Buscar coincidencia exacta o por sufijo
  for (const [pdfName, url] of Object.entries(pdfMap)) {
    if (archivo === pdfName || archivo.endsWith(pdfName.replace('.pdf', ''))) {
      return `<a href="${url}" target="_blank" class="pdf-link" title="Abrir PDF en Google Drive">${archivo}</a>`;
    }
  }
  return archivo;
}

// Formatea archivos/chunks de trazabilidad de forma legible:
//   chunks:     [["FED-SEIDO...T01.txt", 33], ...] -> "FED-SEIDO...T01 - 33, ..."
//   archivos:   ["FED-SEIDO...T01.txt", ...] -> "FED-SEIDO...T01, ..."
function formatearTrazabilidad(v, tipoCol) {
  if (!Array.isArray(v) || v.length === 0) return '';
  const limpiarNombre = (nombre) => String(nombre).replace(/[.]txt$/i, '');
  if (tipoCol === 'chunks') {
    return v.map(item => {
      if (Array.isArray(item) && item.length >= 2) {
        const nombreLimpio = limpiarNombre(item[0]);
        const link = archivoLink(nombreLimpio);
        return `${link} - ${item[1]}`;
      }
      return String(item);
    }).join(', ');
  }
  // archivos / expedientes: lista simple de nombres con links
  return v.map(a => archivoLink(limpiarNombre(a))).join(', ');
}

// Formatea CUALQUIER valor array/objeto sin dejar corchetes, comillas
// ni escapes de JSON crudo visibles. Uso general para columnas que no
// son chunks/archivos/expedientes (ej: fuentes, aliases_origen, cargos).
function formatearValorGenerico(v) {
  if (v === null || v === undefined) return '';
  if (Array.isArray(v)) {
    if (v.length === 0) return '';
    return v.map(item => {
      if (Array.isArray(item)) return formatearValorGenerico(item).replace(/, /g, ' - ');
      if (item !== null && typeof item === 'object') return formatearValorGenerico(item);
      return String(item);
    }).join(', ');
  }
  if (typeof v === 'object') {
    const entradas = Object.entries(v);
    if (entradas.length === 0) return '';
    return entradas.map(([k, val]) => `${k}: ${formatearValorGenerico(val)}`).join(' · ');
  }
  return String(v);
}

// ─── Selector de columnas ───────────────────────────────────────────────────
function toggleColumnasMenu() {
  const menu = document.getElementById('columnas-menu');
  const abierto = menu.style.display !== 'none';
  if (abierto) {
    menu.style.display = 'none';
  } else {
    renderColumnasMenu();
    menu.style.display = 'block';
  }
}

function renderColumnasMenu() {
  const menu = document.getElementById('columnas-menu');
  if (!tablaActiva || colsActuales.length === 0) {
    menu.innerHTML = '<div style="font-size:12px;color:#64748b;padding:6px">Selecciona una tabla primero</div>';
    return;
  }
  const ocultas = columnasOcultasPorTabla[tablaActiva] || new Set();
  let html = '<div class="col-actions">';
  html += '<button onclick="marcarTodasColumnas(true)">Mostrar todas</button>';
  html += '<button onclick="marcarTodasColumnas(false)">Ocultar todas</button>';
  html += '</div>';
  for (const c of colsActuales) {
    const checked = !ocultas.has(c) ? 'checked' : '';
    html += `<label class="col-item"><input type="checkbox" ${checked} onchange="toggleColumna('${c}')"> ${c}</label>`;
  }
  menu.innerHTML = html;
}

function toggleColumna(col) {
  if (!columnasOcultasPorTabla[tablaActiva]) columnasOcultasPorTabla[tablaActiva] = new Set();
  const set = columnasOcultasPorTabla[tablaActiva];
  if (set.has(col)) set.delete(col); else set.add(col);
  renderTabla();
}

function marcarTodasColumnas(mostrar) {
  if (mostrar) {
    columnasOcultasPorTabla[tablaActiva] = new Set();
  } else {
    columnasOcultasPorTabla[tablaActiva] = new Set(colsActuales);
  }
  renderColumnasMenu();
  renderTabla();
}

// cerrar el menu de columnas si se hace click afuera
document.addEventListener('click', (e) => {
  const menu = document.getElementById('columnas-menu');
  if (!menu || menu.style.display === 'none') return;
  const boton = e.target.closest('button');
  const dentroDelMenu = e.target.closest('.columnas-menu');
  if (dentroDelMenu) return;
  if (boton && boton.getAttribute('onclick') === 'toggleColumnasMenu()') return;
  menu.style.display = 'none';
});

// ─── Modal de detalle de fila ───────────────────────────────────────────────
function abrirModal(filaGlobalIdx) {
  const todas = EER_DATA.datos[tablaActiva] || [];
  const fila = filasFiltradas ? filasFiltradas[filaGlobalIdx] : todas[filaGlobalIdx];
  if (!fila) return;

  document.getElementById('modal-titulo').textContent =
    `${tablaActiva} — registro`;

  let html = '';
  for (const [campo, valor] of Object.entries(fila)) {
    let valorTexto = '';
    let claseExtra = '';

    if (valor === null || valor === undefined || valor === '') {
      valorTexto = 'NULL';
      claseExtra = 'vacio';
    } else if (Array.isArray(valor) && (campo === 'chunks' || campo === 'archivos' || campo === 'expedientes')) {
      valorTexto = formatearTrazabilidad(valor, campo) || '(vacío)';
      claseExtra = 'mono';
    } else if (typeof valor === 'object') {
      valorTexto = formatearValorGenerico(valor) || '(vacío)';
      claseExtra = 'mono';
    } else if (campo.endsWith('_id') && EER_DATA.display_names) {
      // FK: mostrar id + nombre legible + link para saltar a esa tabla
      const tipo = campo.replace('_id', '');
      const map = EER_DATA.display_names[tipo];
      if (map && map[valor]) {
        valorTexto = `${valor}  →  ${map[valor]}`;
      } else {
        valorTexto = String(valor);
      }
      claseExtra = 'mono';
    } else {
      valorTexto = String(valor);
    }

    html += `<div class="modal-field">
      <div class="campo-nombre">${campo}</div>
      <div class="campo-valor ${claseExtra}">${valorTexto.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
    </div>`;
  }

  document.getElementById('modal-body').innerHTML = html;
  document.getElementById('modal-overlay').style.display = 'flex';
}

function cerrarModal() {
  document.getElementById('modal-overlay').style.display = 'none';
}

function cerrarModalSiFondo(e) {
  if (e.target.id === 'modal-overlay') cerrarModal();
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') cerrarModal();
});

// render tabla con paginacion y filtro
function renderTabla() {
  if (!tablaActiva) return;
  const todas = EER_DATA.datos[tablaActiva] || [];
  const filtro = document.getElementById('buscar').value.toLowerCase();
  filasFiltradas = !filtro ? todas : todas.filter(fila =>
    Object.values(fila).some(v => String(v).toLowerCase().includes(filtro)));

  const inicio = (paginaActual - 1) * TAMANO_PAGINA;
  const fin = Math.min(inicio + TAMANO_PAGINA, filasFiltradas.length);
  const pagina = filasFiltradas.slice(inicio, fin);

  const area = document.getElementById('content-area');
  if (pagina.length === 0) {
    area.innerHTML = '<div class="empty">Sin datos.</div>';
    return;
  }

  // construir lista de columnas: si una es *_id, agregar la columna de display
  const colsRaw = Object.keys(pagina[0]);
  const colsTodas = [];
  const nombreCols = {};
  for (const c of colsRaw) {
    colsTodas.push(c);
    if (c.endsWith('_id') && c !== 'expediente_id') {
      const tipo = c.replace('_id', '');
      // Skip if table already has a 'nombre' column (avoid duplicate)
      if (colsRaw.includes('nombre') && tipo !== 'persona' && tipo !== 'vehiculo') continue;
      if (EER_DATA.display_names && EER_DATA.display_names[tipo]) {
        const colNombre = c + '_nombre';
        nombreCols[c] = colNombre;
        colsTodas.push(colNombre);
      }
    }
  }
  colsActuales = colsTodas;   // para el menu de columnas

  const ocultas = columnasOcultasPorTabla[tablaActiva] || new Set();
  const cols = colsTodas.filter(c => !ocultas.has(c));

  let html = '<table><thead><tr>';
  for (const c of cols) html += `<th>${c}</th>`;
  html += '</tr></thead><tbody>';

  for (let fIdx = 0; fIdx < pagina.length; fIdx++) {
    const fila = pagina[fIdx];
    const filaGlobalIdx = inicio + fIdx;
    html += `<tr onclick="abrirModal(${filaGlobalIdx})">`;
    for (const c of cols) {
      // columna auxiliar de nombre?
      if (nombreCols[c] !== undefined && c.startsWith('__no__')) continue;
      // si es columna de nombre y no es la original, mostrar el nombre desde display_names
      const cBase = c.replace(/_nombre$/, '');
      const esColNombre = c.endsWith('_nombre') && nombreCols[cBase] === c;
      const cReal = esColNombre ? cBase : c;

      const v = fila[cReal];
      let cellClass = '';
      let cellText = '';
      let cellFull = '';   // texto completo para el tooltip (title=)

      if (v === null || v === undefined || v === '') {
        cellClass = 'null';
        cellText = esColNombre ? '' : 'NULL';
        cellFull = cellText;
      } else if (typeof v === 'object' && (cReal === 'chunks' || cReal === 'archivos' || cReal === 'expedientes')) {
        // formato legible de trazabilidad con links HTML
        cellClass = 'trazabilidad';
        const formateado = formatearTrazabilidad(v, cReal);
        // cellText = HTML with links (displayed in <td>)
        cellText = formateado.length > 110 ? formateado.substring(0, 107) + '...' : formateado;
        // cellFull = plain text for title attribute (strip HTML tags)
        cellFull = formateado.replace(/<[^>]*>/g, '');
      } else if (typeof v === 'object') {
        // cualquier otro array/objeto (fuentes, aliases_origen, cargos, etc.)
        // se formatea sin corchetes/comillas de JSON crudo
        cellClass = 'json-val';
        cellFull = formatearValorGenerico(v);
        cellText = cellFull.length > 90 ? cellFull.substring(0, 87) + '...' : cellFull;
      } else if (cReal === 'persona_id' || cReal.endsWith('_id') || cReal === 'placa' || cReal === 'vehiculo_id') {
        if (esColNombre) {
          // columna de display: nombre legible
          const tipo = cReal.replace('_id', '');
          const map = (EER_DATA.display_names || {})[tipo] || {};
          const dispName = map[v] || '(sin nombre)';
          cellClass = 'display-name';
          cellText = dispName;
          cellFull = dispName;
        } else {
          // columna ID: muestra el ID en monospace
          cellClass = 'id';
          cellText = String(v);
          cellFull = cellText;
        }
      } else {
        cellFull = String(v);
        cellText = cellFull.length > 100 ? cellFull.substring(0, 97) + '...' : cellFull;
      }
      // limpiar el caso empty en col-nombre
      if (esColNombre && cellText === '') { cellText = '—'; cellFull = ''; }
      // Escape HTML entities for the title attribute (plain text tooltip)
      const titleEsc = cellFull.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      html += `<td class="${cellClass}" title="${titleEsc}">${cellText}</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';

  // pagination
  const totalPaginas = Math.ceil(filasFiltradas.length / TAMANO_PAGINA);
  if (totalPaginas > 1) {
    html += '<div class="pagination">';
    html += `<button ${paginaActual===1?'disabled':''} onclick="irPagina(1)">«</button>`;
    html += `<button ${paginaActual===1?'disabled':''} onclick="irPagina(${paginaActual}-1)">‹</button>`;
    html += `<span style="color:#94a3b8;font-size:12px">Pagina ${paginaActual} de ${totalPaginas} (${filasFiltradas.length} filas)</span>`;
    html += `<button ${paginaActual===totalPaginas?'disabled':''} onclick="irPagina(${paginaActual}+1)">›</button>`;
    html += `<button ${paginaActual===totalPaginas?'disabled':''} onclick="irPagina(${totalPaginas})">»</button>`;
    html += '</div>';
  }

  area.innerHTML = html;
}

function irPagina(p) {
  if (typeof p === 'string') p = eval(p);
  if (p < 1) return;
  paginaActual = p;
  renderTabla();
}

document.getElementById('buscar').addEventListener('input', () => {
  paginaActual = 1;
  renderTabla();
});

// mostrar diagrama EER completo
function mostrarDiagrama() {
  tablaActiva = null;
  document.querySelectorAll('.tabla-btn').forEach(b => b.classList.remove('activa'));
  document.getElementById('titulo-tabla').textContent = 'Diagrama EER';
  document.getElementById('buscar').value = '';

  let html = '<div class="stats">';
  const totalFilas = Object.values(EER_DATA.tablas).reduce((s,t) => s + t.filas, 0);
  html += `Modelo EER del Caso LeBaron con <b>${Object.keys(EER_DATA.tablas).length}</b> tablas y <b>${totalFilas}</b> filas totales.<br>`;
  html += `Las <b style="color:#3b82f6">entidades</b> son los objetos principales; las <b style="color:#a78bfa">relaciones</b> conectan entidades.<br>`;
  html += `Click en "Ver datos" para ver el detalle, o usa los botones de export.`;
  html += '</div>';

  html += '<div class="eer-diagram">';
  for (const [tabla, info] of Object.entries(EER_DATA.tablas)) {
    html += '<div class="eer-tabla ' + (tabla.startsWith('rel_') ? 'relacion' : 'entidad') + '">';
    html += `<div class="nombre-tabla">${tabla} <span style="float:right;font-size:11px;color:#94a3b8">${info.filas} filas</span></div>`;

    // columnas segun schema
    const schema = EER_DATA.schema[tabla] || [];
    if (schema.length > 0) {
      html += '<table>';
      for (const col of schema) {
        let cls = '';
        if (col.pk) cls = 'pk';
        else if (col.fk) cls = 'fk';
        html += `<tr><td class="${cls}">${col.nombre}</td><td style="color:#94a3b8;font-size:10px">${col.tipo.split(' ')[0]}</td></tr>`;
      }
      html += '</table>';
    }
    html += `<div class="action"><button onclick="cargarTabla('${tabla}')">Ver datos</button></div>`;
    html += '</div>';
  }
  html += '</div>';

  document.getElementById('content-area').innerHTML = html;
}

// exportadores
function descargar(contenido, nombre, tipo) {
  const blob = new Blob([contenido], { type: tipo });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = nombre; a.click();
  URL.revokeObjectURL(url);
}

function exportarJSON() {
  if (!tablaActiva) return;
  const data = EER_DATA.datos[tablaActiva] || [];
  descargar(JSON.stringify(data, null, 2), tablaActiva + '.json', 'application/json');
}

function exportarCSV() {
  if (!tablaActiva) return;
  const data = EER_DATA.datos[tablaActiva] || [];
  if (data.length === 0) return;
  const cols = Object.keys(data[0]);
  let csv = cols.join(',') + '\\n';
  for (const fila of data) {
    const cells = cols.map(c => {
      let v = fila[c];
      if (v === null || v === undefined) return '';
      if (typeof v === 'object') v = JSON.stringify(v);
      return '"' + String(v).replace(/"/g, '""').replace(/\\n/g, ' ') + '"';
    });
    csv += cells.join(',') + '\\n';
  }
  descargar(csv, tablaActiva + '.csv', 'text/csv');
}

async function exportarSQL() {
  if (!tablaActiva) return;
  const r = await fetch('tablas/' + tablaActiva + '.json');
  const data = await r.json();
  const schema = EER_DATA.schema[tablaActiva] || [];
  let sql = `-- Backup table ${tablaActiva}\\n`;
  sql += `CREATE TABLE backup_${tablaActiva}_${new Date().toISOString().slice(0,10).replace(/-/g,'')} AS SELECT * FROM ${tablaActiva} WHERE 0=1;\\n`;
  const cols = schema.map(c => c.nombre).filter(n => !n.toUpperCase().includes('FOREIGN'));
  for (const fila of data) {
    const values = cols.map(c => {
      const v = fila[c];
      if (v === null) return 'NULL';
      if (typeof v === 'number') return String(v);
      return "'" + String(v).replace(/'/g, "''") + "'";
    });
    sql += `INSERT INTO backup_${tablaActiva} (${cols.join(', ')}) VALUES (${values.join(', ')});\\n`;
  }
  descargar(sql, 'backup_' + tablaActiva + '.sql', 'text/plain');
}

async function exportarTodo() {
  // concatena todas las tablas en un ZIP-equivalente (JSON compuesto)
  const todo = {};
  for (const [tabla, info] of Object.entries(EER_DATA.tablas)) {
    const r = await fetch('tablas/' + tabla + '.json');
    todo[tabla] = await r.json();
  }
  descargar(JSON.stringify(todo, null, 2), 'caso_lebaron_eer_completo.json', 'application/json');
}

// init
document.getElementById('meta-timestamp').textContent = EER_DATA.timestamp;
document.getElementById('meta-total').textContent = Object.keys(EER_DATA.tablas).length + ' tablas';
poblarSidebar();
mostrarDiagrama();
</script>
</body>
</html>
"""


def make_data_for_visor(db_data: dict, schema: dict) -> dict:
    """Prepara el JSON que se incrusta en el visor UI."""
    return {
        "timestamp": __import__('datetime').datetime.now().isoformat(),
        "tablas": {k: {"filas": len(v)} for k, v in db_data.items()},
        "datos": db_data,  # se sobrescribe por fetch en runtime si es muy grande
        "schema": {k: [{"nombre": c[0], "tipo": c[1], "pk": c[0] == info.get("pk"),
                       "fk": "REFERENCES" in c[1]} for c in info["columns"]]
                   for k, info in schema.items()},
    }


def build_eer_ui():
    """Punto de entrada principal."""
    db_path = os.path.join(EER_DIR, "caso_lebaron.db")
    if not os.path.exists(db_path):
        print("Error: primero ejecuta build_eer.py")
        return

    print("Cargando datos del EER...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    tablas_info = {}
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'backup_%' ORDER BY name")
    for (t,) in cur.fetchall():
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        tablas_info[t] = {"filas": cur.fetchone()[0]}

    # cargar todas las tablas para el JSON embebido
    db_data = {}
    for (t,) in cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'backup_%' ORDER BY name").fetchall():
        rows = cur.execute(f"SELECT * FROM {t}").fetchall()
        # convertir JSON strings de vuelta a objetos
        parsed = []
        for row in rows:
            d = dict(row)
            for k, v in d.items():
                if isinstance(v, str) and v.startswith('[') and v.endswith(']'):
                    try:
                        d[k] = json.loads(v)
                    except:
                        pass
            parsed.append(d)
        db_data[t] = parsed

    conn.close()

    # construir mapa id -> nombre legible (DESPUES de cargar db_data)
    display_names = {
        "persona": {},
        "vehiculo": {},
        "lugar": {},
        "hallazgo": {},
        "expediente": {},
    }
    for p in db_data.get('persona', []):
        display_names['persona'][p['persona_id']] = p.get('nombre_canonico', p['persona_id'])
    for v in db_data.get('vehiculo', []):
        display_names['vehiculo'][v['vehiculo_id']] = v.get('placa') or v['vehiculo_id']
    for l in db_data.get('lugar', []):
        display_names['lugar'][l['lugar_id']] = l.get('nombre') or l['lugar_id']
    for h in db_data.get('hallazgo', []):
        display_names['hallazgo'][h['hallazgo_id']] = (
            f"{h['hallazgo_id']} ({h.get('tipo','')}, sev={h.get('severidad','?')})"
        )
    for e in db_data.get('expediente', []):
        display_names['expediente'][e['expediente_id']] = e.get('nombre_archivo', e['expediente_id'])

    # cargar mapeo de PDFs
    pdf_mapping = {}
    pdf_map_path = os.path.join(EER_DIR, 'pdf_mapping.json')
    if os.path.exists(pdf_map_path):
        with open(pdf_map_path, 'r', encoding='utf-8') as f:
            pdf_mapping = json.load(f)

    # generar JSON con todos los datos
    out_data = {
        "timestamp": __import__('datetime').datetime.now().isoformat(),
        "tablas": tablas_info,
        "datos": db_data,
        "schema": {t: [{"nombre": c[0], "tipo": c[1], "pk": c[0] == EER_SCHEMA[t]["pk"],
                       "fk": "REFERENCES" in c[1]} for c in EER_SCHEMA[t]["columns"]]
                   for t in tablas_info},
        "display_names": display_names,
        "pdf_mapping": pdf_mapping,
    }

    # guardar JSON embebido
    data_json_path = os.path.join(EER_DIR, "data_eer.json")
    with open(data_json_path, "w") as f:
        json.dump(out_data, f, ensure_ascii=False)
    print(f"JSON: {data_json_path} ({os.path.getsize(data_json_path)//1024} KB)")

    # generar HTML
    html_out = HTML.replace("__DATA__", json.dumps(out_data, ensure_ascii=False)
                                            .replace("</", "<\\/"))
    visor_path = os.path.join(EER_DIR, "visor.html")
    with open(visor_path, "w") as f:
        f.write(html_out)
    print(f"Visor: {visor_path} ({os.path.getsize(visor_path)//1024} KB)")


if __name__ == "__main__":
    build_eer_ui()
