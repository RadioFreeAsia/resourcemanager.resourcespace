import hashlib
import json
import requests
import urllib.parse
from PIL import Image
from plone import api
from plone.namedfile.file import NamedBlobImage
from Products.Five.browser import BrowserView
from zope.schema import ValidationError

from collective.resourcemanager.browser import search


class ResourceSpaceSearch(BrowserView):
    """Search ResourceSpace
    """

    def __init__(self, context, request):
        self.context = context
        self.request = request
        reg_prefix = 'resourcemanager.resourcespace.settings.IResourceSpaceKeys'
        self.rs_url = context.portal_registry['{0}.rs_url'.format(reg_prefix)]
        self.rs_user = context.portal_registry['{0}.rs_user'.format(reg_prefix)]
        self.rs_private_key = context.portal_registry['{0}.rs_private_key'.format(reg_prefix)]
        self.image_urls = []
        self.messages = []
        self.search_context = 'rs-search'

    def query_resourcespace(self, query):
        hash = hashlib.sha256()
        user_query = 'user={0}'.format(self.rs_user) + query
        key_query = self.rs_private_key + user_query
        hash.update(key_query.encode('utf-8'))
        request_url = self.rs_url + '?' + user_query + '&sign=' + hash.hexdigest()
        exc = requests.exceptions
        try:
            response = requests.get(request_url, timeout=5)
        except (exc.ConnectTimeout, exc.ConnectionError) as e:
            self.messages.append(str(e))
            return []
        if response.status_code != 200:
            self.messages.append(response.reason)
            return []
        try:
            return response.json()
        except ValueError:
            self.messages.append('The json returned from {0} is not valid'.format(
                user_query
            ))
            return []

    def __call__(self):
        form = self.request.form
        search_term = form.get('rs_search')
        browse_term = form.get('rs_browse')
        self.search_context = self.request._steps[-1]
        if not form or not(search_term or browse_term):
            return self.template()
        # do the search based on term or collection name
        if search_term:
            search_term = urllib.parse.quote_plus(form['rs_search'])
        else:
            search_term = urllib.parse.quote_plus('!' + browse_term)
        query = '&function=do_search&param1={0}&param2=1'.format(
            search_term
        )
        response = self.query_resourcespace(query)
        self.image_metadata = {x['ref']: x for x in response}
        self.num_results = len(response)
        media_ids = [x['ref'] for x in response[:100]]
        # build new query to return image urls
        query2 = '&function=get_resource_path&param1=%5B{0}%5D&param2=false&param3=scr'.format(
            ','.join(media_ids)
        )
        self.image_urls = self.query_resourcespace(query2)
        if not self.image_urls and not self.messages:
            self.messages.append("No images found")
        existing = []
        if self.context.portal_type == 'Folder':
            existing = search.existing_copies(self.context)
        for item in self.image_metadata:
            self.image_metadata[item]['url'] = self.image_urls.get(item)
            self.image_metadata[item]['exists'] = self.image_urls.get(item) in existing
        if form.get('type', '') == 'json':
            return json.dumps({
                'search_context': self.search_context,
                'errors': self.messages,
                'metadata': self.image_metadata,
                'urls': self.image_urls,
                })
        return self.template()

    def collections(self):
        query = '&function=search_public_collections&param2=name&param3=ASC&param4=0'
        response = self.query_resourcespace(query)
        return response


class ResourceSpaceCopy(BrowserView):
    """Copy selected media to the current folder
    """

    def __init__(self, context, request):
        self.context = context
        self.request = request
        self.rssearch = ResourceSpaceSearch(context, request)

    def __call__(self):
        img_url = self.request.form.get('image')
        if not img_url:
            return "Image ID not found"
        img_response = requests.get(img_url)
        try:
            Image.open(requests.get(img_url, stream=True).raw)
        except OSError as e:
            raise ValidationError(
                '{}\n ResourceSpace url may be invalid'.format(e))
        blob = NamedBlobImage(
            data=img_response.content)
        query = '&function=get_resource_field_data&param1={0}'.format(
            self.request.form.get('id')
        )
        response = self.rssearch.query_resourcespace(query)
        img_metadata = {x['title']: x['value'] for x in response}
        new_image = api.content.create(
            type='Image',
            image=blob,
            container=self.context,
            title=self.request.form.get('title'),
            external_url=img_url,
            description=str(img_metadata),
        )
        return "Image copied to {}".format(new_image.absolute_url())
