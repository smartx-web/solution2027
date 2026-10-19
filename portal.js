'use strict';
(async function () {
  const pickers = [...document.querySelectorAll('.location-picker')];
  function options(select, label, values, selected = '') {
    select.replaceChildren(new Option(label, ''));
    for (const [value, text] of values) select.add(new Option(text, value));
    select.value = String(selected);
    select.disabled = !values.length;
  }
  try {
    const response = await fetch('/api/locations', {headers: {Accept: 'application/json'}});
    if (!response.ok || !(response.headers.get('content-type') || '').includes('application/json')) throw Error('Please sign in again to load the directory.');
    const rows = await response.json();
    const filterForm = document.querySelector('#filters form');
    if (filterForm) {
      const filterLga = filterForm.elements.lga;
      const filterWard = filterForm.elements.ward;
      filterLga.addEventListener('change', () => {
        const wards = [...new Set(rows.filter(row => !filterLga.value || row.lga === filterLga.value).map(row => row.ward))].sort();
        options(filterWard, 'All wards', wards.map(ward => [ward, ward]));
        filterWard.disabled = false;
      });
    }
    for (const picker of pickers) {
      const form = picker.closest('form');
      const lga = picker.querySelector('[name=location_lga]');
      const ward = picker.querySelector('[name=location_ward]');
      const pu = picker.querySelector('[name=pu_id]');
      const status = picker.querySelector('.directory-status');
      const totalForm = form.getAttribute('action') === '/pu';
      const unique = values => [...new Set(values)].sort().map(value => [value, value]);
      function selectUnit() {
        const row = rows.find(row => String(row.id) === pu.value && row.lga === lga.value && row.ward === ward.value);
        if (totalForm) {
          for (const name of ['id','lga','ward','code','name','voters','source','source_date']) {
            form.elements[name].value = row ? (row[name] ?? '') : '';
          }
          if (row && row.voters === null) {
            form.elements.source.value = '';
            form.elements.source_date.value = '';
          }
        }
        status.textContent = row ? `${row.code} — ${row.name}. 2023 historical baseline: ${row.voters_2023 == null ? 'not recorded' : row.voters_2023}. Other sourced total: ${row.voters === null ? 'not recorded' : row.voters}.` : 'Choose a polling unit.';
      }
      function selectWard() {
        const units = rows.filter(row => row.lga === lga.value && row.ward === ward.value);
        options(pu, ward.value ? 'Select a polling unit' : 'Choose ward first', units.map(row => [row.id, `${row.code} — ${row.name}`]));
        selectUnit();
        if (ward.value && !units.length) status.textContent = 'No polling units available for this ward.';
      }
      function selectLga() {
        options(ward, lga.value ? 'Select a ward' : 'Choose LGA first', unique(rows.filter(row => row.lga === lga.value).map(row => row.ward)));
        selectWard();
      }
      const initialLga = lga.value, initialWard = ward.value, initialPu = pu.value;
      options(lga, 'Select an LGA', unique(rows.map(row => row.lga)), initialLga);
      selectLga();
      if (initialWard) { ward.value = initialWard; selectWard(); }
      if (initialPu) { pu.value = initialPu; selectUnit(); }
      lga.addEventListener('change', selectLga);
      ward.addEventListener('change', selectWard);
      pu.addEventListener('change', selectUnit);
    }
  } catch (error) {
    for (const picker of pickers) {
      picker.querySelector('.directory-status').textContent = 'Directory could not load. Refresh the page or sign in again.';
      picker.closest('form').querySelector('button[type=submit],button').disabled = true;
    }
  }
})();
