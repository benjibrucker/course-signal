(function attachResultsModel(root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
    return;
  }
  if (root) root.CourseSignalResultsModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function createResultsModel() {
  'use strict';

  const POUNDS_PER_KILOGRAM = 2.2046226218;
  const MAX_TOTAL_WEIGHT_KG = 500;
  const SAFE_ROUTE_FIELDS = new Set(['race', 'event', 'mode', 'runner']);
  const SAFE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$/;

  function numberOrNull(value) {
    if (value === null || value === undefined || typeof value === 'boolean') return null;
    if (typeof value === 'string' && value.trim() === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function weightKg(value, unit) {
    const parsed = numberOrNull(value);
    if (parsed === null || parsed <= 0) return null;
    const kilograms = unit === 'kg' ? parsed : unit === 'lb' ? parsed / POUNDS_PER_KILOGRAM : null;
    if (kilograms === null || kilograms > MAX_TOTAL_WEIGHT_KG) return null;
    return kilograms;
  }

  function normalizedEnergyRange(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const low = numberOrNull(value.low);
    const estimate = numberOrNull(value.estimate);
    const high = numberOrNull(value.high);
    if (low === null || estimate === null || high === null || low < 0 || low > estimate || estimate > high) return null;
    return {low, estimate, high};
  }

  function scaleEnergyRange(factors, kilograms) {
    const range = normalizedEnergyRange(factors);
    const mass = numberOrNull(kilograms);
    if (!range || mass === null || mass <= 0 || mass > MAX_TOTAL_WEIGHT_KG) return null;
    const scaled = {
      low: range.low * mass,
      estimate: range.estimate * mass,
      high: range.high * mass,
    };
    return Object.values(scaled).every(Number.isFinite) ? scaled : null;
  }

  function energyFactors(record) {
    if (!record || typeof record !== 'object') return null;
    if (record.energy_kcal_per_kg) return record.energy_kcal_per_kg;
    if (record.energy && record.energy.per_kg_kcal) return record.energy.per_kg_kcal;
    const flat = {
      low: record.energy_kcal_per_kg_low,
      estimate: record.energy_kcal_per_kg_estimate ?? record.energy_kcal_per_kg_mid,
      high: record.energy_kcal_per_kg_high,
    };
    return normalizedEnergyRange(flat) ? flat : null;
  }

  function personalizeEnergy(analysis, weight, unit) {
    const kilograms = weightKg(weight, unit);
    if (kilograms === null) {
      return {status: 'invalid', reason: 'invalid_weight', total: null, sections: []};
    }
    const total = scaleEnergyRange(energyFactors(analysis && analysis.totals), kilograms);
    if (!total) {
      return {status: 'unavailable', reason: 'energy_model_unavailable', total: null, sections: []};
    }
    const sourceSections = Array.isArray(analysis.sections) ? analysis.sections : [];
    const sections = sourceSections.map((section) => ({
      ...section,
      energy: scaleEnergyRange(energyFactors(section), kilograms),
    }));
    return {status: 'ready', weight_kg: kilograms, total, sections};
  }

  function isPrivacySafeRoute(value, baseUrl) {
    let base;
    let url;
    try {
      const baseValue = baseUrl === undefined ? 'https://course-signal.local/' : String(baseUrl);
      const route = String(value);
      if (baseValue.includes('#') || route.includes('#')) return false;
      base = new URL(baseValue);
      if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.hash) return false;
      url = new URL(route, base);
    } catch (_error) {
      return false;
    }
    if (
      !['http:', 'https:'].includes(url.protocol)
      || url.username
      || url.password
      || url.origin !== base.origin
      || url.pathname !== base.pathname
      || url.hash
      || !url.searchParams.has('event')
    ) return false;
    const seen = new Set();
    for (const [key, fieldValue] of url.searchParams.entries()) {
      if (!SAFE_ROUTE_FIELDS.has(key) || seen.has(key)) return false;
      seen.add(key);
      if (key === 'mode') {
        if (!['live', 'results'].includes(fieldValue)) return false;
      } else if (!SAFE_IDENTIFIER.test(fieldValue)) {
        return false;
      }
    }
    return true;
  }

  function formatRange(value) {
    const range = normalizedEnergyRange(value);
    if (!range) return 'Unavailable';
    const roundTen = (amount) => Math.round(amount / 10) * 10;
    const low = roundTen(range.low).toLocaleString('en-US');
    const high = roundTen(range.high).toLocaleString('en-US');
    return `${low}–${high} active kcal`;
  }

  return Object.freeze({
    weightKg,
    scaleEnergyRange,
    personalizeEnergy,
    isPrivacySafeRoute,
    formatRange,
  });
});
