
from cheshire3.configParser import C3Object, CaselessDictionary
from cheshire3.baseObjects import Database, Index, ProtocolMap, Record
from cheshire3.baseStore import SummaryObject
from cheshire3.exceptions import ConfigFileException, ObjectDoesNotExistException
from cheshire3.bootstrap import BSParser, BootstrapDocument
from cheshire3.resultSet import SimpleResultSet, BitmapResultSet, ArrayResultSet
import cheshire3.cqlParser as cql

import os, sys, re, time
from lxml import etree

try:
    # name when installed by hand
    import bsddb3 as bdb
except:
    # name that comes in python 2.3
    import bsddb as bdb


class SimpleDatabase(SummaryObject, Database):
    """ Default database implementation """

    _possibleSettings = {'srw' : {'docs' : 'Should the database be available via the SRW protocol', 'type' : int, 'options' : "0|1"},
                         'z3950' : {'docs' : 'Should the database be available via the Z39.50 protocol', 'type' : int, 'options' : "0|1"},
                         'remoteWorkflow' : {'docs' : 'Should the database be available via the remote workflow protocol for Cheshire3. This MUST be secured, so it is not recommended without fully understanding the implications', 'type' : int, 'options' : "0|1"},
                         'oai-pmh' : {'docs' : 'Should the database be available via the OAI protocol', 'type' : int, 'options' : "0|1"}
                         }

    _possiblePaths = {'indexStoreList' : {'docs' : "Space separated list of indexStore identifiers for this database."}
                      , 'indexStore' : {'docs' : "Single indexStore identifier for this database"}
                      , 'recordStore' : {'docs' : "Single (default) recordStore identifier"}
                      , 'protocolMap' : {'docs' : "Single (default) protocolMap identifier"}
                      }
    indexes = {}
    protocolMaps = {}
    indexConfigs = {}
    protocolMapConfigs = {}
    records = {}

    def __init__(self, session, config, parent):
        self.indexes = CaselessDictionary()
        self.protocolMaps = CaselessDictionary()
        self.indexConfigs = CaselessDictionary()
        self.protocolMapConfigs = CaselessDictionary()
        self.records = {}
        Database.__init__(self, session, config, parent)
        SummaryObject.__init__(self, session, config, parent)

    def _cacheIndexes(self, session):
        storeList = self.get_path(session, 'indexStoreList')
        if not storeList:
            indexStore = self.get_path(session, 'indexStore')
            if not indexStore:
                raise ConfigFileException("No indexStore/indexStoreList associated with database: %s" % self.id)
            storeList = [indexStore.id]
        else:
            storeList = storeList.split(' ')
        for (id, dom) in self.indexConfigs.iteritems():
            # see if index should be built
            if hasattr(dom, 'childNodes'):
                for c in dom.childNodes:
                    if c.nodeType == 1 and c.localName == 'paths':
                        for c2 in c.childNodes:
                            if c2.nodeType == 1 and c2.localName == 'object':
                                istore = c2.getAttributeNS(None, 'ref')
                                if istore in storeList:
                                    o = self.get_object(session, id)
                                    self.indexes[id] = o
            else:
                for c in dom.iterchildren(tag=etree.Element):
                    if c.tag == 'paths':
                        for c2 in c.iterchildren(tag=etree.Element):
                            if c2.tag == 'object':
                                istore = c2.attrib['ref']
                                if istore in storeList:
                                    o = self.get_object(session, id)
                                    self.indexes[id] = o

    def _cacheProtocolMaps(self, session):
        for id in self.protocolMapConfigs.iterkeys():
            pm = self.get_object(session, id)
            self.protocolMaps[pm.protocol] = pm


    def add_record(self, session, rec):
        (storeid, id) = (rec.recordStore, rec.id)
        try:
            full = self.records.get(storeid, [[]])
            k = full[-1]
            if (len(k) > 1 and k[1] == id -1):
                k[1] = id
            elif ((len(k) == 1 and k[0] == id -1) or not k):
                k.append(id)
            else:
                full.append([id])
            self.records[storeid] = full
        except:
            pass
        self.accumulate_metadata(session, rec)
        return rec

    def index_record(self, session, rec):        
        if not self.indexes:
            self._cacheIndexes(session)
        for idx in self.indexes.itervalues():
            if not idx.get_setting(session, 'noIndexDefault', 0):
                idx.index_record(session, rec)
        return rec

    def remove_record(self, session, rec):
        self.totalItems -= 1
        (storeid, id) = (rec.recordStore, rec.id)        
        # XXX remove from self.records
        
        # XXX this should be SummaryObject.unaccumulate_metadata() ?
        if (rec.wordCount):
            self.totalWordCount -= rec.wordCount
        if (rec.byteCount):
            self.totalByteCount -= rec.byteCount

    def unindex_record(self, session, rec):
        if not self.indexes:
            self._cacheIndexes(session)
        for idx in self.indexes.itervalues():
            if not idx.get_setting(session, 'noUnindexDefault', 0):
                idx.delete_record(session, rec)
        return None       

    def begin_indexing(self, session):
        if not self.indexes:
            self._cacheIndexes(session)
        for idx in self.indexes.itervalues():
            idx.begin_indexing(session)
        return None

    def commit_indexing(self, session):
        for idx in self.indexes.itervalues():
            idx.commit_indexing(session)
        return None


    def clear_indexes(self, session):
        if not self.indexes:
            self._cacheIndexes(session)
        for idx in self.indexes.itervalues():
            idx.clear(session)
        
        return None

    def _search(self, session, query):
        if (isinstance(query, cql.SearchClause)):
            # Check resultset
            rsid = query.getResultSetId()
            if (rsid):
                # Get existing result set
                if rsid.find('/') > -1:
                    (rssid, rsid) = rsid.split('/', 1)
                    rss = self.get_object(session, rssid)
                else:
                    rss = self.get_object(session, "defaultResultSetStore")
                rset =  rss.fetch_resultSet(session, rsid)
                rset.fromStore = 1
                return rset
            else:
                pm = self.get_path(session, 'protocolMap')
                if not pm:
                    self._cacheProtocolMaps(session)
                    pm = self.protocolMaps.get('http://www.loc.gov/zing/srw/')
                    self.paths['protocolMap'] = pm
                idx = pm.resolveIndex(session, query)
                if (idx != None):
                    query.config = pm
                    rs = idx.search(session, query, self)
                    query.config = None
                    rs.query = query
                    return rs
                else:
                    # unsupported index
                    raise ObjectDoesNotExistException(query.index.toCQL())

        else:
            # get the indexStore
            left = self._search(session, query.leftOperand)
            right = self._search(session, query.rightOperand)
            if left.__class__ == right.__class__:
                new = left.__class__(session, [], recordStore=left.recordStore)
            elif left.__class__ == BitmapResultSet:
                # Want to switch the left/right, but rset assumes list[0] is same type
                new = right.__class__(session, [], recordStore=right.recordStore)
                if query.boolean.value == 'prox':
                    # bitmaps can't do prox, so just raise
                    raise QueryException("Cannot use Prox with %s" % left.index.toCQL(), 18)
                elif query.boolean.value == 'not':
                    # can't reorder without changing query
                    return new.combine(session, [left, right], query, self)
                else:
                    return new.combine(session, [right, left], query, self)
            elif right.__class__ == BitmapResultSet:
                new = left.__class__(session, [], recordStore=left.recordStore)
            else:
                new = SimpleResultSet(session, [])
            rs = new.combine(session, [left, right], query, self)
            trip = cql.Triple()
            trip.leftOperand = left.query
            trip.rightOperand = right.query
            trip.boolean = query.boolean
            rs.query = trip
            return rs

    def search(self, session, query):
        # check for optimised indexStore based search (eg SQL translation)
        storeList = self.get_path(session, 'indexStoreList')
        if not storeList:
            indexStore = self.get_path(session, 'indexStore')
            if not indexStore:
                raise ConfigFileException("No indexStore/indexStoreList associated with database: %s" % self.id)
            storeList = [indexStore.id]
        else:
            storeList = storeList.split(' ')

        # FIXME: Should respect multiple index stores somehow?
        idxStore = self.get_object(session, storeList[0])
        # check if there's an indexStore specific search function
        start = time.time()
        if hasattr(idxStore, 'search'):
            rs = idxStore.search(session, query, self)
        else:
            rs = self._search(session, query)
        # now do top level stuff, like sort


        if rs.relevancy:
            rs.scale_weights()
            rs.order(session, "weight")
        else:
            # CQL 1.2 sort definition
            # URI: info:srw/cql-context-set/1/sort-v1.0
            sk = query.sortKeys
            sk.reverse()  # stable sort = keys in reverse order
            pm = self.get_path(session, 'protocolMap')
            if not pm:
                self._cacheProtocolMaps(session)
                pm = self.protocolMaps.get('http://www.loc.gov/zing/srw/')
                self.paths['protocolMap'] = pm
            for idx in sk:
                # resolve index
                index = pm.resolveIndex(session, query)
                # and find params from modifiers
                if idx['ascending']:
                    ascending=True
                elif idx['descending']:
                    ascending=False
                elif hasattr(pm, 'defaultSortDirection'):
                    ascending = pm.defaultSortDirection[:3].lower() == 'asc'
                else:    
                    ascending = True

                if idx['missingomit']:
                    miss = 0
                elif idx['missinghigh']:
                    miss = 1
                elif idx['missinglow']:
                    miss = -1
                elif idx['missingfail']:
                    miss = cql.Diagnostic()
                elif idx['missingvalue']:
                    miss = idx['missingvalue'].value
                elif hasattr(pm, 'defaultSortMissing'):
                    m = pm.defaultSortMissing
                    vals = ['low', 'omit', 'high']
                    if m in vals:
                        miss = int(vals.index(m))-1
                    elif m == 'fail':
                        miss = cql.Diagnostic()
                    else:
                        miss = m
                else:
                    miss = [-1, 1][int(ascending)]

                if idx['respectcase']:
                    case = 1
                elif idx['ignorecase']:
                    case = 0
                elif hasattr(pm, 'defaultSortCase'):
                    if pm.defaultSortCase.lower() in ['1', 'true']:
                        case = 1
                    else:
                        case = 0
                else:
                    case = None

                if idx['respectaccents']:
                    accents = 1
                elif idx['ignoreaccents']:
                    accents = 0
                elif hasattr(pm, 'defaultSortAccents'):
                    if pm.defaultSortAccents.lower() in ['1', 'true']:
                        accents = 1
                    else:
                        accents = 0
                else:
                    accents = None

                # now, finally, order resultSet
                rs.order(session, index, ascending=ascending, missing=miss, case=case, accents=accents)

        query.resultSet = rs
        rs.queryTime = time.time() - start
        return rs

    def scan(self, session, clause, nTerms=25, direction=">="):
        if (not isinstance(clause, cql.SearchClause)):
            raise QueryException("Cannot use boolean in scan", 38)
        pm = self.get_path(session, 'protocolMap')
        if not pm:
            self._cacheProtocolMaps(session)
            pm = self.protocolMaps.get('http://www.loc.gov/zing/srw/')
            self.paths['protocolMap'] = pm
        idx = pm.resolveIndex(session, clause)
        if (idx != None):
            return idx.scan(session, clause, nTerms, direction)
        else:
            raise ObjectDoesNotExistException(query.index.toCQL())


