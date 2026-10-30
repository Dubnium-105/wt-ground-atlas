/** Deterministic, source-labelled GLOBAL Ground RB meta rankings.
 * Reads all nationally precomputed candidates, never requires a user lineup.
 * Model utility is not win probability; patch predictions are provisional. */
export const META_RANKING_SCHEMA = '1.0.0';
const finite = x => typeof x === 'number' && Number.isFinite(x);
const numberSort = (a,b) => finite(b.score) - finite(a.score)
  || (b.score ?? -Infinity) - (a.score ?? -Infinity)
  || (b.coverage ?? -Infinity) - (a.coverage ?? -Infinity)
  || a.key.localeCompare(b.key);
const stable = x => JSON.stringify(x, (_, v) =>
  v && typeof v === 'object' && !Array.isArray(v)
    ? Object.fromEntries(Object.keys(v).sort().map(k => [k,v[k]])) : v);
const oneDec = n => Number(n).toFixed(1);

export function compositionKey(candidate, nation) {
  const configs = candidate.selected_rating_configurations ?? {};
  const loadouts = candidate.loadout_choices ?? {};
  const members = (candidate.lineup_ids ?? []).map(id => ({
    vehicle_id: id, configuration_id: configs[id] ?? null,
    loadout_id: loadouts[id] ?? null,
  })).sort((a,b)=>a.vehicle_id.localeCompare(b.vehicle_id));
  return stable({nation, members});
}

function candidateRows(recommendations, version, review) {
  const seen = new Map();
  for (const result of recommendations ?? []) {
    if (!result || !result.nation || !Array.isArray(result.candidates)) continue;
    for (const candidate of result.candidates.flatMap(c => [c,...(c.alternatives_at_br ?? [])])) {
      if (!candidate?.lineup_ids?.length || candidate.valid === false || !finite(candidate.score)
        || !finite(candidate.actual_br ?? candidate.br)) continue;
      const key = compositionKey(candidate,result.nation);
      const members = [...candidate.lineup_ids];
      if (new Set(members).size !== members.length) continue;
      const coverage = finite(candidate.coverage) ? candidate.coverage : null;
      const intervalWidth = finite(candidate.lower) && finite(candidate.upper)
        ? Math.max(0,candidate.upper-candidate.lower) : null;
      const flags = [];
      if (coverage === null || coverage < review.coverage_review_threshold) flags.push('low_evidence');
      if (intervalWidth === null || intervalWidth > review.interval_review_threshold) flags.push('wide_uncertainty');
      const row = {
        key,nation:result.nation,version,br:candidate.actual_br ?? candidate.br,
        candidate_cap:candidate.candidate_cap ?? null,
        vehicle_ids:members,configuration_by_vehicle:candidate.selected_rating_configurations ?? {},
        loadout_by_vehicle:candidate.loadout_choices ?? {},profile:candidate.profile ?? null,
        score:candidate.score,lower:candidate.lower ?? null,upper:candidate.upper ?? null,
        coverage,metrics: {
          mean:candidate.metrics?.mean ?? null,
          same_tier:candidate.metrics?.same_tier ?? null,
          up_tier:candidate.metrics?.up_tier ?? null,
          down_tier:candidate.metrics?.down_tier ?? null,
          worst_case:candidate.metrics?.worst_case ?? null,
          air_window:candidate.metrics?.air_window ?? null,
          aa_coverage:candidate.metrics?.aa_coverage ?? null,
        },
        quality_flags:flags,
        score_semantics: 'uncalibrated_conditional_decision_utility_not_win_probability',
      };
      if (!seen.has(key) || numberSort(row,seen.get(key)) < 0) seen.set(key,row);
    }
  }
  return [...seen.values()].sort(numberSort);
}

function uniqueWithDiversity(rows, limit, perNationBR, overlapThreshold) {
  const result=[],counts = new Map();
  for (const row of rows) {
    const group = row.nation + ':' + oneDec(row.br);
    if ((counts.get(group) ?? 0) >= perNationBR) continue;
    const compared = result.filter(prev => prev.nation === row.nation && oneDec(prev.br) === oneDec(row.br));
    const redundant = compared.some(prev => {
      const a = new Set(prev.vehicle_ids), b = new Set(row.vehicle_ids);
      const common = [...a].filter(id=>b.has(id)).length;
      return common / new Set([...a,...b]).size >= overlapThreshold;
    });
    if (redundant) continue;
    result.push({...row,rank:result.length+1});
    counts.set(group,(counts.get(group)??0)+1);
    if (result.length >= limit) break;
  }
  return result;
}
function groupBy(rows, group, limit) {
  const result = {};
  for (const row of rows) {
    const id = String(group(row));
    (result[id] ??= []).push(row);
  }
  for (const id of Object.keys(result)) result[id] = result[id].slice(0,limit)
    .map((item,i)=>({...item,rank:i+1}));
  return Object.fromEntries(Object.entries(result).sort(([a],[b]) =>
    finite(Number(a)) && finite(Number(b)) ? Number(a)-Number(b) : a.localeCompare(b)));
}

