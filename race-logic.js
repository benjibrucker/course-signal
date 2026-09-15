/* Shared, pure race-display rules; no DOM, network, or stored identities. */
((root, factory) => {
  const rules = factory();
  if (typeof module === 'object' && module.exports) module.exports = rules;
  else root.RutRules = rules;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  'use strict';
  const FRESH_SECONDS = 90;
  const finite = value => (typeof value === 'number' || (typeof value === 'string' && value.trim() !== '')) && Number.isFinite(Number(value));
  function timestamp(value) {
    if (!value) return null;
    const parsed = typeof value === 'number' ? value : Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  function refreshPosition(row, now = Date.now()) {
    if (row.source !== 'GPS') return {...row};
    const recorded = timestamp(row.recorded_at);
    const valid = recorded !== null && recorded <= now + 5000;
    const age = valid ? Math.max(0,(now-recorded)/1000) : null;
    const fresh = valid && age <= FRESH_SECONDS && row.freshness !== 'STALE';
    return {...row, age_seconds:age, freshness:fresh?'LIVE':'STALE',
      rank_eligible: Boolean(row.rank_eligible) && fresh};
  }
  function nearestFinish(rows, eventId, now = Date.now(), generatedAt = null) {
    const stamp = timestamp(generatedAt);
    if (stamp === null || now-stamp > FRESH_SECONDS*1000 || stamp > now+5000) return [];
    return rows.map(row=>refreshPosition(row,now))
      .filter(row=>row.event_id===eventId && row.status==='ON COURSE' && row.rank_eligible===true && !row.estimate_overdue && !row.estimate_held && finite(row.remaining_m) && Number(row.remaining_m)>=0)
      .sort((a,b)=>Number(a.remaining_m)-Number(b.remaining_m) || String(a.key??a.id).localeCompare(String(b.key??b.id),undefined,{numeric:true}))
      .slice(0,10);
  }
  function finishView(rows, eventId, now, generatedAt, snapshot = false) {
    const stamp = timestamp(generatedAt);
    if (snapshot && (stamp === null || now-stamp > 900000 || stamp > now+5000)) return [];
    // Snapshot results describe ordering at capture, never current live order.
    return nearestFinish(rows,eventId,snapshot ? stamp : now,generatedAt);
  }
  function racePhase(event, now = Date.now()) {
    if (!event?.event_date) return 'unknown';
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {timeZone:event.timezone || 'America/Denver',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(now).map(p=>[p.type,p.value]));
    const today = `${parts.year}-${parts.month}-${parts.day}`;
    const day = String(event.event_date).slice(0,10);
    if (day > today) return 'upcoming';
    if (day < today) return 'closed';
    const start = timestamp(event.start_at);
    const clock = `${parts.hour}:${parts.minute}:${parts.second}`;
    if ((start !== null && start > now) || (start === null && event.start_time && String(event.start_time).slice(0,8) > clock)) return 'upcoming';
    if (start === null) return 'awaiting';
    return ['active','started','in_progress'].includes(String(event.course_status).toLowerCase()) ? 'racing' : 'closed';
  }
  function preferredRace(events, now = Date.now()) {
    const priority = {racing:0,awaiting:1,upcoming:2,closed:3,unknown:4};
    return [...events].sort((a,b)=> {
      const ap=racePhase(a,now), bp=racePhase(b,now);
      const phase=priority[ap]-priority[bp];
      if (phase) return phase;
      const dates=String(a.event_date+'T'+a.start_time).localeCompare(String(b.event_date+'T'+b.start_time));
      return ap==='closed' ? -dates : dates;
    })[0] || null;
  }
  function statusLabel(runner, event, now = Date.now()) {
    const status = runner?.status || 'REGISTERED';
    const phase = racePhase(event, now);
    if (status === 'DNS' && (phase === 'upcoming' || phase === 'awaiting' ||
        (phase === 'racing' && !finite(runner.chip_start_seconds)))) return 'NOT STARTED';
    return status;
  }
  function estimateExplanation(position) {
    if (position?.source !== 'ESTIMATED') return '';
    if (position.estimate_basis === 'TERRAIN_CHECKPOINT_PILOT') {
      const count = Number(position.pace_segments_used);
      const recent = position.pace_basis === 'RECENT_SEGMENTS' && Number.isInteger(count) && count > 1 && count <= 3;
      return recent
        ? `Terrain-adjusted pilot · smoothed pace from ${count} recent completed segments. Race-day accuracy is not yet validated.`
        : 'Terrain-adjusted pilot · checkpoint-average pace with limited checkpoint history. Race-day accuracy is not yet validated.';
    }
    if (position.estimate_basis === 'CHECKPOINT_PACE_CHIP') return 'Checkpoint-average estimate; not terrain-adjusted.';
    return 'Checkpoint-based estimate.';
  }
  // Presentation only: never derive a new ETA from progress, goals or the current clock.
  function nextCheckpointEstimate(row, event, {now = Date.now(), generatedAt = null, delivery = 'live_api', degraded = false} = {}) {
    const result = {checkpoint:'—',arrival:'—',remaining:'—',note:'',kind:'unavailable'};
    const unavailable = message => ({...result,arrival:message});
    if (!row) return result;
    let phase;
    try { phase = racePhase(event, now); } catch { return unavailable('Race timing unavailable'); }
    if (phase === 'upcoming' || phase === 'awaiting') return unavailable('Awaiting race start');
    if (row.status === 'FINISHED') return unavailable('Finished');
    if (row.status !== 'ON COURSE') return unavailable('No active estimate');
    if (phase !== 'racing') return unavailable(phase === 'closed' ? 'Race closed' : 'Race timing unavailable');
    const index = finite(row.last_split_index) ? Number(row.last_split_index) : null;
    const names = event?.split_names;
    if (row.event_id !== event?.id || !Number.isInteger(index) || index < 0 || !Array.isArray(names) || index + 1 >= names.length || typeof names[index+1] !== 'string' || !names[index+1].trim()) return unavailable('Not enough timing data');
    result.checkpoint = names[index+1];
    const snapshot = delivery === 'periodic_snapshot';
    const stamp = timestamp(generatedAt);
    if (degraded || row.upstream_stale || event.upstream_stale || row.rank_exclusion === 'UPSTREAM_STALE' || row.freshness === 'STALE' || stamp === null || stamp <= 0 || stamp > now+5000 || now-stamp > (snapshot ? 900000 : FRESH_SECONDS*1000)) return unavailable('Estimate paused · stale feed');
    const awaiting = () => ({...result,arrival:'Awaiting checkpoint read',note:'No newer recorded passage; not a confirmed arrival.',kind:'awaiting'});
    if (row.estimate_overdue || row.estimate_held) return awaiting();
    if (index === 0 || row.rank_eligible !== true || row.rank_exclusion || !['GPS','ESTIMATED'].includes(row.source)) return unavailable('Not enough timing data');
    if (row.source === 'GPS' && refreshPosition(row,snapshot ? stamp : now).freshness !== 'LIVE') return unavailable('Estimate paused · stale GPS');
    // Require an explicit timezone; never interpret an arrival in the spectator's local zone.
    const raw = row.next_checkpoint_at;
    const arrival = passageTimestamp(raw);
    if (arrival === null) return unavailable('Not enough timing data');
    if (arrival <= now) return awaiting();
    const seconds = (arrival - (snapshot ? stamp : now)) / 1000;
    const minutes = Math.max(1, Math.round(seconds/60));
    const duration = seconds < 60 ? 'Less than 1 min' : minutes < 60 ? `About ${minutes} min` : `About ${Math.floor(minutes/60)} hr${minutes%60 ? ` ${minutes%60} min` : ''}`;
    const clock = new Intl.DateTimeFormat('en-US',{timeZone:'America/Denver',hour:'numeric',minute:'2-digit'}).format(arrival);
    const day = time => new Intl.DateTimeFormat('en-US',{timeZone:'America/Denver',month:'short',day:'numeric'}).format(time);
    const date = day(arrival) === day(now) ? '' : ` · ${day(arrival)}`;
    return {...result,arrival:`~${clock} MT${date}`,remaining:duration+(snapshot ? ' at snapshot' : ''),
      note:snapshot ? 'Snapshot pace estimate · not live; not a recorded passage.' : 'Pace/terrain pilot estimate · not a recorded passage.',kind:snapshot ? 'snapshot' : 'estimate'};
  }
  // Validate civil fields before Date.parse can normalize impossible dates.
  function passageTimestamp(value) {
    const parts = typeof value === 'string' && /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value);
    if (!parts) return null;
    const [year,month,day,hour,minute,second] = parts.slice(1,7).map(Number);
    const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const days = [31,leap ? 29 : 28,31,30,31,30,31,31,30,31,30,31];
    if (month < 1 || month > 12 || day < 1 || day > days[month-1] || hour > 23 || minute > 59 || second > 59 || Number(parts[7] || 0) > 23 || Number(parts[8] || 0) > 59) return null;
    return timestamp(value);
  }
  // Official timing history only. A null clock is evidence, not a virtual start.
  function recordedCheckIn(runner, event, {now = Date.now(), generatedAt = null, delivery = 'live_api', degraded = false} = {}) {
    const names = Array.isArray(event?.split_names) ? event.split_names : [];
    const rows = Array.isArray(runner?.checkpoint_passages) ? runner.checkpoint_passages : [];
    const reads = new Map(), seen = new Set(), duplicates = new Set();
    const snapshot = delivery === 'periodic_snapshot', stamp = timestamp(generatedAt);
    const cutoff = snapshot && stamp !== null && stamp > 0 && stamp <= now+5000 ? Math.min(now,stamp) : now;
    for (const row of rows) {
      const index = row?.split_index;
      if (!Number.isInteger(index) || index < 0 || index >= names.length) continue;
      if (seen.has(index)) duplicates.add(index);
      seen.add(index);
      const elapsed = row.elapsed_seconds;
      if (typeof names[index] !== 'string' || !names[index].trim() || typeof elapsed !== 'number' || !Number.isFinite(elapsed) || elapsed < 0 || elapsed > 31536000 || (index === 0 ? elapsed !== 0 : elapsed === 0)) continue;
      const when = passageTimestamp(row.passed_at);
      if (row.passed_at != null && (when === null || when > cutoff)) continue;
      reads.set(index,{index,name:names[index],elapsed,when});
    }
    duplicates.forEach(index => reads.delete(index));
    const latest = [...reads.values()].sort((a,b)=>b.index-a.index)[0] || null;
    const stale = Boolean(degraded || runner?.checkpoint_passages_stale || runner?.upstream_stale || event?.upstream_stale || stamp === null || stamp <= 0 || stamp > now+5000 || now-stamp > (snapshot ? 900000 : FRESH_SECONDS*1000));
    const source = [snapshot ? 'Dated snapshot · not live.' : 'Official timing reads.', stale ? 'Timing feed stale · history may be incomplete.' : '', !Array.isArray(runner?.checkpoint_passages) ? 'Checkpoint history unavailable in this feed.' : runner.checkpoint_passages_status === 'unavailable' ? 'No recorded checkpoint history available.' : runner.checkpoint_passages_status !== 'recorded' ? 'Partial history: missing reads are not inferred.' : ''].filter(Boolean).join(' ');
    const clock = latest?.when != null ? `${new Intl.DateTimeFormat('en-US',{timeZone:'America/Denver',hour:'numeric',minute:'2-digit',second:'2-digit'}).format(latest.when)} MT · ${new Intl.DateTimeFormat('en-US',{timeZone:'America/Denver',month:'short',day:'numeric',year:'numeric'}).format(latest.when)}` : 'Clock time unavailable';
    return {reads,latest,clock,ageSeconds:latest?.when != null ? Math.max(0,(now-latest.when)/1000) : null,source,stale};
  }
  // A schedule is atomic: never interpolate missing checkpoints or advance reads.
  function checkpointTimeline(row, event, options = {}) {
    const now = options.now ?? Date.now();
    const history = options.historyRunner ?? row;
    const evidence = recordedCheckIn(history, event, {...options, now});
    const names = Array.isArray(event?.split_names) ? event.split_names : [];
    const timingRow = history?.status && history.status !== 'ON COURSE' ? {...row,status:history.status} : row;
    let next = nextCheckpointEstimate(timingRow, event, {...options, now});
    const last = row?.last_split_index;
    const validLast = Number.isInteger(last) && last >= 0 && last < names.length && row?.event_id === event?.id;
    const consistent = validLast && (!evidence.latest || evidence.latest.index === last);
    const currentSegment = validLast && consistent && row.status === 'ON COURSE' && last + 1 < names.length && typeof names[last] === 'string' && typeof names[last+1] === 'string'
      ? `${names[last]} → ${names[last+1]}` : '—';
    const schedule = row?.checkpoint_forecasts;
    const absent = schedule === undefined || (Array.isArray(schedule) && schedule.length === 0);
    let valid = !absent && Array.isArray(schedule) && validLast && evidence.latest?.index === last && history?.event_id === event?.id &&
      schedule.length === names.length-last-1 && schedule.length > 0 &&
      ['TERRAIN_CHECKPOINT_PILOT','CHECKPOINT_PACE_CHIP'].includes(row.checkpoint_forecast_basis) && row.checkpoint_forecast_basis === row.eta_basis;
    let previous = evidence.latest?.when ?? -Infinity;
    if (valid) for (let offset = 0; offset < schedule.length; offset++) {
      const item = schedule[offset], index = last+1+offset, when = passageTimestamp(item?.estimated_at);
      if (!Number.isInteger(item?.split_index) || item.split_index !== index || typeof names[index] !== 'string' || !names[index].trim() ||
          when === null || when <= previous || when <= now || (offset === 0 && when !== passageTimestamp(row.next_checkpoint_at)) ||
          (offset === schedule.length-1 && when !== passageTimestamp(row.eta_at))) { valid = false; break; }
      previous = when;
    }
    // Awaiting is stronger than malformed: an elapsed next arrival is never passage.
    if ((evidence.stale || !consistent || (!absent && !valid)) && ['estimate','snapshot'].includes(next.kind)) {
      next = {...next,kind:'unavailable',arrival:evidence.stale ? 'Estimate paused · stale feed' : 'Forecast unavailable',remaining:'—',note:'No supported checkpoint schedule; no missing times are inferred.'};
    }
    const forecastAvailable = Boolean(valid && !evidence.stale && ['estimate','snapshot'].includes(next.kind));
    const clock = new Intl.DateTimeFormat('en-US',{timeZone:'America/Denver',hour:'numeric',minute:'2-digit'});
    const date = new Intl.DateTimeFormat('en-US',{timeZone:'America/Denver',month:'short',day:'numeric',year:'numeric'});
    const rows = names.map((name,index) => {
      const read = evidence.reads.get(index), isNext = consistent && index === last+1 && row?.status === 'ON COURSE';
      if (read) return {...read,kind:'recorded',isNext:false};
      if (forecastAvailable && index > last) {
        const when = passageTimestamp(schedule[index-last-1].estimated_at);
        return {index,name,isNext,kind:'forecast',arrival:`~${clock.format(when)} MT`,date:date.format(when),qualifier:isNext ? 'Next checkpoint' : 'Less certain'};
      }
      return {index,name,isNext,kind:isNext && next.kind === 'awaiting' ? 'awaiting' : validLast && index > last ? 'unavailable' : 'missing'};
    });
    const source = [evidence.source,next.kind === 'awaiting' ? 'Awaiting checkpoint read · estimate held; no confirmed arrival.' : '',forecastAvailable ? `${next.note} Later forecasts are less certain.` : `Future forecasts unavailable. ${next.kind === 'unavailable' ? next.arrival + '. ' : ''}Missing times are not inferred.`].filter(Boolean).join(' ');
    return {evidence,next,currentSegment,rows,source,forecastAvailable};
  }
  function recordedCheckpointAnchor(event, passage) {
    const names = event?.split_names;
    if (!passage || !Array.isArray(names) || names[passage.index] !== passage.name || names.filter(name=>name===passage.name).length !== 1) return null;
    // Geographic splits are filtered (not timing-index aligned, notably VK).
    const points = event.course?.split_points;
    if (!Array.isArray(points)) return null;
    const matches = points.filter(point=>point && typeof point === 'object' && !Array.isArray(point) && typeof point.name === 'string' && point.name===passage.name);
    if (matches.length !== 1) return null;
    const {lat,lng} = matches[0];
    return typeof lat === 'number' && Number.isFinite(lat) && Math.abs(lat)<=90 && typeof lng === 'number' && Number.isFinite(lng) && Math.abs(lng)<=180 ? {lat,lng} : null;
  }
  // Tracks arrive as immutable course payloads. Cache only geometry, never runners.
  const directionTracks = new WeakMap();
  const coordinatePoint = point => point && !Array.isArray(point) && Number.isFinite(point.lat) && Math.abs(point.lat) <= 90 && Number.isFinite(point.lng) && Math.abs(point.lng) <= 180;
  function directionGeometry(track) {
    if (!Array.isArray(track) || track.length < 2) return null;
    const segments = [], radians = degrees => degrees * Math.PI / 180;
    let total = 0;
    for (let index = 0; index < track.length; index++) {
      const right = track[index];
      // Never bridge a malformed sample; polar/antimeridian routes fail closed.
      if (!coordinatePoint(right) || Math.abs(right.lat) > 85.05112878) return null;
      if (!index) continue;
      const left = track[index - 1];
      if (Math.abs(right.lng - left.lng) >= 180) return null;
      const lat1 = radians(left.lat), lat2 = radians(right.lat), dx = radians(right.lng - left.lng);
      // Match finish_metrics.prepare_route's horizontal great-circle progress.
      const h = Math.sin((lat2 - lat1) / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dx / 2) ** 2;
      const length = 6371000 * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
      if (length === 0) continue;
      total += length;
      // Bearing follows the displayed Leaflet/Web Mercator segment, not motion.
      const dy = Math.asinh(Math.tan(lat2)) - Math.asinh(Math.tan(lat1));
      const bearing = (Math.atan2(dx, dy) * 180 / Math.PI + 360) % 360;
      if (!Number.isFinite(total) || !Number.isFinite(bearing)) return null;
      segments.push({end:total, bearing});
    }
    return total > 0 ? {segments,total} : null;
  }
  function courseDirection(event, position) {
    const p = position?.progress;
    if (event?.id == null || position?.event_id !== event.id || position.status !== 'ON COURSE' || !coordinatePoint(position) ||
        !Number.isFinite(p) || p < 0 || p >= 1 ||
        !((position.source === 'GPS' && position.distance_source === 'GPS_MATCHED') || (position.source === 'ESTIMATED' && position.distance_source === 'SPLIT_ESTIMATE')) ||
        ['GPS_AMBIGUOUS','GPS_OFF_ROUTE','GPS_INACCURATE','ROUTE_UNAVAILABLE'].includes(position.rank_exclusion)) return null;
    const track = event.course?.track_points;
    if (!Array.isArray(track)) return null;
    if (!directionTracks.has(track)) directionTracks.set(track, directionGeometry(track));
    const geometry = directionTracks.get(track);
    if (!geometry) return null;
    const {segments,total} = geometry;
    let low = 0, high = segments.length - 1;
    // Upper bound selects the outgoing leg at a bend (including duplicate points).
    // Allow only floating-point roundoff at boundaries, not look-ahead distance.
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (segments[middle].end / total <= p + 8 * Number.EPSILON) low = middle + 1;
      else high = middle;
    }
    return segments[low].bearing;
  }
  return Object.freeze({finite,timestamp,refreshPosition,nearestFinish,finishView,racePhase,preferredRace,statusLabel,estimateExplanation,nextCheckpointEstimate,checkpointTimeline,recordedCheckIn,recordedCheckpointAnchor,courseDirection,FRESH_SECONDS});
});
