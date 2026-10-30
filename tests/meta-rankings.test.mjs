import test from 'node:test';
import assert from 'node:assert/strict';
import {buildMetaRankings,compositionKey} from '../shared/meta-rankings.mjs';

const options = {
  global_limit:100,per_nation_br_limit:3,per_br_limit:10,per_nation_limit:25,
  mover_limit:50,coverage_review_threshold:0.5,interval_review_threshold:0.35,
  diversity_max_jaccard:0.65,
};
const candidate = (ids,br,score,extra={}) => ({
  lineup_ids:ids, actual_br:br, candidate_cap:br,score,lower:score-0.08,
  upper:score+0.08,coverage:0.9,metrics:{same_tier:score,up_tier:score-0.05},
  valid:true, ...extra,
});
const recommendation = (nation,version,candidates)=>({nation,version,candidates});
const make = (current,planned,impacts=[])=>buildMetaRankings({
  current,planned,impacts,model_version:'fixture-v2',calibration_status:'uncalibrated',
  scenario_status:'proposed',snapshot_id:'fixture',options,
});

test('META01: all nations and all BRs are ranked; not just one best per nation',()=>{
  const planned=[
    recommendation('usa','planned',[candidate(['a','b'],7.0,0.7),candidate(['c','d'],9.3,0.95)]),
    recommendation('germany','planned',[candidate(['e','f'],8.7,0.82)]),
  ];
  const result=make([],planned);
  assert.deepEqual(result.planned.global.map(r=>[r.nation,r.br,r.score]),
    [['usa',9.3,0.95],['germany',8.7,0.82],['usa',7.0,0.7]]);
  assert.deepEqual(Object.keys(result.planned.by_br),['7.0','8.7','9.3']);
  assert.equal(result.global_optimum_guaranteed,false);
  assert.equal(result.global_is_empirical_win_rate,false);
});

test('META02: duplicate composition is collapsed even if reversed and found under multiple profiles',()=>{
  const result=make([],[
    recommendation('usa','planned',[candidate(['a','b'],9.7,0.85,{
      alternatives_at_br:[candidate(['b','a'],9.7,0.72)]})]),
  ]);
  assert.equal(result.planned.candidate_count,1);
  assert.equal(result.planned.global[0].score,0.85);
});

test('META03: rating configuration and loadout are separate composition identities',()=>{
  const low=candidate(['tank','plane'],10.0,0.80,{selected_rating_configurations:{plane:'low'},
    loadout_choices:{plane:'bombs'}});
  const high=candidate(['tank','plane'],11.0,0.86,{selected_rating_configurations:{plane:'high'},
    loadout_choices:{plane:'missiles'}});
  assert.notEqual(compositionKey(low,'usa'),compositionKey(high,'usa'));
  assert.equal(make([], [recommendation('usa','planned',[low,high])]).planned.candidate_count,2);
});

test('META04: no fabricated ranking from invalid, unknown or non-finite candidates',()=>{
  const result=make([], [recommendation('usa','planned',[
    candidate(['a'],10,null),candidate(['b'],10,NaN),candidate(['c'],10,0.9,{valid:false})])]);
  assert.equal(result.planned.candidate_count,0);
  assert.deepEqual(result.planned.global,[]);
});

test('META05: patch movements compare carried SAME lineup, not a hypothetical rank gain',()=>{
  const impact=[
    {nation:'usa',br:10,previous_score:0.8,carried_score:0.69,optimized_score:0.92,
      previous_actual_br:10,carried_actual_br:10.3,previous_lineup_ids:['a','b'],
      optimized_lineup_ids:['a','c'],member_changes:[{vehicle_id:'b'}],entered:['f'],left:[],simulated:true},
    {nation:'germany',br:9,previous_score:0.65,carried_score:0.73,optimized_score:0.74,
      previous_lineup_ids:['d','e'],optimized_lineup_ids:['d','f'],member_changes:[],
      entered:['g'],left:[],simulated:true},
    {nation:'italy',br:10.3,previous_score:null,carried_score:null,optimized_score:0.80,
      newly_valid_cap:true,simulated:true},
  ];
  const result=make([],[],impact);
  assert.equal(result.movements.falling[0].classification,'directly_modified');
  assert.ok(Math.abs(result.movements.falling[0].fixed_lineup_delta+0.11)<1e-9);
  assert.equal(result.movements.rising[0].classification,'indirect_matchmaking_change');
  assert.equal(result.movements.rising[0].original_ids[0],'d');
  assert.equal(result.movements.new_caps.length,1);
  assert.equal(result.movements.recoveries[0].adaptation_gain,0.23);
});

test('META06: low evidence is flagged, not silently invented or counted as zero',()=>{
  const result=make([], [recommendation('usa','planned',
    [candidate(['a','b'],9.0,0.7,{coverage:0.15,lower:0.05,upper:0.94})])]);
  assert.deepEqual(result.planned.global[0].quality_flags,['low_evidence','wide_uncertainty']);
  assert.equal(result.planned.global[0].coverage,0.15);
  assert.equal(result.calibration_status,'uncalibrated');
});

test('META07: future unimplemented patch stays explicitly proposed',()=>{
  const result=make([],[]);
  assert.equal(result.scenario_is_released,false);
  assert.equal(result.scenario_status,'proposed');
  assert.equal(result.country_and_BR_normalization_status,'not_calibrated_cross_BR_provisional');
});

test('META08: diversification avoids nearly identical entries flooding each nation/BR leaderboard',()=>{
  const result=make([], [recommendation('usa','planned',[
    candidate(['a','b','c','d','e'],9.0,0.90,{
      alternatives_at_br:[
        candidate(['a','b','c','d','f'],9.0,0.88),
        candidate(['g','h','i','j','k'],9.0,0.85),
      ]}),
  ])]);
  assert.equal(result.planned.candidate_count,3);
  assert.deepEqual(result.planned.global.map(x=>x.vehicle_ids),
    [['a','b','c','d','e'],['g','h','i','j','k']]);
});
