import hashlib
import json
import math
import requests
from requests import exceptions as exc
import urllib.parse
from PIL import Image
from plone import api
from plone.namedfile.file import NamedBlobImage
from Products.Five.browser import BrowserView

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
        self.image_metadata = []
        self.messages = []
        self.search_context = 'rs-search'

    def query_resourcespace(self, query):
        hash = hashlib.sha256()
        user_query = 'user={0}'.format(self.rs_user) + query
        key_query = self.rs_private_key + user_query
        hash.update(key_query.encode('utf-8'))
        request_url = self.rs_url + '?' + user_query + '&sign=' + hash.hexdigest()
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
        batch = int(form.get('batch'))
        b_size = 20
        b_start = (batch - 1) * b_size + 1
        b_end = b_start + b_size
        self.search_context = self.request._steps[-1]
        if not form or not(search_term or browse_term):
            return self.template()
        # do the search based on term or collection name
        if search_term:
            search_term = urllib.parse.quote_plus(form['rs_search'])
        else:
            search_term = urllib.parse.quote_plus('!' + browse_term)
        query = '&function=search_get_previews&param1={0}&param2=1&param8=pre'.format(
            search_term
        )
        response = self.query_resourcespace(query)
        num_results = len(response)
        self.image_metadata = {x['ref']: x for x in response[b_start-1:b_end-1]}
        if not self.image_metadata and not self.messages:
            self.messages.append("No images found")
        existing = []
        if self.context.portal_type == 'Folder':
            existing = search.existing_copies(self.context)
        for item in self.image_metadata:
            url = self.image_metadata[item].get('url_pre', 'none')
            self.image_metadata[item]['url'] = url
            self.image_metadata[item]['exists'] = url in existing
        if form.get('type', '') == 'json':
            return json.dumps({
                'search_context': self.search_context,
                'errors': self.messages,
                'metadata': self.image_metadata,
                'num_results': num_results,
                'b_start': b_start,
                'b_end': num_results > b_end and b_end or num_results,
                'num_batches': math.ceil(num_results / b_size),
                'curr_batch': batch,
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

    def valid_image(self, img_url):
        # test if image url is valid
        try:
            img_response = requests.get(img_url)
        except (exc.ConnectTimeout, exc.ConnectionError):
            return None
        if img_response.status_code != 200:
            return None
        try:
            Image.open(requests.get(img_url, stream=True).raw)
        except OSError:
            return None
        return (img_response, img_url)

    def __call__(self):
        """Return original image size
           If function is 'geturl', return the image url
           If function is 'copyimage', copy image into the current folder
        """
        img_function = self.request.form.get('function')
        img_id = self.request.form.get('id')
        img_url = self.request.form.get('image')  # preview size
        if not img_url:
            return "Image ID not found"
        # get original image size
        sizes_query = '&function=get_resource_path&param1={0}&param2=false&param3='.format(
            img_id
        )
        img_orig_url = self.rssearch.query_resourcespace(sizes_query)
        for size in [img_orig_url, img_url]:
            img_response = self.valid_image(size)
            if img_response:
                break
        if not img_response:
            return "Unable to find a valid image url"
        if img_function == 'geturl':
            return img_response[1]
        if img_function == 'copyimage':
            blob = NamedBlobImage(
                data=img_response[0].content)
            query = '&function=get_resource_field_data&param1={0}'.format(
                img_id
            )
            response = self.rssearch.query_resourcespace(query)
            img_metadata = {x['title']: x['value'] for x in response}
            new_image = api.content.create(
                type='Image',
                image=blob,
                container=self.context,
                title=self.request.form.get('title'),
                external_url=img_url,  # use preview size
                description=str(img_metadata),
            )
            return "Image copied to <a href='{0}/view'>{0}</a>".format(
                new_image.absolute_url())
        return "No action taken, did you pass in a function?"
