import httpx
import pytest

from apps.api.worker_transport import WorkerStoreClient


@pytest.mark.parametrize('method,path,read_timeout', [
    ('POST', '/rpc/stage_onflows_analysis_generation', 35.),
    ('GET', '/rpc/stage_onflows_analysis_generation', 15.),
    ('POST', '/rpc/claim_onflows_sync_job', 15.),
    ('POST', '/rpc/activate_onflows_analysis_generation', 15.),
])
def test_only_background_staging_receives_longer_transport_budget(method, path, read_timeout):
    seen = []
    def handler(request):
        seen.append(request.extensions['timeout'])
        return httpx.Response(200, json=[])
    with WorkerStoreClient(timeout=httpx.Timeout(15., connect=5.),
                           transport=httpx.MockTransport(handler)) as client:
        client.request(method, 'https://offline.invalid'+path)
    assert seen == [{'connect': 5., 'read': read_timeout, 'write': read_timeout, 'pool': read_timeout}]


def test_uncertain_stage_write_is_not_retried_by_transport():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('uncertain')
    with WorkerStoreClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.post('https://offline.invalid/rpc/stage_onflows_analysis_generation', json={})
    assert len(calls) == 1
