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
    candidates = [];
    markers.clearLayers();
    catchments.clearLayers();
    terrainExtent.clearLayers();
    $('candidate-list').replaceChildren();
    $('results').hidden = true;
    $('compare').checked = false;
    $('compare').disabled = true;
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
    // KMZ parsing stays on the backend; its map extent is available after analysis.
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
    if (!Number.isFinite(volume?.annual_m3)) return 'Water volume unavailable';
    return `${number(volume.annual_m3, 0)} m³/year${volume.status === 'provisional' ? ' · provisional' : ' · estimated'}`;
  }
  function geometry(candidate) {
    return candidate.catchment?.geometry;
  }
  function selectCandidate(index, fit = true, scroll = false) {
    selected = index;
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
    // Contours remain available in the layer control, but can obscure catchments.
    map.removeLayer(contours);
    candidates = (data.pond_candidates || []).slice(0, 5);
    $('results').hidden = false;
    $('result-count').textContent = String(candidates.length);
    const publicTerrain = data.planning?.terrain_scope === 'buffered_public_dem';
    $('result-meta').textContent = `${data.terrain_source?.name || data.filename} · ${number(data.dem?.resolution_m)} m terrain grid · ${data.waterway_screening?.status === 'screened_against_mapped_water' ? 'Mapped-water screening complete' : 'Water screening not confirmed'}`;
    $('source-credit').hidden = !publicTerrain;
    const rainfall = data.rainfall;
    $('rainfall-credit').hidden = rainfall?.status !== 'available';
    $('rainfall-meta').textContent = rainfall?.status === 'available'
      ? `Average rainfall: ${number(rainfall.mean_annual_mm, 0)} mm/year (${rainfall.start_year}–${rainfall.end_year}). Runoff fraction: ${number(data.water_volume_model.runoff_coefficient)}. Regional precipitation is used as a rainfall estimate for all options.`
      : rainfall?.reason || 'Historical rainfall unavailable; water volumes could not be estimated.';
    if (data.terrain?.geometry) L.geoJSON(data.terrain.geometry, {style: {color: '#68766d', weight: 1.5, dashArray: '5 5', fillOpacity: 0}, interactive: false}).addTo(terrainExtent);
    if (data.land_selection) $('result-meta').textContent += ` · Sites within ${number(data.land_selection.area_ha)} ha of selected land`;
    const coverage = [];
    if (data.land_selection?.partial_terrain_coverage) coverage.push(`The survey covers ${number(data.land_selection.terrain_coverage_fraction * 100, 1)}% of your selected land. Only covered terrain was searched.`);
    if (publicTerrain) coverage.push(`Public surface elevation at 30 m resolution; ${number(data.planning.terrain_buffer_m / 1000)} km of surrounding terrain included.`);
    if (candidates.some(site => site.catchment.boundary_truncated)) coverage.push(publicTerrain
      ? 'Some catchments still reach the analyzed terrain edge. Their areas remain provisional because upstream terrain may be missing.'
      : 'Some catchments reach the survey edge. Their areas are provisional because upstream terrain may be missing. A larger survey is needed to resolve this.');
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
      if (candidate.catchment.boundary_truncated) card.append(element('span', 'flag', 'Provisional area · catchment reaches terrain edge'));
      if (candidate.assessment?.routing_sensitivity_fraction > .5) card.append(element('span', 'flag', 'Area depends strongly on modeled overflow'));
      card.addEventListener('click', () => selectCandidate(i));
      $('candidate-list').append(card);
      const label = element('span', '', `Option ${i + 1} · ${number(candidate.catchment.area_ha)} ha`);
      label.append(element('span', 'tooltip-volume', volumeLabel(candidate)));
      L.marker([candidate.latitude, candidate.longitude], {title: `Option ${i + 1}: ${title}`, alt: `Pond option ${i + 1}`})
        .bindTooltip(label, {direction: 'top', offset: [0, -17], className: 'site-tooltip'})
        .on('click', () => selectCandidate(i, true, true)).addTo(markers);
    });
    if (candidates.length) selectCandidate(0);
    else $('candidate-list').append(element('p', 'notice', 'No suitable pond locations were found. Try a different boundary or contour map.'));
  }
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
