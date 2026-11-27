"""Transport budgets for durable background writes, outside model identities."""
import httpx


class WorkerStoreClient(httpx.Client):
    def request(self, method, url, **kwargs):
        # This immutable bulk write has a 30-second SQL budget. Keep a network
        # margin without widening page reads, queue claims or other RPCs.
        if method.upper() == 'POST' and str(url).endswith('/rpc/stage_onflows_analysis_generation'):
            kwargs.setdefault('timeout', httpx.Timeout(35., connect=5.))
        return super().request(method, url, **kwargs)