class OptimisingDatabase(SimpleDatabase):
    """ Experimental query optimising database """

    def __init__(self, session, config, parent):
        SimpleDatabase.__init__(self, session, config, parent)
        self.maskRe = re.compile(r'(?<!\\)[*?]')
        
    def _rewriteQuery(self, session, query):
        if isinstance(query, cql.SearchClause):
            if query.relation.value == "all" :
                # rewrite to AND triples
                nbool = " and "
            elif query.relation.value == "any":
                nbool = " or "
            elif query.relation.value == "=" and not query.term.value.isnumeric() and query.term.value.index(' ') > -1:
                nbool = " prox "
            else:
                # can't rewrite
                return None

            # now split on spaces
            terms = query.term.value.split(' ')
            if len(terms) == 1:
                return None
            nq = []
            for t in terms:
                nq.append(' '.join([query.index.toCQL(), query.relation.toCQL(), '"' + t + '"']))
            newstr = nbool.join(nq)
            newQuery = cql.parse(newstr)
            return newQuery
        else:
            n = self._rewriteQuery(session, query.leftOperand)
            if n:
                query.leftOperand = n
            n = self._rewriteQuery(session, query.rightOperand)
            if n:
                query.rightOperand = n
            return None

    def _attachResultCount(self, session, query):
        if (isinstance(query, cql.SearchClause)):
            # If have masking chrs, assign positive number
            if self.maskRe.search(query.term.value):
                query.resultCount = 100
            else:
                pm = self.get_path(session, 'protocolMap')
                if not pm:
                    self._cacheProtocolMaps(session)
                    pm = self.protocolMaps.get('http://www.loc.gov/zing/srw/')
                    self.paths['protocolMap'] = pm
                idx = pm.resolveIndex(session, query)
                # terms should be atomic now.
                scandata = idx.scan(session, query, 1)
                if scandata[0][0] != query.term.value:
                    # no matches
                    query.resultCount = 0
                else:
                    query.resultCount = scandata[0][1][1]
        else:
            self._attachResultCount(session, query.leftOperand)
            if query.boolean.value in ['and', 'prox'] and query.leftOperand.resultCount == 0:
                query.resultCount = 0
                return

            self._attachResultCount(session, query.rightOperand)
            if query.boolean.value in ['and', 'prox']:
                query.resultCount = min(query.leftOperand.resultCount, query.rightOperand.resultCount)
                if query.boolean.value == "and" and query.rightOperand.resultCount < query.leftOperand.resultCount:
                    # can't reorder prox
                    temp = query.leftOperand
                    query.leftOperand = query.rightOperand
                    query.rightOperand = temp                    
                    del temp
            elif query.boolean.value == 'or':
                query.resultCount = query.leftOperand.resultCount + query.rightOperand.resultCount
                if query.rightOperand.resultCount > query.leftOperand.resultCount:
                    temp = query.leftOperand
                    query.leftOperand = query.rightOperand
                    query.rightOperand = temp                    
                    del temp
            else:
                # Can't really predict not and can't reorder. just take LHS
                query.resultCount = query.leftOperand.resultCount
        return None


    def _search(self, session, query):
        if query.resultCount == 0:
            # no matches in this full subtree
            return SimpleResultSet([])
        else:
            return SimpleDatabase._search(self, session, query)
                
    def search(self, session, query):
        # check for optimised indexStore based search (eg SQL translation)
        storeList = self.get_path(session, 'indexStoreList')
        if not storeList:
            indexStore = self.get_path(session, 'indexStore')
            if not indexStore:
                raise ConfigFileException("No indexStore/indexStoreList associated with database: %s" % self.id)
            storeList = [indexStore.id]
        else:
            storeList = storeList.split(' ')

        # FIXME: Should respect multiple index stores somehow?
        idxStore = self.get_object(session, storeList[0])
        # check if there's an indexStore specific search function
        if hasattr(idxStore, 'search'):
            return idxStore.search(session, query, self)
        else:

            if (isinstance(query, cql.SearchClause) and query.relation.value == "any"):
                # don't try to rewrite, futile.
                pass
            else:
                n = self._rewriteQuery(session, query)
                if n:
                    query = n
            if (isinstance(query, cql.SearchClause)):
                # single term or any in single clause
                query.resultCount = 1
                rs = self._search(session, query)
            else:
                # triples... walk and look for ANDs that have a 0 length rs
                # attach resultsets with counts
                self._attachResultCount(session, query)
                if query.resultCount == 0:
                    # no matches
                    return SimpleResultSet([])
                else:
                    rs = self._search(session, query)

        # now do top level stuff, like sort

        if rs.relevancy:
            rs.scale_weights()
            rs.order(session, "weight")
        elif query.sortKeys:
            # CQL 1.2 sort definition
            # URI: info:srw/cql-context-set/1/sort-v1.0

            sk = query.sortKeys
            sk.reverse()  # stable sort = keys in reverse order
            pm = self.get_path(session, 'protocolMap')
            if not pm:
                self._cacheProtocolMaps(session)
                pm = self.protocolMaps.get('http://www.loc.gov/zing/srw/')
                self.paths['protocolMap'] = pm
            for idx in sk:
                # resolve index
                index = pm.resolveIndex(session, query)
                # and find params from modifiers
                if idx['ascending']:
                    ascending=True
                elif idx['descending']:
                    ascending=False
                elif hasattr(pm, 'defaultSortDirection'):
                    ascending = pm.defaultSortDirection[:3].lower() == 'asc'
                else:    
                    ascending = True

                if idx['missingomit']:
                    miss = 0
                elif idx['missinghigh']:
                    miss = 1
                elif idx['missinglow']:
                    miss = -1
                elif idx['missingfail']:
                    miss = cql.Diagnostic()
                elif idx['missingvalue']:
                    miss = idx['missingvalue'].value
                elif hasattr(pm, 'defaultSortMissing'):
                    m = pm.defaultSortMissing
                    vals = ['low', 'omit', 'high']
                    if m in vals:
                        miss = int(vals.index(m))-1
                    elif m == 'fail':
                        miss = cql.Diagnostic()
                    else:
                        miss = m
                else:
                    miss = [-1, 1][int(ascending)]

                if idx['respectcase']:
                    case = 1
                elif idx['ignorecase']:
                    case = 0
                elif hasattr(pm, 'defaultSortCase'):
                    if pm.defaultSortCase.lower() in ['1', 'true']:
                        case = 1
                    else:
                        case = 0
                else:
                    case = None

                if idx['respectaccents']:
                    accents = 1
                elif idx['ignoreaccents']:
                    accents = 0
                elif hasattr(pm, 'defaultSortAccents'):
                    if pm.defaultSortAccents.lower() in ['1', 'true']:
                        accents = 1
                    else:
                        accents = 0
                else:
                    accents = None

                # now, finally, order resultSet
                rs.order(session, idx, ascending=asc, missing=miss, case=case, accents=accents)

        query.resultSet = rs
        return rs
        