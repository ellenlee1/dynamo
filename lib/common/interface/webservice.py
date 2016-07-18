import sys
import urllib
import urllib2
import httplib
import json
import logging

import common.configuration as config
from common.misc import unicode2str

logger = logging.getLogger(__name__)

GET, POST = range(2) # enumerators

class HTTPSGridAuthHandler(urllib2.HTTPSHandler):

    def __init__(self):
        urllib2.HTTPSHandler.__init__(self)
        self.key = config.webservice.x509_key
        self.cert = self.key

    def https_open(self, req):
        return self.do_open(self.create_connection, req)

    def create_connection(self, host, timeout = 300):
        return httplib.HTTPSConnection(host, key_file = self.key, cert_file = self.cert)


class RESTService(object):
    """
    An interface to RESTful APIs (e.g. PhEDEx, DBS) with X509 authentication.
    make_request will take the REST "command" and a list of options as arguments.
    Options are chained together with '&' and appended to the url after '?'.
    Returns python-parsed content.
    """

    def __init__(self, url_base, headers = [], accept = 'application/json'):
        self.url_base = url_base
        self.headers = list(headers)
        self.accept = accept

    def make_request(self, resource, options = [], method = GET, format = 'url'):
        url = self.url_base + '/' + resource
        if method == GET and len(options) != 0:
            if type(options) is list:
                url += '?' + '&'.join(options)
            elif type(options) is str:
                url += '?' + options

        if logger.getEffectiveLevel() == logging.DEBUG:
            logger.debug(url)
        
        request = urllib2.Request(url)

        if method == POST and len(options) != 0:
            if type(options) is list:
                # convert key=value strings to (key, value) 2-tuples
                optlist = []
                for opt in options:
                    if type(opt) is tuple:
                        optlist.append(opt)

                    elif type(opt) is str:
                        key, eq, value = opt.partition('=')
                        if eq == '=':
                            optlist.append((key, value))

                options = optlist
            
            if format == 'url':
                # Options can be a dict or a list of 2-tuples. The latter case allows repeated keys (e.g. dataset=A&dataset=B)
                data = urllib.urlencode(options)

            elif format == 'json':
                # Options can be a dict or a list of 2-tuples. Repeated keys in the list case gets collapsed.
                if type(options) is list:
                    optdict = {}
                    for key, value in options:
                        if key in optdict:
                            try:
                                optdict[key].append(value)
                            except AttributeError:
                                current = optdict[key]
                                optdict[key] = [current, value]
                        else:
                            optdict[key] = value
    
                    options = optdict

                request.add_header('Content-type', 'application/json')
                data = json.dumps(options)

            request.add_data(data)

        exceptions = []
        while len(exceptions) != config.webservice.num_attempts:
            try:
                if self.url_base.startswith('https:'):
                    opener = urllib2.build_opener(HTTPSGridAuthHandler())
                else:
                    opener = urllib2.build_opener(urllib2.HTTPHandler())

                if 'Accept' not in self.headers:
                    opener.addheaders.append(('Accept', self.accept))

                opener.addheaders.extend(self.headers)

                response = opener.open(request)

                # clean up - break reference cycle so python can free the memory up
                for handler in opener.handlers:
                    handler.parent = None
                del opener

                content = response.read()
                del response

                if self.accept == 'application/json':
                    result = json.loads(content)
                    unicode2str(result)

                elif self.accept == 'application/xml':
                    # TODO implement xml -> dict
                    pass

                del content

                return result
    
            except:
                exceptions.append(sys.exc_info()[:2])
                continue

        else: # exhausted allowed attempts
            logger.error('Too many failed attempts in webservice')
            logger.error('%s' % ' '.join(map(str, exceptions)))
            raise RuntimeError('webservice too many attempts')


if __name__ == '__main__':

    import sys
    from argparse import ArgumentParser

    parser = ArgumentParser(description = 'REST interface')

    parser.add_argument('url_base', metavar = 'URL', help = 'Request URL base.')
    parser.add_argument('resource', metavar = 'RES', help = 'Request resource.')
    parser.add_argument('options', metavar = 'EXPR', nargs = '*', default = [], help = 'Options after ? (chained with &).')
    parser.add_argument('--post', '-P', action = 'store_true', dest = 'use_post', help = 'Use POST instead of GET request.')
    parser.add_argument('--log-level', '-l', metavar = 'LEVEL', dest = 'log_level', default = '', help = 'Logging level.')

    args = parser.parse_args()
    sys.argv = []

    if args.log_level:
        try:
            level = getattr(logging, args.log_level.upper())
            logging.getLogger().setLevel(level)
        except AttributeError:
            logging.warning('Log level ' + args.log_level + ' not defined')

    interface = RESTService(args.url_base)

    if args.use_post:
        method = POST
    else:
        method = GET

    print interface.make_request(args.resource, args.options, method = method)
