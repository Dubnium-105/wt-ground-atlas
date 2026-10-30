// Selection uses complete offline view shards or the requested search tables.
// The global representative limit must never become an input to another view.
export function tableRows(table, view) {
  return table.budget_exhausted ? [] : (table.category_candidates?.[view] || table.candidates).filter(r=>r.members.length===table.slots);
}

export function selectRows(rows, view) {
  rows = rows.filter(r => r.rankable && Number.isFinite(r.score));
  if (['ground_only', 'combined_arms'].includes(view)) rows = rows.filter(r => r.composition === view);
  if (view === 'uptier') rows = rows.filter(r => Number.isFinite(r.uptier_lower_utility));
  const metric = r => view === 'uptier' ? r.uptier_lower_utility : r.score;
  rows = [...rows].sort((a,b) => metric(b)-metric(a) || b.evidence_coverage-a.evidence_coverage || a.members.length-b.members.length || a.lineup_key.localeCompare(b.lineup_key));
  const seen = new Set();
  return rows.filter(r => {
    const key = view === 'br' ? r.actual_br : view === 'nation' ? r.nation : r.members.map(m => m.vehicle_id).sort().join('|');
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}

export function filterMovements(moves, {nation, br, board, version, direction='all'}) {
  const [slots, acquisition] = board.split('/');
  return moves.filter(m => {
    if (m.slots !== Number(slots) || m.acquisition !== acquisition || (nation && m.nation !== nation)) return false;
    const selected = version === 'planned' ? (m.after_fixed || m.before) : m.before;
    if (br && String(selected?.actual_br) !== br) return false;
    if (direction === 'rising') return Number.isFinite(m.fixed_lineup_delta) && m.fixed_lineup_delta > 0;
    if (direction === 'falling') return Number.isFinite(m.fixed_lineup_delta) && m.fixed_lineup_delta < 0;
    if (direction === 'unavailable') return !Number.isFinite(m.fixed_lineup_delta);
    return true;
  }).sort((a,b) => {
    const av = a.fixed_lineup_delta, bv = b.fixed_lineup_delta;
    if (!Number.isFinite(av) || !Number.isFinite(bv)) return Number.isFinite(av) ? -1 : Number.isFinite(bv) ? 1 : 0;
    return direction === 'falling' ? av-bv : bv-av;
  });
}

export function migrationSummaries(index) {
  if (Array.isArray(index)) return index; // Read a previously built snapshot.
  if (index.kind !== 'migration_index') throw new Error('不支持的版本对照索引');
  return index.tables.flatMap(({summaries, nation, slots, acquisition, ...detail}) =>
    summaries.map((summary, detail_index) => ({...summary, nation, slots, acquisition, detail, detail_index})));
}
