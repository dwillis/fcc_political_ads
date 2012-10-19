from django.conf import settings
from django.conf.urls import url
from django.core.urlresolvers import NoReverseMatch
from django.core.paginator import InvalidPage
from django.http import Http404

from tastypie import fields
from tastypie.http import HttpBadRequest
from tastypie.exceptions import BadRequest
from tastypie.resources import ModelResource, Bundle
from tastypie.cache import SimpleCache
from tastypie.utils import trailing_slash
from tastypie.paginator import Paginator

from fccpublicfiles.models import PoliticalBuy
from haystack.query import SearchQuerySet

from dateutil import parser

uuid_re_str = r'(?P<uuid_key>[a-f0-9-]{32,36})'

API_NAME = 'v1'

API_MAX_RESULTS_PER_PAGE = getattr(settings, 'API_MAX_RESULTS_PER_PAGE', 500)
API_LIMIT_PER_PAGE = getattr(settings, 'API_LIMIT_PER_PAGE', 20)

# Made methods that return lists of objects respect a max_limit meta option.


class PoliticalFileResource(ModelResource):
    class Meta:
        queryset = PoliticalBuy.objects.all()
        limit = API_LIMIT_PER_PAGE
        max_limit = API_MAX_RESULTS_PER_PAGE
        resource_name = 'politicalfile'
        api_name = API_NAME
        allowed_methods = ['get']
        cache = SimpleCache()
        paginator_class = Paginator
        fields = ('uuid_key', 'advertiser', 'contract_number', \
                  'contract_start_date', 'contract_end_date', \
                  'nielsen_dma', 'community_state', \
                  'candidate_type', 'upload_time', 'updated_at')
        ordering = ('updated_at', 'contract_start_date', 'contract_end_date')
        filtering = {
            "nielsen_dma_id": ('exact',),
            "community_state": ('exact',),
            "contract_start_date": ('exact', 'lte', 'gte'),
            "contract_end_date": ('exact', 'lte', 'gte'),
        }

    description = fields.CharField(help_text='Description calculated from parsed and entered data')
    nielsen_dma_id = fields.IntegerField(attribute='dma_id', null=True, blank=True)
    advertiser = fields.CharField(help_text='Advertiser name: should be a political entity (most likely a committee)')
    broadcasters = fields.ListField(help_text='List of broadcaster station callsigns')
    doc_status = fields.CharField()
    total_spent = fields.DecimalField()
    num_spots = fields.IntegerField(attribute='num_spots_raw', null=True, blank=True)
    doc_source = fields.CharField()

    def dehydrate_description(self, bundle):
        return bundle.obj.name()

    def dehydrate_advertiser(self, bundle):
        return bundle.obj.advertiser or None

    def dehydrate_contract_number(self, bundle):
        return bundle.obj.contract_number or None

    def dehydrate_broadcasters(self, bundle):
        return bundle.obj.broadcasters_callsign_list()

    def dehydrate_doc_status(self, bundle):
        return bundle.obj.doc_status()

    def dehydrate_total_spent(self, bundle):
        return bundle.obj.total_spent()

    def dehydrate_doc_source(self, bundle):
        return bundle.obj.doc_source()

    def base_urls(self):
        """
        ***
        Override:
            - exclude pk resources
        ***
        "The standard URLs this ``Resource`` should respond to."
        """
        # Due to the way Django parses URLs, ``get_multiple`` won't work without
        # a trailing slash.
        return [
            url(r"^(?P<resource_name>{0}){1}$".format(self._meta.resource_name, trailing_slash()), self.wrap_view('dispatch_list'), name="api_dispatch_list"),
            url(r"^(?P<resource_name>{0})/{1}{2}$".format(self._meta.resource_name, uuid_re_str, trailing_slash()), self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
            url(r"^(?P<resource_name>{0})/schema{1}$".format(self._meta.resource_name, trailing_slash()), self.wrap_view('get_schema'), name="api_get_schema"),
        ]

    def get_resource_uri(self, bundle_or_obj):
        kwargs = {
            'resource_name': self._meta.resource_name,
            'api_name': self._meta.api_name
        }
        if isinstance(bundle_or_obj, Bundle):
            kwargs['uuid_key'] = bundle_or_obj.obj.uuid_key
        else:
            kwargs['uuid_key'] = bundle_or_obj.uuid_key
        return self._build_reverse_url("api_dispatch_detail", kwargs=kwargs)

    def get_list(self, request, **kwargs):
        """
        ***
        Override:
            - Implements max_limit
        ***
        Returns a serialized list of resources.

        Calls ``obj_get_list`` to provide the data, then handles that result
        set and serializes it.

        Should return a HttpResponse (200 OK).
        """
        # TODO: Uncached for now. Invalidation that works for everyone may be
        #       impossible.
        objects = self.obj_get_list(request=request, **self.remove_api_resource_names(kwargs))
        sorted_objects = self.apply_sorting(objects, options=request.GET)

        req_data = request.GET.copy()
        limit = int(req_data.get('limit', self._meta.limit))
        if limit > self._meta.max_limit:
            limit = self._meta.max_limit
            req_data['limit'] = unicode(limit)
        paginator = self._meta.paginator_class(req_data, sorted_objects, resource_uri=self.get_resource_list_uri(), limit=limit)
        to_be_serialized = paginator.page()

        # Dehydrate the bundles in preparation for serialization.
        bundles = [self.build_bundle(obj=obj, request=request) for obj in to_be_serialized['objects']]
        to_be_serialized['objects'] = [self.full_dehydrate(bundle) for bundle in bundles]
        to_be_serialized = self.alter_list_data_to_serialize(request, to_be_serialized)
        return self.create_response(request, to_be_serialized)

    def build_filters(self, filters=None):
        if filters is None:
            filters = {}

        orm_filters = super(PoliticalFileResource, self).build_filters(filters)

        if "q" in filters:
            sqs = SearchQuerySet().auto_query(filters['q'])
            orm_filters["pk__in"] = [i.pk for i in sqs]

        return orm_filters