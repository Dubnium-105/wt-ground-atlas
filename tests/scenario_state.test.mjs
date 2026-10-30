import test from 'node:test';
import assert from 'node:assert/strict';
import {hasFutureScenario} from '../web/scenario-state.mjs';

test('empty and released changes never become a future version',()=>{
  assert.equal(hasFutureScenario(null),false);
  assert.equal(hasFutureScenario({status:'announced',entries:[]}),false);
  for(const status of ['released','unknown','unavailable','not_imported'])
    assert.equal(hasFutureScenario({status,entries:[{vehicle_id:'fixture'}]}),false);
});
test('independent announced and local scenarios remain editable',()=>{
  for(const status of ['announced','proposed','hypothetical','simulated','local_simulation','acceptance_fixture_simulation'])
    assert.equal(hasFutureScenario({status,entries:[{vehicle_id:'fixture'}]}),true);
});
