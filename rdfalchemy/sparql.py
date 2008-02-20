from rdflib import ConjunctiveGraph, RDF
from rdflib import URIRef , Literal, BNode
from rdflib.syntax.parsers.ntriples import NTriplesParser

from urllib2 import urlopen, Request
from urllib import urlencode

import simplejson
import logging

log=logging.getLogger(__name__)

# use a fast ElementTree
# TODO: test these each for iterparse compatability and relative speed
try:
    import cElementTree as ET # effbot's C module
except ImportError:
    try:
        import xml.etree.ElementTree as ET # in python >=2.5
    except ImportError:
        try:
            import lxml.etree as ET # ElementTree API using libxml2
        except ImportError:
            import elementtree.ElementTree as ET # effbot's pure Python module
log.debug('Using ElementTree: %s' % ET)

# some constants for parsing the xml tree
_S_NS    = "{http://www.w3.org/2005/sparql-results#}"
_VARIABLE= _S_NS+"variable"
_BNODE   = _S_NS+"bnode"
_URI     = _S_NS+"uri"
_BINDING = _S_NS+"binding"
_LITERAL = _S_NS+"literal"
_HEAD    = _S_NS+"head"
_RESULT  = _S_NS+"result"
_X_NS = "{http://www.w3.org/XML/1998/namespace}"
_LANG = _X_NS+"lang"



class DumpSink(object):
   def __init__(self):
      self.length = 0

   def triple(self, s, p, o):
      self.length += 1
      self._triple=(s,p,o)

   def get_triple(self):
       return self._triple


