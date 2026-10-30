import assert from 'node:assert/strict';
import test from 'node:test';
import {selectRows, tableRows, filterMovements} from '../web/forecast/views.mjs';

const row=(id, nation, br, score, extra={})=>({lineup_key:id,nation,actual_br:br,score,rankable:true,evidence_coverage:1,members:[{vehicle_id:id}],...extra});

test('BR and nation groups are selected before parent de-duplication',()=>{
  const low=row('same','a',9.3,40), high=row('same','a',9.7,80), other=row('b','b',9.3,50);
  assert.deepEqual(new Set(selectRows([low,high,other],'br').map(r=>r.actual_br)),new Set([9.3,9.7]));
  assert.deepEqual(new Set(selectRows([low,high,other],'nation').map(r=>r.nation)),new Set(['a','b']));
  assert.equal(selectRows([low,high],'global').length,1);
});

test('category alternatives and uptier utility are independent of the ordinary shortlist',()=>{
  const ordinary=row('ordinary','a',9.3,90,{composition:'combined_arms',uptier_lower_utility:10});
  const reserve=row('reserve','a',9.3,50,{composition:'ground_only',uptier_lower_utility:45});
  const table={candidates:[ordinary],category_candidates:{uptier:[reserve],ground_only:[reserve]}};
  assert.deepEqual(tableRows(table,'uptier'),[reserve]);
  assert.equal(selectRows([ordinary,reserve],'uptier')[0].lineup_key,'reserve');
  assert.deepEqual(selectRows([ordinary,reserve],'ground_only'),[reserve]);
  assert.deepEqual(tableRows({...table,budget_exhausted:true},'ground_only'),[]);
  assert.deepEqual(selectRows([{...reserve,score:null}],'br'),[]);
});

test('migration filters retain the selected slots, acquisition, version BR and direction',()=>{
  const base={nation:'a',slots:5,acquisition:'researchable',before:{actual_br:9.3},after_fixed:{actual_br:9.7},fixed_lineup_delta:-4};
  const rows=[base,{...base,slots:8},{...base,acquisition:'all'},{...base,nation:'b'},{...base,fixed_lineup_delta:8}];
  const query={nation:'a',board:'5/researchable',br:'9.7',version:'planned',direction:'falling'};
  assert.deepEqual(filterMovements(rows,query),[base]);
  assert.deepEqual(filterMovements(rows,{...query,version:'current'}),[]);
  assert.equal(filterMovements(rows,{...query,version:'current',br:'9.3'}).length,1);
});

test('declines and removed configurations are not hidden by a rising top-100 limit',()=>{
  const base={nation:'a',slots:5,acquisition:'all',before:{actual_br:9.3}};
  const rises=Array.from({length:105},(_,i)=>({...base,fixed_lineup_delta:i+1}));
  const decline={...base,fixed_lineup_delta:-5},removed={...base,status:'configuration_removed_or_illegal'};
  const rows=[...rises,decline,removed],q={board:'5/all',version:'planned',br:'9.3'};
  assert.deepEqual(filterMovements(rows,{...q,direction:'falling'}),[decline]);
  assert.deepEqual(filterMovements(rows,{...q,direction:'unavailable'}),[removed]);
  assert.equal(filterMovements(rows,q).length,107);
});