function impactRows(impacts, version, limit) {
  const rows = [];
  for (const item of impacts ?? []) {
    if (item.simulated !== true || !item.nation) continue;
    const old = item.previous_score, carried = item.carried_score,
      optimized = item.optimized_score;
    const delta = finite(old) && finite(carried) ? carried-old : null;
    const adaptGain = finite(carried) && finite(optimized) ? optimized-carried : null;
    const classification = item.newly_valid_cap ? 'new_br_cap'
      : (item.member_changes ?? []).length ? 'directly_modified'
      : (item.entered?.length || item.left?.length) ? 'indirect_matchmaking_change' : 'environment_or_model_change';
    rows.push({
      nation:item.nation,original_cap:item.br,
      original_br:item.previous_actual_br ?? null,
      carried_br:item.carried_actual_br ?? null,
      original_ids:item.previous_lineup_ids ?? [],
      optimized_ids:item.optimized_lineup_ids ?? [],
      previous_score:finite(old)?old:null,
      carried_score:finite(carried)?carried:null,
      optimized_score:finite(optimized)?optimized:null,
      fixed_lineup_delta:delta,adaptation_gain:adaptGain,
      newly_valid_cap:item.newly_valid_cap === true,
      changed_member_count:item.member_changes?.length ?? 0,
      entered_count:item.entered?.length ?? 0,left_count:item.left?.length ?? 0,
      classification,version,
      compare_semantics:'old lineup with SAME membership; not rank change; changed opponent field is included',
    });
  }
  const compare = (a,b) => (b.fixed_lineup_delta ?? -Infinity) - (a.fixed_lineup_delta ?? -Infinity)
    || a.nation.localeCompare(b.nation) || a.original_cap-b.original_cap;
  const measured = rows.filter(row=>finite(row.fixed_lineup_delta));
  const rising = measured.slice().sort(compare).filter(row=>row.fixed_lineup_delta>0).slice(0,limit);
  const falling = measured.slice().sort((a,b)=>a.fixed_lineup_delta-b.fixed_lineup_delta
    || a.nation.localeCompare(b.nation) || a.original_cap-b.original_cap)
    .filter(row=>row.fixed_lineup_delta<0).slice(0,limit);
  const recoveries = rows.filter(row=>finite(row.adaptation_gain)&&row.adaptation_gain>0)
    .sort((a,b)=>b.adaptation_gain-a.adaptation_gain||a.nation.localeCompare(b.nation)).slice(0,limit);
  return {rising,falling,recoveries,new_caps:rows.filter(row=>row.newly_valid_cap).slice(0,limit)};
}

/** Current/planned candidates are national recommendation results from the *same*
 * scoring engine. Cross-BR ranks are conditional, NOT empirical strength claims. */
export function buildMetaRankings({
  current = [],planned = [],impacts = [],
  model_version = null,calibration_status = 'uncalibrated',
  snapshot_id = null,scenario_status = 'unknown',
  options = {},
} = {}) {
  const limits = {
    global_limit:options.global_limit,
    per_nation_br_limit:options.per_nation_br_limit,
    per_br_limit:options.per_br_limit,
    per_nation_limit:options.per_nation_limit,
    mover_limit:options.mover_limit,
    coverage_review_threshold:options.coverage_review_threshold,
    interval_review_threshold:options.interval_review_threshold,
    diversity_max_jaccard:options.diversity_max_jaccard,
  };
  for (const [name,n] of Object.entries(limits)) {
    if (!finite(n) || n < 0 || (name.endsWith('_limit') && !Number.isInteger(n))
      || (name.startsWith('diversity_') && n>1) || (name.endsWith('_threshold')&&n>1)) {
      throw new Error('Invalid meta ranking setting: '+name);
    }
  }
  const rank = (recommendations,version) => {
    const rows = candidateRows(recommendations,version,limits);
    return {
      candidate_count:rows.length,
      global:uniqueWithDiversity(rows,limits.global_limit,limits.per_nation_br_limit,limits.diversity_max_jaccard),
      by_br:groupBy(rows,row=>oneDec(row.br),limits.per_br_limit),
      by_nation:groupBy(rows,row=>row.nation,limits.per_nation_limit),
    };
  };
  const before = rank(current,'current'),after = rank(planned,'planned');
  const beforeKeys = new Set(candidateRows(current,'current',limits).map(row=>row.key));
  const newly_selected = after.global.filter(row=>!beforeKeys.has(row.key));
  return {
    schema_version:META_RANKING_SCHEMA,kind:'next_patch_meta_rankings',
    snapshot_id,model_version,calibration_status,scenario_status,
    ranking_method:'all_nations_all_BR_candidate_utility_then_dedup_and_diversify',
    global_is_empirical_win_rate:false,
    global_optimum_guaranteed:false,
    country_and_BR_normalization_status:'not_calibrated_cross_BR_provisional',
    scenario_is_released: ['implemented','released','official_implemented'].includes(scenario_status),
    methodology_notes:[
      'Ranked lineups come from national heuristic candidate searches, not exhaustive global optimization.',
      'Global ranks compare conditional utilities across BRs; do not interpret as actual win rates.',
      'Variant availability requires official configuration and loadout rules; unknown mechanism does not become a fabricated BR.',
      'Patch movement compares the same carried lineup before and after the proposed rules, not a player skill adjusted observed win rate.',
    ],
    limits,
    current:before,planned:after,
    projected_new_candidates:newly_selected,
    movements:impactRows(impacts,'planned',limits.mover_limit),
  };
}