class SPARQLGraph(object):
    """provides (some) rdflib api via http to a SPARQL endpoint
    gives 'read-only' access to the graph
    constructor takes http endpoint and repository name
    e.g.  SPARQLGraph('http://localhost:2020/sparql')"""
    
    def __init__(self, url, context=None):
        self.url = url
        self.context=context

    def construct(self, strOrTriple, initBindings={}, initNs={}):
        """
        Executes a SPARQL Construct
        :param strOrTriple: can be either
        
          * a string in which case it it considered a CONSTRUCT query
          * a triple in which case it acts as the rdflib `triples((s,p,o))`
        
        :param initBindings:  A mapping from a Variable to an RDFLib term (used as initial bindings for SPARQL query)
        :param initNs:  A mapping from a namespace prefix to a namespace
        
        :returns: an instance of rdflib.ConjuctiveGraph('IOMemory')
        """
        if isinstance(strOrTriple, str):
            query = strOrTriple
            if initNs:
                prefixes = ''.join(["prefix %s: <%s>\n"%(p,n) for p,n in initNs.items()])
                query = prefixes + query
        else:
            s,p,o = strOrTriple
            t='%s %s %s'%((s and s.n3() or '?s'),(p and p.n3() or '?p'),(o and o.n3() or '?o'))
            query='construct {%s} where {%s}'%(t,t)
        query = dict(query=query)
        
        url = self.url+"?"+urlencode(query)
        req = Request(url)
        log.debug("Request: %s" % req.get_full_url())
        req.add_header('Accept','application/rdf+xml')
        log.debug("opening url: %s\n  with headers: %s" % (req.get_full_url(), req.header_items()))        
        subgraph = ConjunctiveGraph('IOMemory')
        subgraph.parse(urlopen(req))
        return subgraph
        
    def triples(self, (s,p,o), method='CONSTRUCT'):
        """
        :returns: a generator over triples matching the pattern
        :param method: must be 'CONSTRUCT' or 'SELECT'
        
             * CONSTRUCT calls CONSTRUCT query and returns a Graph result 
             * SELECT calls a SELECT query and returns an interator streaming over the results
        
        Use SELECT if you expect a large result set or may consume less than the entire result"""
        if method == 'CONSTRUCT':
            return self.construct((s,p,o)).triples((None,None,None))
        elif method == 'SELECT':
            if s or p or o:
                pattern = "%s %s %s"%((s and s.n3() or '?s'),(p and p.n3() or '?p'),(o and o.n3() or '?o'))
            else:
                pattern = ''
            query = "select ?s ?p ?o where {?s ?p ?o. %s}" % pattern
            return self.query(query)
        else:
            raise "Unknown method: %s"%(method)
    
    def __iter__(self):
        """Iterates over all triples in the store"""
        return self.triples((None, None, None))

    def __contains__(self, triple):
        """Support for 'triple in graph' syntax"""
        for triple in self.triples(triple):
            return 1
        return 0
        
    def subjects(self, predicate=None, object=None):
        """A generator of subjects with the given predicate and object"""
        for s, p, o in self.triples((None, predicate, object)):
            yield s

    def predicates(self, subject=None, object=None):
        """A generator of predicates with the given subject and object"""
        for s, p, o in self.triples((subject, None, object)):
            yield p

    def objects(self, subject=None, predicate=None):
        """A generator of objects with the given subject and predicate"""
        for s, p, o in self.triples((subject, predicate, None)):
            yield o

    def subject_predicates(self, object=None):
        """A generator of (subject, predicate) tuples for the given object"""
        for s, p, o in self.triples((None, None, object)):
            yield s, p

    def subject_objects(self, predicate=None):
        """A generator of (subject, object) tuples for the given predicate"""
        for s, p, o in self.triples((None, predicate, None)):
            yield s, o

    def predicate_objects(self, subject=None):
        """A generator of (predicate, object) tuples for the given subject"""
        for s, p, o in self.triples((subject, None, None)):
            yield p, o


    def value(self, subject=None, predicate=RDF.value, object=None, default=None, any=True):
        """Get a value for a pair of two criteria

        Exactly one of subject, predicate, object must be None. Useful if one
        knows that there may only be one value.

        It is one of those situations that occur a lot, hence this *macro* like utility

        :param  subject, predicate, object: exactly one must be None
        :param default: value to be returned if no values found
        :param any:     if more than one answer return **any one** answer, otherwise `raise UniquenessError`
        """
        retval = default

        if (subject is None and (predicate is None or object is None)) or \
                (predicate is None and object is None):
            return None
        
        if object is None:
            values = self.objects(subject, predicate)
        if subject is None:
            values = self.subjects(predicate, object)
        if predicate is None:
            values = self.predicates(subject, object)

        try:
            retval = values.next()
        except StopIteration, e:
            retval = default
        else:
            if any is False:
                try:
                    next = values.next()
                    msg = ("While trying to find a value for (%s, %s, %s) the "
                           "following multiple values where found:\n" %
                           (subject, predicate, object))
                    triples = self.triples((subject, predicate, object))
                    for (s, p, o) in triples:
                        msg += "(%s, %s, %s)\n" % (
                            s, p, o)
                    raise exceptions.UniquenessError(msg)
                except StopIteration, e:
                    pass
        return retval

    def label(self, subject, default=''):
        """Query for the RDFS.label of the subject

        Return default if no label exists
        """
        if subject is None:
            return default
        return self.value(subject, RDFS.label, default=default, any=True)

    def comment(self, subject, default=''):
        """Query for the RDFS.comment of the subject

        Return default if no comment exists
        """
        if subject is None:
            return default
        return self.value(subject, RDFS.comment, default=default, any=True)

    def items(self, list):
        """Generator over all items in the resource specified by list

        list is an RDF collection.
        """
        while list:
            item = self.value(list, RDF.first)
            if item:
                yield item
            list = self.value(list, RDF.rest)
           
    def qname(self,uri):
        """turn uri into a qname given self.namespaces"""
        for p,n in db.namespaces.items():
            if uri.startswith(n):
                return "%s:%s"%(p,uri[len(n):])
        return uri


    def query(self, strOrQuery, initBindings={}, initNs={}, resultMethod="xml",processor="sparql"):
        """
        Executes a SPARQL query against this Graph
        
        :param strOrQuery: Is either a string consisting of the SPARQL query 
        :param initBindings: *optional* mapping from a Variable to an RDFLib term (used as initial bindings for SPARQL query)
        :param initNs: optional mapping from a namespace prefix to a namespace
        :param resultMethod: results query requested (must be 'xml' or 'json') 
         xml streams over the result set and json must read the entire set  to succeed 
        :param processor: The kind of RDF query (must be 'sparql' or 'serql')
        """
        query = strOrQuery
        if initNs:
	    prefixes = ''.join(["prefix %s: <%s>\n"%(p,n) for p,n in initNs.items()])
	    query = prefixes + query
        log.debug("Query: %s"%(query))
        query = dict(query=query,queryLn=processor)
        url = self.url+"?"+urlencode(query)
        req = Request(url)
        if resultMethod == 'xml':
            return self._sparql_results_xml(req)
        elif resultMethod == 'json':
            return self._sparql_results_json(req)
        else:
            raise "Unknown resultMethod: %s"%(resultMethod)
        
    def _sparql_results_json(self,req):
        """_sparql_results_json takes a Request
         returns an interator over the results but
         **does not use a real generator** 
         it consumes the entire result set before
         yielding the first result"""
        req.add_header('Accept','application/sparql-results+json')
        log.debug("opening url: %s\n  with headers: %s" % (req.get_full_url(), req.header_items()))
        ret=simplejson.load(urlopen(req))
        var_names = ret['head']['vars'] 
        bindings = ret['results']['bindings']
        for b in bindings:
            for var,val in b.items():
                type = val['type']
                if type=='uri':
                   b[var]=URIRef(val['value'])
                elif type == 'bnode':
                   b[var]=BNode(val['value'])
                elif type == 'literal':
                   b[var]=Literal(val['value'],lang=val.get('xml:lang'))
                elif type == 'typed-literal':
                   b[var]=Literal(val['value'],datatype=val.get('datatype'))
                else:
                   raise AttributeError("Binding type error: %s"%(type))
            yield tuple([b.get(var) for var in var_names])
            

    def _sparql_results_xml(self,req):
        """_sparql_results_xml takes a Request
         returns an interator over the results"""
        # this uses xml.
        var_names=[]
        bindings=[] 
        req.add_header('Accept','application/sparql-results+xml')
        log.debug("opening url: %s\n  with headers: %s" % (req.get_full_url(), req.header_items()))
        events = iter(ET.iterparse(urlopen(req),events=('start','end')))
        # lets gather up the variable names in head
        for (event, node) in events:
            if event == 'start' and node.tag == _VARIABLE:
                var_names.append(node.get('name'))
            elif event == 'end' and node.tag == _HEAD:
                break
        # now let's yield each result as we parse them
        for (event, node) in events:
            if event == 'start':
                if node.tag == _RESULT:
                    bindings = [None,] *  len(var_names)
                elif node.tag == _BINDING:
                    idx = var_names.index(node.get('name'))
            elif event == 'end':
                if node.tag == _URI:
                    bindings[idx] = URIRef(node.text)
                elif node.tag == _BNODE:
                    bindings[idx] = BNode(node.text)
                elif node.tag == _LITERAL:
                    bindings[idx] = Literal(node.text or '',
                                        datatype = node.get('datatype'), 
                                        lang= node.get(_LANG))
                elif node.tag == _RESULT:
                    node.clear()
                    yield tuple(bindings)


    def describe(self, s_or_po, initBindings={}, initNs={}):
        """
        Executes a SPARQL describe of resource
        
        :param s_or_po:  is either
        
          * a subject ... should be a URIRef
          * a tuple of (predicate,object) ... pred should be inverse functional
          * a describe query string
          
        :param initBindings: A mapping from a Variable to an RDFLib term (used as initial bindings for SPARQL query)
        :param initNs: A mapping from a namespace prefix to a namespace
        """
        if isinstance(s_or_po, str):
            query = s_or_po
            if initNs:
                prefixes = ''.join(["prefix %s: <%s>\n"%(p,n) for p,n in initNs.items()])
                query = prefixes + query
        elif isinstance(s_or_po, URIRef) or isinstance(s_or_po, BNode):
            query = "describe %s" % (s_or_po.n3())
        else:
            p,o = s_or_po
            query = "describe ?s where {?s %s %s}"%(p.n3(),o.n3())
        query = dict(query=query)
        
        url = self.url+"?"+urlencode(query)
        req = Request(url)
        req.add_header('Accept','application/rdf+xml')
        log.debug("opening url: %s\n  with headers: %s" % (req.get_full_url(), req.header_items()))
        subgraph = ConjunctiveGraph()
        subgraph.parse(urlopen(req))
        return subgraph

