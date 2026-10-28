"""Outbound read-only HTTP client. Source addresses never come from client text."""
import asyncio
from urllib.parse import urlsplit
import httpx
from pydantic import SecretStr, ValidationError
from contracts.models import PolicyQuery, PolicyResult
from contracts.inventory import InventoryQuery, InventoryResult
from contracts.reference import (
    DeliveryQuery, DeliveryResult, HoursQuery, HoursResult, PromotionQuery, PromotionResult,
    StoreCatalogResult,
)
from .sources import SourceFailure


class SourceAPI:
    def __init__(self, base_url='http://127.0.0.1:8767', api_key='', *, transport=None, timeout=1.0):
        url=urlsplit(base_url)
        if url.username or url.password or url.query or url.fragment or not url.hostname:
            raise ValueError('Source URL must not contain credentials, query or fragment')
        local=url.hostname in ('localhost','127.0.0.1','::1')
        if url.scheme != 'https' and not (url.scheme=='http' and local):
            raise ValueError('HTTPS required except literal localhost')
        self.base_url=base_url.rstrip('/')
        self._key=SecretStr(api_key)
        self.transport=transport
        self.timeout=timeout

    def _post(self,path,body,model):
        return asyncio.run(self._post_async(path,body,model))

    async def _post_async(self,path,body,model):
        headers={}
        if self._key.get_secret_value():
            headers['Authorization']='Bearer '+self._key.get_secret_value()
        try:
            async with asyncio.timeout(self.timeout):
                async with httpx.AsyncClient(transport=self.transport,timeout=self.timeout,
                                            trust_env=False,follow_redirects=False) as client:
                    async with client.stream('POST',self.base_url+path,json=body,headers=headers) as response:
                        if response.status_code!=200:
                            raise SourceFailure('source_unavailable')
                        data=bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data)>65536: raise SourceFailure('source_invalid_response')
            return model.model_validate_json(bytes(data))
        except SourceFailure:
            raise
        except (httpx.TimeoutException,TimeoutError):
            raise SourceFailure('source_timeout') from None
        except httpx.HTTPError:
            raise SourceFailure('source_unavailable') from None
        except (ValidationError,ValueError,TypeError):
            raise SourceFailure('source_invalid_response') from None

    def _require_fresh(self, source, now):
        checked_at=now() if callable(now) else now
        if not source.as_of <= checked_at <= source.valid_until:
            raise SourceFailure('source_stale')
        # This runnable demo must not present real data as synthetic.
        if not source.synthetic: raise SourceFailure('source_mode_mismatch')

    def inventory(self,query: InventoryQuery,now):
        result=self._post('/source/v1/inventory/lookup',query.model_dump(),InventoryResult)
        if result.status=='found':
            if result.item.sku != query.sku or result.item.warehouse_id != query.warehouse_id:
                raise SourceFailure('source_scope_mismatch')
            self._require_fresh(result.source, now)
        return result

    def stores(self, now):
        result=self._post('/source/v1/stores/catalog', {}, StoreCatalogResult)
        if result.status=='found': self._require_fresh(result.source, now)
        return result

    def hours(self, query: HoursQuery, now):
        result=self._post('/source/v1/stores/hours', query.model_dump(mode='json'), HoursResult)
        if result.status=='found':
            if result.store_id != query.store_id or result.on_date != query.on_date:
                raise SourceFailure('source_scope_mismatch')
            self._require_fresh(result.source, now)
        return result

    def promotions(self, query: PromotionQuery, now):
        result=self._post('/source/v1/promotions/list', query.model_dump(mode='json'), PromotionResult)
        if result.status=='found':
            if result.on_date != query.on_date: raise SourceFailure('source_scope_mismatch')
            self._require_fresh(result.source, now)
        return result

    def delivery(self, query: DeliveryQuery, now):
        result=self._post('/source/v1/delivery/terms', query.model_dump(), DeliveryResult)
        if result.status=='found':
            if result.topic != query.topic: raise SourceFailure('source_scope_mismatch')
            self._require_fresh(result.source, now)
        return result

    def policy(self,query: PolicyQuery):
        result=self._post('/source/v1/policies/resolve',query.model_dump(),PolicyResult)
        if result.status=='found' and result.policy.approval_state!='synthetic_demo':
            raise SourceFailure('source_mode_mismatch')
        return result
