/* Pure course-elevation geometry; metres throughout, no rematching or clocks. */
((root, factory) => {
  const elevation = factory();
  if (typeof module === 'object' && module.exports) module.exports = elevation;
  else root.RutElevation = elevation;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  'use strict';
  const EARTH_RADIUS_M = 6371000;
  const radians = degrees => degrees * Math.PI / 180;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const validProgress = value => finite(value) && value >= 0 && value <= 1;
  // Generous Earth-terrain bounds (including ocean trenches) also prevent
  // finite-but-extreme input overflowing feet conversion or chart ranges.
  const validElevation = value => finite(value) && Math.abs(value) <= 12000;
  const validTrackPoint = point => point && finite(point.lat) && Math.abs(point.lat) <= 90 &&
    finite(point.lng) && Math.abs(point.lng) <= 180 && validElevation(point.ele);
  const validProfilePoint = point => point && finite(point.distance_m) && finite(point.elevation_m);

  // Same horizontal great-circle accumulation as finish_metrics.prepare_route.
  function greatCircleM(a, b) {
    const lat1 = radians(a.lat), lat2 = radians(b.lat);
    const h = Math.sin((lat2 - lat1) / 2) ** 2 +
      Math.cos(lat1) * Math.cos(lat2) * Math.sin(radians(b.lng - a.lng) / 2) ** 2;
    return EARTH_RADIUS_M * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  function buildProfile(course) {
    const track = course?.track_points;
    if (!Array.isArray(track) || track.length < 2) return null;
    const points = [];
    let total_m = 0, min_m = Infinity, max_m = -Infinity;
    for (let index = 0; index < track.length; index++) {
      const point = track[index];
      // Reject the entire track, rather than bridge over a corrupt sample.
      if (!validTrackPoint(point)) return null;
      if (index) total_m += greatCircleM(track[index - 1], point);
      points.push({distance_m: total_m, elevation_m: point.ele});
      min_m = Math.min(min_m, point.ele);
      max_m = Math.max(max_m, point.ele);
    }
    if (!finite(total_m) || total_m <= 0) return null;
    return {points, total_m, min_m, max_m};
  }

  // Profiles are the unmodified output of buildProfile; query in O(log n).
  function atProgress(profile, progress) {
    if (!validProgress(progress) || !profile || !finite(profile.total_m) || profile.total_m <= 0 ||
        !Array.isArray(profile.points) || profile.points.length < 2) return null;
    const {points, total_m} = profile;
    const first = points[0], last = points[points.length - 1];
    if (!validProfilePoint(first) || !validProfilePoint(last) || first.distance_m !== 0 || last.distance_m !== total_m) return null;
    // Preserve the original endpoints even when several samples share them.
    if (progress === 0) return {...first};
    if (progress === 1) return {...last};
    const distance_m = progress * total_m;
    let low = 0, high = points.length - 1;
    // Lower bound: exact internal duplicate distances use the first sample.
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (!validProfilePoint(points[middle])) return null;
      if (points[middle].distance_m < distance_m) low = middle + 1;
      else high = middle;
    }
    const right = points[low];
    if (!validProfilePoint(right)) return null;
    if (right.distance_m === distance_m) return {distance_m, elevation_m: right.elevation_m};
    const left = points[low - 1];
    if (!validProfilePoint(left) || left.distance_m >= distance_m || right.distance_m <= distance_m) return null;
    const fraction = (distance_m - left.distance_m) / (right.distance_m - left.distance_m);
    const elevation_m = (1 - fraction) * left.elevation_m + fraction * right.elevation_m;
    return finite(elevation_m) ? {distance_m, elevation_m} : null;
  }

  function checkpointPoints(profile, course, splitNames) {
    if (!Array.isArray(splitNames) || !Array.isArray(course?.progress_points)) return [];
    const markers = [];
    // Timing indexes are authoritative; never use filtered geographic splits.
    splitNames.forEach((name, index) => {
      const progress = course.progress_points[index];
      const point = atProgress(profile, progress);
      if (point) markers.push({index, name, progress, ...point});
    });
    return markers;
  }

  function runnerPoint(profile, position) {
    if (!position || !((position.source === 'GPS' && position.distance_source === 'GPS_MATCHED') ||
        (position.source === 'ESTIMATED' && position.distance_source === 'SPLIT_ESTIMATE'))) return null;
    const point = atProgress(profile, position.progress);
    // Stale/held samples retain their supplied position; callers label freshness.
    return point ? {...point, progress: position.progress} : null;
  }

  return Object.freeze({buildProfile, atProgress, checkpointPoints, runnerPoint});
});
