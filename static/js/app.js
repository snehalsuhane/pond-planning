/* Contour analysis with land-constrained pond sites and full upstream catchments. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const number = (value, digits = 2) => Number.isFinite(value)
    ? value.toLocaleString(undefined, {maximumFractionDigits: digits}) : '—';
  const status = (message, kind = '') => {
    $('status').textContent = message;
    $('status').className = kind;
  };
  if (!window.L || !L.Control.Draw) {
    status('Map libraries could not load. Check your internet connection and reload the page.', 'error');
    return;
  }
  const colors = ['#24594b', '#94682b', '#446c9d', '#896095', '#a95146'];
  const map = L.map('map', {worldCopyJump: true, zoomAnimation: false}).setView([20, 0], 2);
  const mapElement = $('map');
  map.on('draw:drawstart', () => { mapElement.dataset.tool = 'draw'; });
  map.on('draw:editstart', () => { mapElement.dataset.tool = 'edit'; });
  map.on('draw:deletestart', () => { mapElement.dataset.tool = 'delete'; });
  map.on('draw:drawstop draw:editstop draw:deletestop', () => { delete mapElement.dataset.tool; });
  map.on('dragstart', () => { mapElement.dataset.dragging = 'true'; });
  map.on('dragend', () => { delete mapElement.dataset.dragging; });
  let zoomCursorTimer;
  mapElement.addEventListener('wheel', event => {
    mapElement.dataset.zoom = event.deltaY < 0 ? 'in' : 'out';
    clearTimeout(zoomCursorTimer);
    zoomCursorTimer = setTimeout(() => { delete mapElement.dataset.zoom; }, 350);
  }, {passive: true});
  document.addEventListener('keydown', event => {
    if (event.key === 'Shift') mapElement.dataset.shift = 'true';
  });
  document.addEventListener('keyup', event => {
    if (event.key === 'Shift') delete mapElement.dataset.shift;
  });
  window.addEventListener('blur', () => {
    delete mapElement.dataset.shift;
    delete mapElement.dataset.dragging;
    delete mapElement.dataset.zoom;
  });
  const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
  tiles.on('tileerror', () => { $('map-caption').textContent = 'Base map unavailable. Survey overlays remain usable.'; });
  L.control.scale({imperial: false, position: 'bottomright'}).addTo(map);
  const land = L.featureGroup().addTo(map);
  const contours = L.featureGroup().addTo(map);
  const catchments = L.featureGroup().addTo(map);
  const markers = L.featureGroup().addTo(map);
  const terrainExtent = L.featureGroup();
  const pondDesign = L.featureGroup().addTo(map);
  let designRequest = null, designRevision = 0;
  let storageRequest = null, storageRevision = 0, storageData = null;
  let sizingRequest = null, sizingRevision = 0;
  let checkedDesign = null, storageMapLabel = null;
  let lastDesignResult = null, lastSiteCandidate = null, lastAnalysisData = null;
  L.control.layers(null, {'Contour preview': contours, 'Land boundary': land, 'Analyzed terrain': terrainExtent}, {position: 'topright'}).addTo(map);
  map.addControl(new L.Control.Draw({
    draw: {polygon: {allowIntersection: false, showArea: true, shapeOptions: {color: '#ad7e26', dashArray: '6 5'}},
      rectangle: {shapeOptions: {color: '#ad7e26', dashArray: '6 5'}},
      polyline: false, circle: false, marker: false, circlemarker: false},
    edit: {featureGroup: land, poly: {allowIntersection: false}}
  }));
  function updateLand() {
    const layer = land.getLayers()[0];
    $('selection-area').textContent = layer
      ? `${number(L.GeometryUtil.geodesicArea(layer.getLatLngs()[0]) / 10000)} ha`
      : 'No boundary drawn';
  }
  map.on(L.Draw.Event.CREATED, event => {
    land.clearLayers();
    land.addLayer(event.layer);
    updateLand();
    invalidateSelection();
  });
  map.on('draw:edited draw:deleted', () => { updateLand(); invalidateSelection(); });
  map.on('draw:drawstart draw:editstart draw:deletestart', () => {
    editing = true;
    invalidateSelection();
  });
  map.on('draw:drawstop draw:editstop draw:deletestop', () => {
    editing = false;
    invalidateSelection();
  });

  let candidates = [], selected = 0, request = null, revision = 0, editing = false;
  const fileInput = $('contour-file');
  const sourceInput = $('terrain-source');
  const isPublic = () => sourceInput.value === 'public';
  function clearResults() {
    clearDesign();
    $('open-design').disabled = true;
    candidates = [];
    markers.clearLayers();
    catchments.clearLayers();
    terrainExtent.clearLayers();
    $('candidate-list').replaceChildren();
    $('results').hidden = true;
    $('compare').checked = false;
    $('compare').disabled = true;
    lastAnalysisData = null;
  }
  function fileError(file) {
    if (!file) return 'Upload a contour map to begin.';
    if (!/\.(kml|kmz)$/i.test(file.name)) return 'Choose a .kml or .kmz contour file.';
    if (!file.size) return 'This file is empty. Choose a contour map with elevation data.';
    if (file.size > 50 * 1024 * 1024) return 'The file exceeds the 50 MB upload limit.';
    return null;
  }
  function inputError() {
    const coefficient = Number($('runoff-coefficient').value);
    if (!$('runoff-coefficient').value.trim() || !Number.isFinite(coefficient) || coefficient < 0 || coefficient > 1)
      return 'Enter a runoff fraction between 0 and 1.';
    if (!isPublic()) return fileError(fileInput.files[0]);
    const boundary = land.getLayers()[0];
    if (!boundary) return 'Draw a land boundary to use public elevation.';
    if (L.GeometryUtil.geodesicArea(boundary.getLatLngs()[0]) > 25000000) return 'Select a smaller area (up to 2,500 hectares).';
    return null;
  }
  function displaySource() {
    $('upload-panel').hidden = isPublic();
    $('public-help').hidden = !isPublic();
    $('land-requirement').textContent = isPublic() ? 'required' : 'optional';
    $('mode-label').textContent = isPublic() ? 'Public terrain analysis' : 'Contour survey analysis';
    $('land-help').textContent = isPublic()
      ? 'Select up to 2,500 ha. Pond sites stay inside your land; catchments can extend outside it. Surrounding terrain is included automatically.'
      : 'Pond sites must lie inside your land. Catchments can extend beyond it. Without a boundary, the whole survey is searched.';
    if (isPublic()) map.removeLayer(contours); else contours.addTo(map);
  }
  $('runoff-coefficient').addEventListener('input', invalidateSelection);
  sourceInput.addEventListener('change', () => { displaySource(); invalidateSelection(); });
  let searchRequest = null, searchVersion = 0;
  $('place-query').addEventListener('input', () => {
    ++searchVersion;
    searchRequest?.abort();
    $('search-results').replaceChildren();
    $('search-results').hidden = true;
    $('search-status').textContent = '';
    $('search-button').disabled = false;
  });
  $('place-search').addEventListener('submit', async event => {
    event.preventDefault();
    const query = $('place-query').value.trim();
    if (query.length < 2) { $('search-status').textContent = 'Enter at least two characters.'; return; }
    const version = ++searchVersion;
    searchRequest?.abort();
    const controller = new AbortController();
    searchRequest = controller;
    const timeout = setTimeout(() => controller.abort(), 25000);
    $('search-button').disabled = true;
    $('search-results').replaceChildren();
    $('search-results').hidden = true;
    $('search-status').textContent = 'Searching locations…';
    try {
      const response = await fetch(`/api/places/search?q=${encodeURIComponent(query)}`, {signal: controller.signal});
      const data = await response.json();
      if (version !== searchVersion) return;
      if (!response.ok) throw new Error(data.error || 'Location search is unavailable. Please try again.');
      for (const place of data.results) {
        const button = element('button', 'search-result', place.label);
        button.type = 'button';
        button.addEventListener('click', () => {
          if (place.bounds) map.stop().fitBounds(place.bounds, {padding: [45, 45], maxZoom: 16, animate: false});
          else map.stop().setView([place.latitude, place.longitude], 15, {animate: false});
          $('latitude').value = place.latitude;
          $('longitude').value = place.longitude;
          $('place-query').value = place.label.slice(0, 200);
          $('search-results').hidden = true;
          $('search-status').textContent = 'Location shown. Draw a boundary around the land you want to analyze.';
          if (window.matchMedia('(max-width: 760px)').matches) mapElement.scrollIntoView({block: 'start'});
        });
        const item = element('li');
        item.append(button);
        $('search-results').append(item);
      }
      $('search-results').hidden = !data.results.length;
      $('search-status').textContent = data.results.length ? 'Select a location below.' : 'No locations found. Try adding the district, state, or country.';
    } catch (error) {
      if (version !== searchVersion) return;
      $('search-status').textContent = error.name === 'AbortError'
        ? 'Location search timed out. Try again or use coordinates.'
        : error instanceof TypeError || error instanceof SyntaxError
          ? 'Location search is unavailable. Try again or use coordinates.' : error.message;
    } finally {
      clearTimeout(timeout);
      if (version === searchVersion) { $('search-button').disabled = false; searchRequest = null; }
    }
  });
  $('locate-form').addEventListener('submit', event => {
    event.preventDefault();
    const lat = Number($('latitude').value), lon = Number($('longitude').value);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -80 || lat > 84 || Math.abs(lon) > 180) return;
    map.stop().setView([lat, lon], 15, {animate: false});
  });
  function invalidateSelection() {
    ++revision;
    request?.abort();
    request = null;
    clearResults();
    $('analyze').disabled = editing || !!inputError();
    $('analyze').textContent = 'Find pond locations ↗';
    $('map-caption').textContent = land.getLayers().length ? 'Pond sites will be restricted to your land' : isPublic() ? 'Zoom in and draw the land you want to analyze' : 'Analysis will search the full contour survey';
    status(editing ? 'Finish or cancel the boundary edit before analyzing.'
      : inputError() || 'Ready. Run the analysis to refresh pond options.');
  }
  async function preview(file, version) {
    if (!/\.kml$/i.test(file.name)) return;
    const xml = new DOMParser().parseFromString(await file.text(), 'application/xml');
    if (version !== revision || xml.getElementsByTagName('parsererror').length) return;
    const lines = [];
    for (const line of xml.getElementsByTagNameNS('*', 'LineString')) {
      const raw = line.getElementsByTagNameNS('*', 'coordinates')[0]?.textContent;
      if (!raw) continue;
      const points = raw.trim().split(/\s+/).map(point => point.split(',').map(Number))
        .filter(([lon, lat]) => Number.isFinite(lon) && Number.isFinite(lat) && Math.abs(lon) <= 180 && Math.abs(lat) <= 90)
        .map(([lon, lat]) => [lat, lon]);
      if (points.length > 1) lines.push(L.polyline(points, {color: '#737a57', weight: 1, opacity: .5, interactive: false}));
    }
    lines.forEach(line => contours.addLayer(line));
    if (contours.getBounds().isValid()) {
      map.fitBounds(contours.getBounds(), {padding: [40, 40], maxZoom: 16, animate: false});
      $('map-caption').textContent = 'Contour preview · draw a land boundary if needed';
    }
  }
  fileInput.addEventListener('change', async () => {
    sourceInput.value = 'contours';
    displaySource();
    const version = ++revision;
    request?.abort();
    request = null;
    clearResults();
    contours.clearLayers();
    contours.addTo(map);
    land.clearLayers();
    updateLand();
    $('analyze').textContent = 'Find pond locations ↗';
    const file = fileInput.files[0], error = fileError(file);
    $('analyze').disabled = editing || !!inputError();
    $('file-info').textContent = file ? `${file.name} · ${number(file.size / 1024 / 1024)} MB` : 'Your survey supplies the elevation data.';
    $('map-caption').textContent = 'Upload a survey to locate your terrain';
    status(error || 'Survey ready. You can mark your land or run the analysis.', error && file ? 'error' : '');
    if (!error) {
      try { await preview(file, version); }
      catch { if (version === revision) $('file-info').textContent = `${file.name} · Preview unavailable; upload to analyze.`; }
    }
  });

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function volumeLabel(candidate) {
    const volume = candidate.water_volume;
    if (!Number.isFinite(volume?.annual_m3)) return 'Annual runoff unavailable';
    return `Estimated annual runoff: ${number(volume.annual_m3, 0)} m³/year`;
  }
  function geometry(candidate) {
    return candidate.catchment?.geometry;
  }
  function selectCandidate(index, fit = true, scroll = false) {
    if (selected !== index) clearDesign();
    selected = index;
    $('open-design').disabled = !land.getLayers().length;
    $('open-design').textContent = `Design this pond · option ${index + 1}`;
    $('design-help').textContent = land.getLayers().length ? 'Select a pond option, then check a proposed footprint and capacity.' : 'Draw a land boundary and rerun analysis to check whether a pond footprint fits.';
    catchments.clearLayers();
    candidates.forEach((candidate, i) => {
      const card = $('candidate-list').children[i];
      card.setAttribute('aria-pressed', String(i === index));
      const marker = markers.getLayers()[i];
      marker.setIcon(L.divIcon({className: `pond-marker${i === index ? ' selected' : ''}`,
        html: `<span>${i + 1}</span>`, iconSize: [30, 30], iconAnchor: [15, 15]}));
      marker.getElement().style.setProperty('--site-color', colors[i]);
      marker.setZIndexOffset(i === index ? 1000 : 0);
      if (i === index || $('compare').checked) {
        L.geoJSON(geometry(candidate), {style: {color: colors[i], weight: i === index ? 2.5 : 1.5,
          fillOpacity: i === index ? .2 : 0, dashArray: i === index ? null : '5 5'}, interactive: false}).addTo(catchments);
      }
      if (i === index) marker.openTooltip(); else marker.closeTooltip();
    });
    if (fit) {
      const bounds = L.geoJSON(geometry(candidates[index])).getBounds();
      bounds.extend([candidates[index].latitude, candidates[index].longitude]);
      map.stop().fitBounds(bounds, {padding: [55, 65], maxZoom: 17, animate: false});
      if (window.matchMedia('(max-width: 760px)').matches) $('map').scrollIntoView({block: 'start'});
    }
    if (scroll) $('candidate-list').children[index].scrollIntoView({block: 'nearest'});
    $('map-caption').textContent = `Option ${index + 1} · ${number(candidates[index].catchment.area_ha)} ha catchment · ${volumeLabel(candidates[index])}`;
  }
  function viewAll() {
    const bounds = markers.getBounds();
    candidates.forEach(candidate => bounds.extend(L.geoJSON(geometry(candidate)).getBounds()));
    if (bounds.isValid()) map.stop().fitBounds(bounds, {padding: [55, 65], maxZoom: 17, animate: false});
  }
  function showResults(data) {
    map.removeLayer(contours);
    lastAnalysisData = data;
    candidates = (data.pond_candidates || []).slice(0, 5);
    $('results').hidden = false;
    $('result-count').textContent = String(candidates.length);
    const publicTerrain = data.planning?.terrain_scope === 'buffered_public_dem';
    $('result-meta').textContent = `${data.terrain_source?.name || data.filename} · ${number(data.dem?.resolution_m)} m terrain grid · ${data.waterway_screening?.status === 'screened_against_mapped_water' ? 'Mapped-water screening complete' : 'Water screening not confirmed'}`;
    if (data.waterway_screening?.source) $('result-meta').textContent += ` · ${data.waterway_screening.source}`;
    $('source-credit').hidden = !publicTerrain;
    const rainfall = data.rainfall;
    $('rainfall-credit').hidden = rainfall?.status !== 'available';
    $('rainfall-meta').textContent = rainfall?.status === 'available'
      ? `Average rainfall: ${number(rainfall.mean_annual_mm, 0)} mm/year (${rainfall.start_year}–${rainfall.end_year}). Runoff fraction: ${number(data.water_volume_model.runoff_coefficient)}. Regional precipitation is used as a rainfall estimate for all options.`
      : rainfall?.reason || 'Historical rainfall unavailable; water volumes could not be estimated.';
    if (data.terrain?.geometry) L.geoJSON(data.terrain.geometry, {style: {color: '#68766d', weight: 1.5, dashArray: '5 5', fillOpacity: 0}, interactive: false}).addTo(terrainExtent);
    if (data.land_selection) $('result-meta').textContent += ` · Sites within ${number(data.land_selection.area_ha)} ha of selected land`;
    const coverage = [];
    if (data.waterway_screening?.coverage_note) coverage.push(data.waterway_screening.coverage_note);
    if (data.land_selection?.partial_terrain_coverage) coverage.push(`The survey covers ${number(data.land_selection.terrain_coverage_fraction * 100, 1)}% of your selected land. Only covered terrain was searched.`);
    if (publicTerrain) coverage.push(`Public surface elevation at 30 m resolution; ${number(data.planning.terrain_buffer_m / 1000)} km of surrounding terrain included.`);
    if (candidates.some(site => site.catchment.boundary_truncated)) coverage.push(publicTerrain
      ? 'Some catchments still reach the analyzed terrain edge. Their areas and runoff estimates may be incomplete because upstream land may be missing.'
      : 'Some catchments reach the survey edge. Their areas and runoff estimates may be incomplete because upstream land may be missing. A larger survey is needed to resolve this.');
    if (data.planning?.coverage_note) coverage.push(data.planning.coverage_note);
    $('coverage-note').textContent = coverage.join(' ');
    $('coverage-note').hidden = !coverage.length;
    $('fit-results').disabled = !candidates.length;
    $('compare').disabled = candidates.length < 2;
    candidates.forEach((candidate, i) => {
      const title = candidate.collection_type === 'natural_depression' ? 'Natural collection area' : 'Drainage outlet';
      const card = element('button', 'candidate');
      card.type = 'button';
      card.style.setProperty('--site-color', colors[i]);
      const heading = element('span', 'candidate-title');
      heading.append(element('span', 'rank', String(i + 1)), document.createTextNode(title));
      const area = element('span', 'candidate-area', `${number(candidate.catchment.area_ha)} ha `);
      area.append(element('small', '', 'catchment'));
      card.append(heading, area,
        element('span', 'candidate-volume', volumeLabel(candidate)),
        element('span', 'candidate-info', `${number(candidate.local_slope_deg)}° local slope · ${number(candidate.elevation_m, 1)} m elevation`),
        element('span', 'candidate-info', `${candidate.latitude.toFixed(5)}, ${candidate.longitude.toFixed(5)}`));
      const reasons = candidate.water_volume?.uncertainty_reasons || [];
      reasons.forEach(reason => card.append(element('span', 'flag', reason.message)));
      card.addEventListener('click', () => selectCandidate(i));
      $('candidate-list').append(card);
      const label = element('span', '', `Option ${i + 1} · ${number(candidate.catchment.area_ha)} ha`);
      label.append(element('span', 'tooltip-volume', volumeLabel(candidate)));
      reasons.forEach(reason => label.append(element('span', 'tooltip-reason', reason.message)));
      L.marker([candidate.latitude, candidate.longitude], {title: `Option ${i + 1}: ${title}`, alt: `Pond option ${i + 1}`})
        .bindTooltip(label, {direction: 'top', offset: [0, -17], className: 'site-tooltip'})
        .on('click', () => selectCandidate(i, true, true)).addTo(markers);
    });
    if (candidates.length) selectCandidate(0);
    else $('candidate-list').append(element('p', 'notice', 'No suitable pond locations were found. Try a different boundary or contour map.'));
  }

  // ── Site summary panel ──────────────────────────────────────────────────────
  function showSiteSummary(designData, site) {
    const dl = $('summary-stats');
    dl.replaceChildren();
    function row(label, value, sub) {
      const dt = element('dt', 'summary-label', label);
      const dd = element('dd', 'summary-value', value);
      if (sub) dd.append(element('span', 'summary-sub', sub));
      dl.append(dt, dd);
    }
    row('Location', `${site.latitude.toFixed(5)}, ${site.longitude.toFixed(5)}`);
    row('Catchment area', `${number(site.catchment.area_ha)} ha`);
    row('Annual runoff (est.)', volumeLabel(site).replace('Estimated annual runoff: ', ''));
    row('Excavation depth', `${number(designData.dimensions.depth_m)} m`, `${number(designData.water_depth_m)} m water depth`);
    row('Footprint', `${number(designData.dimensions.length_m)} × ${number(designData.dimensions.width_m)} m`, `${number(designData.footprint_area_m2)} m² rim area`);
    row('Proposed capacity', `${number(designData.capacity_m3, 0)} m³`, 'below freeboard');
    row('Fit status', designData.screening_status === 'passes_checks' ? '✓ Passes land & water checks' :
      designData.screening_status === 'does_not_fit' ? '✗ Does not fit — revise design' : '⚠ Water check unavailable');
    $('design-summary').hidden = false;
  }

  function clearDesign(close = true) {
    clearStorage();
    clearSizing();
    checkedDesign = null;
    storageMapLabel = null;
    lastDesignResult = null;
    lastSiteCandidate = null;
    $('storage-panel').hidden = true;
    $('sizing-panel').hidden = true;
    $('design-summary').hidden = true;
    $('summary-stats').replaceChildren();
    ++designRevision;
    designRequest?.abort();
    designRequest = null;
    pondDesign.clearLayers();
    if (candidates[selected]) {
      $('map-caption').textContent = `Option ${selected + 1} · ${number(candidates[selected].catchment.area_ha)} ha catchment · ${volumeLabel(candidates[selected])}`;
      markers.getLayers()[selected]?.openTooltip();
    }
    $('design-result').replaceChildren();
    $('design-result').hidden = true;
    $('fit-design').hidden = true;
    $('download-results').hidden = true;
    $('calculate-design').disabled = false;
    $('design-status').textContent = '';
    if (close) $('pond-design').hidden = true;
  }
  $('open-design').addEventListener('click', () => {
    if (!candidates.length || !land.getLayers().length) return;
    $('pond-design').hidden = false;
    $('design-title').textContent = `Design pond option ${selected + 1}`;
    $('pond-design').scrollIntoView({block: 'nearest'});
  });
  $('design-form').addEventListener('input', () => {
    clearDesign(false);
    $('design-status').textContent = 'Dimensions changed. Recalculate to refresh capacity and map checks.';
  });
  $('fit-design').addEventListener('click', () => {
    const bounds = pondDesign.getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, {padding: [60, 90], maxZoom: 19, animate: false});
    if (window.matchMedia('(max-width: 760px)').matches) $('map').scrollIntoView({block: 'start'});
  });
  $('design-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!candidates.length || !land.getLayers().length) return;
    clearDesign(false);
    const version = designRevision;
    const controller = new AbortController();
    designRequest = controller;
    const timeout = setTimeout(() => controller.abort(), 180000);
    const site = candidates[selected];
    const body = Object.fromEntries([...new FormData($('design-form'))].map(([key, value]) => [key, Number(value)]));
    body.site = {latitude: site.latitude, longitude: site.longitude};
    body.land_area = land.getLayers()[0].toGeoJSON().geometry;
    $('calculate-design').disabled = true;
    $('design-status').textContent = 'Calculating capacity and checking the full footprint against land and mapped water…';
    try {
      const response = await fetch('/api/designPond', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body), signal: controller.signal});
      const data = await response.json();
      if (version !== designRevision) return;
      if (!response.ok) throw new Error(data.error || 'Pond design could not be checked.');
      const passed = data.screening_status === 'passes_checks';
      const color = passed ? '#167c86' : data.screening_status === 'does_not_fit' ? '#b34535' : '#94682b';
      L.geoJSON(data.clearance, {style: {color, weight: 2, dashArray: '5 5', fillOpacity: 0}, interactive: false}).addTo(pondDesign);
      L.geoJSON(data.footprint, {style: {color, weight: 2, fillOpacity: .15}, interactive: false}).addTo(pondDesign);
      const label = element('span', '', `Proposed capacity: ${number(data.capacity_m3, 0)} m³`);
      label.append(element('span', 'tooltip-reason', `${number(data.dimensions.depth_m)} m excavation · ${number(data.water_depth_m)} m water depth`));
      storageMapLabel = element('span', 'tooltip-reason', '');
      label.append(storageMapLabel);
      label.append(element('span', 'tooltip-reason', passed ? 'Land and mapped-water checks passed' : data.screening_status === 'does_not_fit' ? 'Does not fit · revise this design' : 'Water check unavailable · unverified'));
      L.geoJSON(data.water_surface, {style: {color, weight: 1, fillOpacity: .35}})
        .bindTooltip(label, {permanent: true, direction: 'top', className: 'site-tooltip'}).addTo(pondDesign);
      markers.getLayers()[selected].closeTooltip();
      $('design-status').textContent = data.messages.join(' ');
      $('design-result').append(
        element('p', 'candidate-volume', `Proposed capacity: ${number(data.capacity_m3, 0)} m³`),
        element('p', 'hint', `Water depth: ${number(data.water_depth_m)} m · Bottom: ${number(data.bottom_length_m)} × ${number(data.bottom_width_m)} m`),
        element('p', 'hint', `Excavation footprint: ${number(data.footprint_area_m2)} m² · Land including margin: ${number(data.land_required_m2)} m²`),
        element('p', 'hint', volumeLabel(site)),
        element('p', 'hint', data.assumptions));
      if (data.waterway_screening?.coverage_note) $('design-result').append(element('p', 'hint', data.waterway_screening.coverage_note));
      lastDesignResult = data;
      lastSiteCandidate = site;
      showSiteSummary(data, site);
      checkedDesign = passed ? body : null;
      $('storage-panel').hidden = !passed;
      $('sizing-panel').hidden = false;
      $('design-result').hidden = false;
      $('fit-design').hidden = false;
      $('download-results').hidden = false;
      $('map-caption').textContent = `Pond option ${selected + 1} · Proposed capacity ${number(data.capacity_m3, 0)} m³ · ${passed ? 'Map checks passed' : 'Design needs review'}`;
    } catch (error) {
      if (version !== designRevision) return;
      $('design-status').textContent = error.name === 'AbortError' ? 'Design check timed out. Please retry.'
        : error instanceof TypeError || error instanceof SyntaxError ? 'Design service unavailable. Please retry.' : error.message;
    } finally {
      clearTimeout(timeout);
      if (version === designRevision) { designRequest = null; $('calculate-design').disabled = false; }
    }
  });

  // ── Storage simulation ──────────────────────────────────────────────────────
  function clearStorage() {
    ++storageRevision;
    storageRequest?.abort();
    storageRequest = null;
    storageData = null;
    if (storageMapLabel) storageMapLabel.textContent = '';
    $('storage-result').hidden = true;
    $('storage-status').textContent = '';
    $('calculate-storage').disabled = false;
  }
  $('storage-form').addEventListener('input', () => {
    clearStorage();
    $('storage-status').textContent = 'Assumptions changed. Run the simulation again.';
  });

  function renderStorageChart(rows, cap, selectedMonth) {
    const svg = $('storage-chart');
    svg.replaceChildren();
    const W = 380, H = 210, PL = 48, PR = 10, PT = 28, PB = 32;
    const chartW = W - PL - PR, chartH = H - PT - PB;
    const ns = 'http://www.w3.org/2000/svg';
    function node(tag, attrs, text) {
      const el = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
      if (text !== undefined) el.textContent = text;
      svg.append(el);
      return el;
    }
    node('title', {}, `End-of-month stored water, capacity ${number(cap, 0)} m³`);
    // Fill area under curve
    const pts = rows.map((r, i) => [PL + i * (chartW / (rows.length - 1 || 1)), PT + chartH - chartH * r.end_storage_m3 / cap]);
    const area = [...pts.map(([x, y]) => `${x},${y}`), `${pts.at(-1)[0]},${PT + chartH}`, `${PL},${PT + chartH}`].join(' ');
    node('polygon', {points: area, fill: '#167c8622', stroke: 'none'});
    // Capacity line
    node('line', {x1: PL, y1: PT, x2: PL + chartW, y2: PT, stroke: '#9cae98', 'stroke-dasharray': '5 4', 'stroke-width': 1});
    node('text', {x: PL + 2, y: PT - 6, 'font-size': 10, fill: '#68766d'}, `Cap. ${number(cap, 0)} m³`);
    // Zero line
    node('line', {x1: PL, y1: PT + chartH, x2: PL + chartW, y2: PT + chartH, stroke: '#d5dccf', 'stroke-width': 1});
    // Y-axis ticks
    [0, 0.25, 0.5, 0.75, 1].forEach(frac => {
      const y = PT + chartH - chartH * frac;
      node('line', {x1: PL - 4, y1: y, x2: PL, y2: y, stroke: '#9cae98', 'stroke-width': 1});
      node('text', {x: PL - 6, y: y + 4, 'font-size': 9, fill: '#68766d', 'text-anchor': 'end'}, `${Math.round(frac * 100)}%`);
    });
    // Curve
    node('polyline', {points: pts.map(([x, y]) => `${x},${y}`).join(' '), fill: 'none', stroke: '#167c86', 'stroke-width': 2.5, 'stroke-linejoin': 'round'});
    // Month dots and labels
    const months = ['J','F','M','A','M','J','J','A','S','O','N','D'];
    rows.forEach((r, i) => {
      const [cx, cy] = pts[i];
      const isSel = i === selectedMonth;
      node('circle', {cx, cy, r: isSel ? 5.5 : 2.5, fill: '#167c86', stroke: isSel ? '#fff' : 'none', 'stroke-width': isSel ? 2 : 0});
      if (rows.length <= 12) node('text', {x: cx, y: PT + chartH + 14, 'font-size': 9, fill: '#68766d', 'text-anchor': 'middle'}, months[i]);
    });
    // Y-axis label
    const lbl = document.createElementNS(ns, 'text');
    lbl.setAttribute('transform', `rotate(-90,12,${PT + chartH / 2})`);
    lbl.setAttribute('x', 0); lbl.setAttribute('y', 0);
    lbl.setAttribute('font-size', 9); lbl.setAttribute('fill', '#68766d');
    lbl.setAttribute('text-anchor', 'middle');
    lbl.setAttribute('dominant-baseline', 'central');
    lbl.textContent = '% full';
    lbl.setAttribute('transform', `translate(12,${PT + chartH / 2}) rotate(-90)`);
    svg.append(lbl);
  }

  function renderFillRateChart(annualRows, cap) {
    const svg = $('fillrate-chart');
    svg.replaceChildren();
    const n = annualRows.length;
    if (!n) return;
    const barW = Math.min(20, Math.floor(340 / n) - 2);
    const svgW = n * (barW + 2) + 20;
    const svgH = 48;
    svg.setAttribute('viewBox', `0 0 ${svgW} ${svgH}`);
    svg.setAttribute('width', '100%');
    svg.setAttribute('style', 'display:block;margin:8px 0');
    const ns = 'http://www.w3.org/2000/svg';
    function node(tag, attrs, text) {
      const el = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
      if (text !== undefined) el.textContent = text;
      svg.append(el);
      return el;
    }
    annualRows.forEach((row, i) => {
      const filled = row.days_full > 0;
      const x = 10 + i * (barW + 2);
      const barH = filled ? 24 : 8;
      const y = 26 - barH;
      node('rect', {x, y, width: barW, height: barH, rx: 2, fill: filled ? '#167c86' : '#d5dccf'});
      if (i === 0 || i === n - 1 || n <= 8) {
        node('text', {x: x + barW / 2, y: 40, 'font-size': 8, fill: '#68766d', 'text-anchor': 'middle'}, String(row.year));
      }
    });
    const filled = annualRows.filter(r => r.days_full > 0).length;
    node('text', {x: svgW - 2, y: 10, 'font-size': 9, fill: '#24594b', 'text-anchor': 'end', 'font-weight': '600'},
      `${filled}/${n} years filled`);
  }

  function renderStorage() {
    if (!storageData) return;
    const year = $('storage-year').value;
    const rows = storageData.monthly.filter(row => row.month.startsWith(year));
    const row = rows[Number($('storage-month').value)];
    const annual = storageData.annual.find(item => String(item.year) === year);
    const cap = storageData.capacity_m3;
    const seasonal = storageData.seasonal?.summary;
    let summaryText = `${storageData.annual.filter(item => item.days_full > 0).length} of ${storageData.annual.length} historical years reached capacity.`;
    if (seasonal) {
      summaryText += ` Fill rate: ${number(seasonal.fill_rate_pct, 0)}%. Mean annual overflow: ${number(seasonal.mean_annual_overflow_m3, 0)} m³.`;
      if (seasonal.mean_end_monsoon_storage_pct !== null) summaryText += ` Typical end-of-monsoon storage: ${number(seasonal.mean_end_monsoon_storage_pct, 0)}% full.`;
    }
    summaryText += ` ${year}: overflow ${number(annual.overflow_m3, 0)} m³; water use supplied ${number(annual.supplied_m3, 0)} m³; unmet use ${number(annual.unmet_demand_m3, 0)} m³.`;
    $('storage-summary').textContent = summaryText;
    const snapshot = `${row.month} month end: estimated stored water ${number(row.end_storage_m3, 0)} m³ (${number(100 * row.end_storage_m3 / cap, 0)}% full)`;
    $('storage-snapshot').textContent = `${snapshot}. Monthly inflow ${number(row.inflow_m3, 0)} m³; overflow ${number(row.overflow_m3, 0)} m³; evaporation ${number(row.evaporation_m3, 0)} m³; seepage ${number(row.seepage_m3, 0)} m³.`;
    if (storageMapLabel) storageMapLabel.textContent = snapshot;
    renderStorageChart(rows, cap, Number($('storage-month').value));
    renderFillRateChart(storageData.annual, cap);
  }
  $('storage-year').addEventListener('change', renderStorage);
  $('storage-month').addEventListener('change', renderStorage);
  $('storage-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!checkedDesign) return;
    clearStorage();
    const version = storageRevision;
    const controller = new AbortController();
    storageRequest = controller;
    const timeout = setTimeout(() => controller.abort(), 65000);
    const settings = Object.fromEntries([...new FormData($('storage-form'))].map(([key, value]) => [key, Number(value)]));
    $('calculate-storage').disabled = true;
    $('storage-status').textContent = 'Simulating daily storage across historical rainfall years…';
    try {
      const response = await fetch('/api/simulatePond', {method: 'POST', headers: {'Content-Type': 'application/json'}, signal: controller.signal,
        body: JSON.stringify({...checkedDesign, ...settings, catchment: candidates[selected].catchment.geometry,
          runoff_coefficient: Number($('runoff-coefficient').value)})});
      const data = await response.json();
      if (version !== storageRevision) return;
      if (!response.ok) throw new Error(data.error || 'Storage simulation failed.');
      storageData = data;
      $('storage-year').replaceChildren(...data.annual.map(row => new Option(row.year, row.year)));
      $('storage-month').replaceChildren(...Array.from({length: 12}, (_, i) => new Option(new Date(2020, i, 1).toLocaleString(undefined, {month: 'long'}), i)));
      $('storage-explanation').textContent = `${data.rainfall.source}, ${data.rainfall.start_year}–${data.rainfall.end_year}. ${data.explanation}`;
      $('storage-result').hidden = false;
      $('storage-status').textContent = 'Simulation complete. Choose a year and month to inspect stored water on the map.';
      renderStorage();
    } catch (error) {
      if (version !== storageRevision) return;
      $('storage-status').textContent = error.name === 'AbortError' ? 'Simulation timed out. Please retry.' : error instanceof TypeError || error instanceof SyntaxError ? 'Storage service unavailable. Please retry.' : error.message;
    } finally {
      clearTimeout(timeout);
      if (version === storageRevision) { storageRequest = null; $('calculate-storage').disabled = false; }
    }
  });

  // ── Size alternatives ───────────────────────────────────────────────────────
  function clearSizing() {
    ++sizingRevision;
    sizingRequest?.abort();
    sizingRequest = null;
    $('sizing-result').hidden = true;
    $('sizing-status').textContent = '';
    $('sizing-tbody').replaceChildren();
  }

  async function runSizing() {
    if (!candidates.length || !land.getLayers().length) return;
    clearSizing();
    const version = sizingRevision;
    const controller = new AbortController();
    sizingRequest = controller;
    const timeout = setTimeout(() => controller.abort(), 90000);
    const site = candidates[selected];
    const targetRaw = $('target-volume').value.trim();
    const body = {
      site: {latitude: site.latitude, longitude: site.longitude},
      land_area: land.getLayers()[0].toGeoJSON().geometry,
      catchment: site.catchment.geometry,
      runoff_coefficient: Number($('runoff-coefficient').value),
    };
    if (targetRaw) body.target_volume_m3 = Number(targetRaw);
    $('suggest-size').disabled = true;
    $('sizing-status').textContent = 'Evaluating size alternatives against 10 years of historical rainfall…';
    try {
      const response = await fetch('/api/suggestSize', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body), signal: controller.signal});
      const data = await response.json();
      if (version !== sizingRevision) return;
      if (!response.ok) throw new Error(data.error || 'Size comparison failed.');
      $('sizing-reasoning').textContent = data.reasoning;
      const tbody = $('sizing-tbody');
      tbody.replaceChildren();
      data.alternatives.forEach(alt => {
        const tr = document.createElement('tr');
        const isRec = alt.label === 'recommended' || (data.recommended && alt.capacity_m3 === data.recommended.capacity_m3 && alt.label === data.recommended.label);
        if (isRec) tr.className = 'sizing-recommended';
        const pct = alt.mean_end_monsoon_storage_pct !== null ? `${number(alt.mean_end_monsoon_storage_pct, 0)}%` : '—';
        tr.innerHTML = `
          <td><span class="sizing-label sizing-label--${alt.label}">${alt.label.replace('_', ' ')}${isRec ? ' ★' : ''}</span></td>
          <td>${number(alt.length_m)} × ${number(alt.width_m)}</td>
          <td>${number(alt.depth_m)}</td>
          <td>${number(alt.capacity_m3, 0)}</td>
          <td>${number(alt.fill_rate_pct, 0)}%</td>
          <td>${number(alt.mean_annual_inflow_m3, 0)}</td>
          <td>${number(alt.mean_annual_overflow_m3, 0)}</td>
          <td>${pct}</td>`;
        tbody.append(tr);
      });
      $('sizing-result').hidden = false;
      $('sizing-status').textContent = `${data.mode === 'target' ? 'Target mode' : 'Alternatives mode'} · ${data.alternatives.length} options evaluated against ${data.rainfall?.end_year - data.rainfall?.start_year + 1 || 10} years of rainfall.`;
    } catch (error) {
      if (version !== sizingRevision) return;
      $('sizing-status').textContent = error.name === 'AbortError' ? 'Size comparison timed out. Please retry.'
        : error instanceof TypeError || error instanceof SyntaxError ? 'Sizing service unavailable. Please retry.' : error.message;
    } finally {
      clearTimeout(timeout);
      if (version === sizingRevision) { sizingRequest = null; $('suggest-size').disabled = false; }
    }
  }
  $('suggest-size').addEventListener('click', runSizing);
  $('refresh-sizing').addEventListener('click', runSizing);

  // ── Download results summary ────────────────────────────────────────────────
  $('download-results').addEventListener('click', () => {
    if (!lastDesignResult || !lastSiteCandidate) return;
    const site = lastSiteCandidate;
    const design = lastDesignResult;
    const rainfall = lastAnalysisData?.rainfall;
    const seasonal = storageData?.seasonal?.summary;
    const now = new Date().toISOString().slice(0, 10);

    const rows = (label, value) => `<tr><th scope="row">${label}</th><td>${value}</td></tr>`;
    const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Pond Planning Summary — ${now}</title>
<style>
  body{font-family:system-ui,sans-serif;font-size:13px;color:#203d36;max-width:780px;margin:32px auto;padding:0 24px}
  h1{font-size:22px;margin:0 0 4px}
  .meta{color:#68766d;font-size:12px;margin:0 0 28px}
  h2{font-size:14px;border-bottom:1px solid #e0e5da;padding-bottom:6px;margin:24px 0 10px}
  table{border-collapse:collapse;width:100%;margin-bottom:16px}
  th,td{padding:7px 10px;text-align:left;border-bottom:1px solid #e0e5da;font-weight:400}
  th[scope=row]{color:#68766d;width:46%;font-size:12px}
  .notice{background:#edf0e5;border-radius:6px;padding:10px 14px;font-size:12px;color:#586448;margin:16px 0}
  footer{font-size:11px;color:#68766d;margin-top:32px;border-top:1px solid #e0e5da;padding-top:12px}
</style></head><body>
<h1>Village Pond Planner — Results Summary</h1>
<p class="meta">Generated ${now} · This is an illustrative planning scenario, not a construction specification or guaranteed yield.</p>

<h2>Selected site — Option ${selected + 1}</h2>
<table>
  ${rows('Location', `${site.latitude.toFixed(5)}° N, ${site.longitude.toFixed(5)}° E`)}
  ${rows('Collection type', site.collection_type === 'natural_depression' ? 'Natural depression' : 'Drainage outlet')}
  ${rows('Catchment area', `${number(site.catchment.area_ha)} ha`)}
  ${rows('Local slope', `${number(site.local_slope_deg)}°`)}
  ${rows('Elevation', `${number(site.elevation_m, 1)} m`)}
  ${rows('Annual runoff (estimated)', volumeLabel(site))}
</table>

<h2>Rainfall data</h2>
<table>
  ${rainfall ? rows('Source', rainfall.source || 'NASA POWER') : ''}
  ${rainfall ? rows('Period', `${rainfall.start_year}–${rainfall.end_year}`) : ''}
  ${rainfall ? rows('Mean annual rainfall', `${number(rainfall.mean_annual_mm, 0)} mm/year`) : ''}
  ${rows('Runoff fraction used', $('runoff-coefficient').value)}
</table>

<h2>Proposed pond design</h2>
<table>
  ${rows('Top rim dimensions', `${number(design.dimensions.length_m)} × ${number(design.dimensions.width_m)} m`)}
  ${rows('Excavation depth', `${number(design.dimensions.depth_m)} m`)}
  ${rows('Water depth (below freeboard)', `${number(design.water_depth_m)} m`)}
  ${rows('Side slope', `${number(design.dimensions.side_slope)}:1 H:V`)}
  ${rows('Freeboard', `${number(design.dimensions.freeboard_m)} m`)}
  ${rows('Margin around rim', `${number(design.dimensions.margin_m)} m`)}
  ${rows('Bottom dimensions', `${number(design.bottom_length_m)} × ${number(design.bottom_width_m)} m`)}
  ${rows('Footprint area', `${number(design.footprint_area_m2)} m²`)}
  ${rows('Proposed capacity', `${number(design.capacity_m3, 0)} m³`)}
  ${rows('Fit status', design.screening_status === 'passes_checks' ? 'Passes land and mapped-water checks' : design.screening_status === 'does_not_fit' ? 'Does not fit — design needs revision' : 'Water check unavailable')}
</table>

${storageData ? `<h2>Seasonal storage simulation</h2>
<p class="notice">Loss defaults: evaporation ${storageData.assumptions?.evaporation_mm_day ?? '—'} mm/day · seepage ${storageData.assumptions?.seepage_mm_day ?? '—'} mm/day · daily demand ${storageData.assumptions?.demand_m3_day ?? '—'} m³/day. These are illustrative, not measured values.</p>
<table>
  ${seasonal ? rows('Fill rate', `${number(seasonal.fill_rate_pct, 0)}% of modelled years reached capacity`) : ''}
  ${seasonal ? rows('Mean annual overflow', `${number(seasonal.mean_annual_overflow_m3, 0)} m³`) : ''}
  ${seasonal?.mean_end_monsoon_storage_pct !== null ? rows('Mean end-of-monsoon storage', `${number(seasonal.mean_end_monsoon_storage_pct, 0)}% of capacity`) : ''}
  ${rows('Years analysed', String(storageData.annual.length))}
  ${rows('Years at capacity', String(storageData.annual.filter(r => r.days_full > 0).length))}
  ${rows('Total period inflow', `${number(storageData.totals.inflow_m3, 0)} m³`)}
  ${rows('Total period overflow', `${number(storageData.totals.overflow_m3, 0)} m³`)}
</table>` : ''}

<h2>Assumptions and limitations</h2>
<p class="notice">
  Terrain: ${lastAnalysisData?.terrain_source?.name || 'contour survey'}.
  Rainfall: regional daily precipitation from NASA POWER; not a local rain gauge.
  Storage model: daily mass balance with uniform rainfall and constant runoff fraction. Not a forecast.
  Pond dimensions assume level ground and a flat bottom. Ground stability, inlet/outlet design and earthworks on sloping terrain are not assessed.
  Catchments trace modelled upstream drainage; boundaries are provisional where they reach the terrain edge.
  Pond ranking is based on catchment size and local slope; it does not reflect design suitability or water availability.
  Mapped-water screening uses OpenStreetMap data, which may be incomplete.
</p>

<footer>Village Pond Planner · ${window.location.origin} · Generated ${new Date().toLocaleString()}</footer>
</body></html>`;
    const blob = new Blob([html], {type: 'text/html'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `pond-planning-summary-${now}.html`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 2000);
  });

  $('compare').addEventListener('change', () => selectCandidate(selected, false));
  $('fit-results').addEventListener('click', viewAll);
  $('analyze').addEventListener('click', async () => {
    const file = fileInput.files[0], error = inputError();
    if (error || editing) { status(error || 'Finish the boundary edit first.', 'error'); return; }
    const version = ++revision;
    request?.abort();
    const controller = new AbortController();
    request = controller;
    const timeout = setTimeout(() => controller.abort(), isPublic() ? 300000 : 180000);
    clearResults();
    $('analyze').disabled = true;
    $('analyze').textContent = 'Analyzing terrain…';
    status(isPublic() ? 'Retrieving elevation, checking mapped water, tracing catchments, and retrieving rainfall. This can take a few minutes.'
      : 'Analyzing terrain, checking mapped water, tracing catchments, and retrieving rainfall. This can take a minute.', 'loading');
    try {
      const boundary = land.getLayers()[0];
      let response;
      if (isPublic()) {
        response = await fetch('/api/analyzeArea', {method: 'POST', signal: controller.signal,
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify({land_area: boundary.toGeoJSON().geometry, runoff_coefficient: Number($('runoff-coefficient').value)})});
      } else {
        const form = new FormData();
        form.append('contour_map', file);
        form.append('runoff_coefficient', $('runoff-coefficient').value);
        if (boundary) form.append('land_area', JSON.stringify(boundary.toGeoJSON().geometry));
        response = await fetch('/api/analyzeContour', {method: 'POST', body: form, signal: controller.signal});
      }
      const data = await response.json().catch(() => null);
      if (version !== revision) return;
      if (!response.ok || data?.status !== 'success') {
        throw new Error(data?.error || (response.status === 413 ? 'Upload is too large. Choose a smaller file.' : `Analysis failed (${response.status}). Please try again.`));
      }
      showResults(data);
      status(candidates.length ? `${candidates.length} pond ${candidates.length === 1 ? 'option' : 'options'} found. Select one to explore its catchment.` : 'Analysis complete. No suitable sites found.');
    } catch (error) {
      if (version !== revision) return;
      clearResults();
      status(error.name === 'AbortError' ? 'The request timed out. Please try again; the server may still be finishing the analysis.'
        : error instanceof TypeError ? 'Could not reach the server. Check your connection and try again.' : error.message, 'error');
    } finally {
      clearTimeout(timeout);
      if (version === revision) {
        request = null;
        $('analyze').disabled = editing || !!inputError();
        $('analyze').textContent = 'Find pond locations ↗';
      }
    }
  });
})();
