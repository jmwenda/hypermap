import sys
import pysolr
import requests
import logging
import json
import datetime

from urlparse import urlparse
from django.conf import settings
from django.utils.html import strip_tags

from aggregator.utils import mercator_to_llbbox


def get_date(layer):
    """
    Returns a date for Solr. A date can be detected or from metadata.
    It can be a range or a simple date in isoformat.
    """
    date = None
    type = 1
    layer_dates = layer.get_layer_dates()
    if layer_dates:
        date = layer_dates[0][0]
        type = layer_dates[0][1]
    if date is None:
        date = layer.created.date()
    # layer date > 2300 is invalid for sure
    # TODO put this logic in date miner
    if date.year > 2300:
        date = None
    if type == 0:
        type = "Detected"
    if type == 1:
        type = "From Metadata"
    return get_solr_date(date), type


def get_solr_date(pydate):
    """
    Returns a date in a valid Solr format from a string.
    """
    # check if date is valid and then set it to solr format YYYY-MM-DDThh:mm:ssZ
    try:
        if isinstance(pydate, datetime.datetime):
            solr_date = '%sZ' % pydate.isoformat()[0:19]
            return solr_date
        else:
            return None
    except Exception:
        return None


class SolrHypermap(object):

    solr_url = settings.SOLR_URL
    solr = pysolr.Solr(solr_url, timeout=60)
    logger = logging.getLogger("hypermap")

    @staticmethod
    def get_domain(url):
        urlParts = urlparse(url)
        hostname = urlParts.hostname
        if hostname == "localhost":
            return "Harvard"  # assumption
        return hostname

    @staticmethod
    def is_solr_up():
        solr_url = settings.SOLR_URL
        solr_url_parts = solr_url.split('/')
        admin_url = '/'.join(solr_url_parts[:-1]) + '/admin/cores'
        params = {'action': 'STATUS', 'wt': 'json'}
        try:
            req = requests.get(admin_url, params=params)
            response = json.loads(req.text)
            status = response['status']
            if status:
                response = True
        except requests.exceptions.RequestException:
            response = False
        return response

    @staticmethod
    def layer_to_solr(layer):
        category = None
        username = None
        try:
            # as a first thing we need to remove the existing index in case there is already one
            SolrHypermap.solr.delete(q='LayerId:%s' % layer.id)
            bbox = None
            if not layer.has_valid_bbox():
                message = 'There are not valid coordinates for layer id: %s' % layer.id
                SolrHypermap.logger.error(message)
            else:
                bbox = [float(layer.bbox_x0), float(layer.bbox_y0), float(layer.bbox_x1), float(layer.bbox_y1)]
                for proj in layer.srs.values():
                    if proj['code'] in ('102113', '102100'):
                        bbox = mercator_to_llbbox(bbox)
                minX = bbox[0]
                minY = bbox[1]
                maxX = bbox[2]
                maxY = bbox[3]
                # coords hack needed by solr
                if (minX < -180):
                    minX = -180
                if (maxX > 180):
                    maxX = 180
                if (minY < -90):
                    minY = -90
                if (maxY > 90):
                    maxY = 90
                wkt = "ENVELOPE({:f},{:f},{:f},{:f})".format(minX, maxX, maxY, minY)
                halfWidth = (maxX - minX) / 2.0
                halfHeight = (maxY - minY) / 2.0
                area = (halfWidth * 2) * (halfHeight * 2)
            domain = SolrHypermap.get_domain(layer.service.url)
            if hasattr(layer, 'layerwm'):
                category = layer.layerwm.category
                username = layer.layerwm.username
            abstract = layer.abstract
            if abstract:
                abstract = strip_tags(layer.abstract)
            else:
                abstract = ''
            if layer.service.type == "WM":
                originator = username
            else:
                originator = domain
            # now we add the index
            solr_record = {
                            "LayerId": str(layer.id),
                            "LayerName": layer.name,
                            "LayerTitle": layer.title,
                            "Originator": originator,
                            "ServiceId": str(layer.service.id),
                            "ServiceType": layer.service.type,
                            "LayerCategory": category,
                            "LayerUsername": username,
                            "LayerUrl": layer.url,
                            "LayerReliability": layer.reliability,
                            "LayerRecentReliability": layer.recent_reliability,
                            "LayerLastStatus": layer.last_status,
                            "Is_Public": layer.is_public,
                            "Availability": "Online",
                            "Location": '{"layerInfoPage": "' + layer.get_absolute_url() + '"}',
                            "Abstract": abstract,
                            "SrsProjectionCode": layer.srs.values_list('code', flat=True),
                            "DomainName": layer.service.get_domain
                            }

            solr_date, type = get_date(layer)
            if solr_date is not None:
                solr_record['LayerDate'] = solr_date
                solr_record['LayerDateType'] = type
            if bbox is not None:
                solr_record['MinX'] = minX
                solr_record['MinY'] = minY
                solr_record['MaxX'] = maxX
                solr_record['MaxY'] = maxY
                solr_record['Area'] = area
                solr_record['bbox'] = wkt
            SolrHypermap.solr.add([solr_record])
            SolrHypermap.logger.info("Solr record saved for layer with id: %s" % layer.id)
            return True, None
        except Exception:
            SolrHypermap.logger.error("Error saving solr record for layer with id: %s - %s"
                                      % (layer.id, sys.exc_info()[1]))
            return False, sys.exc_info()[1]

    @staticmethod
    def clear_solr():
        """Clear all indexes in the solr core"""
        SolrHypermap.solr.delete(q='*:*')
        print 'Solr core cleared'
