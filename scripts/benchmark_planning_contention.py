from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import perf_counter
import json
import pytest
from fastapi.testclient import TestClient
from tests.api.test_read_performance import Repository
from tests.api.test_management_api import HEADERS
from tests.api.test_training_plan_engine import Repository as PlanRepository, profile, TODAY, NOW, reference_speed
from tests.api.test_final_audit_contract import SharedStore
from apps.api import main, dependencies, training_plan_engine as engine
from apps.api.planning_computation import compute

results=[]
with pytest.MonkeyPatch.context() as patch:
 patch.setenv('ONFLOWS_SERVICE_TOKEN','service-secret')
 patch.setattr(dependencies,'repository',lambda:Repository())
 patch.setattr(engine.model_service,'speed_view',reference_speed)
 client=TestClient(main.app)
 for count in [1,10,25]:
  repo=SharedStore(); barrier=Barrier(count+1); computations=[]
  def calculation():
   computations.append(1)
   return engine.generate_plan(PlanRepository(),'athlete',profile(),start_date=TODAY,now=NOW)
  def read(_):
   barrier.wait(); start=perf_counter()
   response=client.get('/api/v2/athlete/management/view?view=week',headers=HEADERS)
   assert response.status_code==200,response.text
   return perf_counter()-start
  def plan():
   barrier.wait()
   return compute(repo,'athlete','a'*64,calculation)
  with ThreadPoolExecutor(max_workers=count+1) as pool:
   background=pool.submit(plan)
   timings=list(pool.map(read,range(count)))
   background.result()
  p95=sorted(timings)[min(len(timings)-1,int(.95*len(timings)))]
  results.append({'concurrent_reads':count,'p95_seconds':round(p95,3),'planning_computations':len(computations)})
print(json.dumps({'environment':'local synthetic API workload with actual planner','results':results}))
