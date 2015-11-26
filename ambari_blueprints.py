#!/usr/bin/env python
#  vim:ts=4:sts=4:sw=4:et
#
#  Author: Hari Sekhon
#  Date: 2015-11-08 14:09:50 +0000 (Sun, 08 Nov 2015)
#  (re-instantiated from a Perl version in 2014)
#
#  https://github.com/harisekhon/pytools
#
#  License: see accompanying Hari Sekhon LICENSE file
#
#  If you're using my code you're welcome to connect with me on LinkedIn and optionally send me feedback to help improve or steer this or other code I publish
#
#  http://www.linkedin.com/in/harisekhon
#

"""

Ambari Blueprints Tool

Features:

1. find and fetch all Ambari Blueprints and/or Blueprint existing clusters
2. fetch a specific given blueprint
3. blueprint an existing cluster (<== I like this one)
3. strips out href that is not valid to re-submit
4. strips out configuration settings values to make the blueprint more generic if specifying --strip-config
5. push a given blueprint file to Ambari, resetting a blueprint's name field on the fly to avoid field conflicts between adjacent blueprints
6. create a new cluster using a previously uploaded blueprint and a hostmapping file
7. list available blueprints, clusters and hosts

Ambari Blueprints are supported for Ambari 1.6.0 upwards.

Tested on Ambari 2.1.0 and 2.1.2.x and Hortonworks HDP 2.2 / 2.3 clusters

Ambari Blueprints documentation

https://cwiki.apache.org/confluence/display/AMBARI/Blueprints
https://cwiki.apache.org/confluence/display/AMBARI/Blueprint+Support+for+HA+Clusters:

For custom repos see:

https://cwiki.apache.org/confluence/display/AMBARI/Blueprints#Blueprints-Step4:SetupStackRepositories(Optional)

"""

from __future__ import print_function

__author__ = 'Hari Sekhon'
__version__ = '0.6.6'

import base64
from httplib import BadStatusLine
import json
import logging
import os
import sys
# using optparse rather than argparse for servers still on Python 2.6
from optparse import OptionParser
import urllib2
from urllib2 import URLError
sys.path.append(os.path.dirname(os.path.abspath(sys.argv[0])) + '/pylib')
try:
    pass
    from harisekhon.utils import *
#    from harisekhon import CLI
except ImportError, e:
    print('module import failed: %s' % e)
    sys.exit(4)

# TODO: auto-store to git - see perl tools

class AmbariBlueprintTool():

    def __init__(self, host, port, user, password, ssl=False, **kwargs):
        # must set X-Requested-By in newer versions of Ambari
        # log.info("contacting Ambari as '%s'" % self.user)
        self.X_Requested_By = os.getenv('USER', user)
        if not isHost(host) or not isPort(port) or not isUser(user) or not password:
            raise InvalidOptionException('invalid options passed to AmbariBlueprint()')
        proto    = 'http'
        if ssl:
            proto = 'https'
        self.host = host
        self.port = port
        self.user = user
        self.strip_config = False
        # if kwargs.has_key('strip_config') and kwargs['strip_config']:
        if 'strip_config' in kwargs and kwargs['strip_config']:
            self.strip_config = True
        self.timeout_per_req = 30
        self.url_base = '%(proto)s://%(host)s:%(port)s/api/v1' % locals()
        # hack per req because otherwise needs to catch and then retry which is tedious
        self.base64authtok = base64.encodestring('%s:%s' % (user, password)).replace('\n', '')
        ## doesn't work with first req
        # passman = urllib2.HTTPPasswordMgrWithDefaultRealm()
        # passman.add_password(None, '%(proto)s://%(host)s:%(port)s/' % locals(), user, password)
        # auth_handler = urllib2.HTTPBasicAuthHandler(passman)
        ## doesn't work with first req
        # auth_handler.add_password(
        ##                            realm='Ambari',
        #                           None,
        #                           '%(proto)s://%(host)s:%(port)s/' % locals(),
        #                           user,
        #                           password)
        # opener = urllib2.build_opener(auth_handler)
        # urllib2.install_opener(opener)
        # same as below but more direct, but harder to read
        # self.blueprint_dir = kwargs.get('dir', os.path.join(os.path.dirname(sys.argv[0]), 'ambari_blueprints'))
        self.blueprint_dir = os.path.join(os.path.dirname(sys.argv[0]), 'ambari_blueprints')
        if 'dir' in kwargs and kwargs['dir']:
            self.blueprint_dir = kwargs['dir']
        if not isDirname(self.blueprint_dir):
            quit('UNKNOWN', 'invalid dir arg passed to AmbariBlueprintTool')
        try:
            if not self.blueprint_dir or not os.path.exists(self.blueprint_dir):
                log.info("creating blueprint data dir '%s'" % self.blueprint_dir)
                os.mkdir(self.blueprint_dir)
            if not os.path.isdir(self.blueprint_dir):
                raise IOError("blueprint dir '%s'already taken and is not a directory" % self.blueprint_dir)
        except IOError, e:
            die("'failed to create dir '%s': %s" % (self.blueprint_dir, e))

    def parse_cluster_name(self, item):
        if isStr(item):
            item = json.loads(item)
        try:
            return item['Clusters']['cluster_name']
        except Exception, e:
            quit('CRITICAL', 'failed to parse Ambari cluster name: %s' % e)

    def get_clusters(self):
        log.debug('get_clusters()')
        jsonData = self.list('clusters')
        return [ self.parse_cluster_name(item) for item in jsonData['items'] ]

    def parse_blueprint_name(self, item):
        if isStr(item):
            item = json.loads(item)
        try:
            return item['Blueprints']['blueprint_name']
        except Exception, e:
            quit('CRITICAL', 'failed to parse Ambari blueprint name: %s' % e)

    def get_blueprints(self):
        # log.debug('get_blueprints()')
        jsonData = self.list('blueprints')
        return [ self.parse_blueprint_name(item) for item in jsonData['items'] ]

    def parse_host_name(self, item):
        if isStr(item):
            item = json.loads(item)
        try:
            return item['Hosts']['host_name']
        except Exception, e:
            quit('CRITICAL', 'failed to parse Ambari host name: %s' % e)

    def get_hosts(self):
        log.debug('get_hosts()')
        jsonData = self.list('hosts')
        return [ self.parse_host_name(item) for item in jsonData['items'] ]

    def list(self, url_suffix):
        self.url = self.url_base + '/' + url_suffix
        try:
            response = self.get(url_suffix)
        except URLError, e:
            err = 'failed to fetch list of Ambari Blueprints: %s' % e
            # log.critical(err)
            quit('CRITICAL', err)
        jsonData = json.load(response)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("jsonData = " + jsonpp(jsonData))
        return jsonData

    def get_cluster_blueprint(self, cluster):
        return self.fetch('clusters/%s?format=blueprint' % cluster)

    def get_blueprint(self, blueprint):
        return self.fetch('blueprints/%s' % blueprint)

    # throws (URLError, BadStatusLine) - catch in caller for more specific exception handling error reporting
    def req(self, url_suffix, data=None):
        self.url = self.url_base + '/' + url_suffix
        if data:
            log.debug('POST %s' % self.url)
        else:
            log.debug('GET %s' % self.url)
        req = urllib2.Request(self.url) #, data, self.timeout)
        req.add_header('X-Requested-By', self.X_Requested_By)
        req.add_header("Authorization", "Basic %s" % self.base64authtok)
        # response = ''
        # try:
        response = urllib2.urlopen(req, data, self.timeout_per_req)
        # except (URLError, BadStatusLine), e:
        #     log.warn(e.read())
        #     if response:
        #         log.critical(response)
        #     #re-throw, preserve stack
        #     raise
        return response

    def get(self, url_suffix):
        return self.req(url_suffix)

    def post(self, url_suffix, data):
        return self.req(url_suffix, data)

    def fetch(self, url_suffix):
        err = ''
        try:
            response = self.get(url_suffix)
        except URLError, e:
            err = "failed to fetch Ambari Blueprint from '%s': %s" % (self.url, e)
        # This happens with stale SSH tunnels
        # TODO: XXX: this doesn't seem to catch but it's such an intermittent issue it's hard to reproduce
        except BadStatusLine, e:
            err = "failed to fetch Ambari Blueprint from '%s' due to BadStatusLine returned: %s" % (self.url, e)
        if err:
            # log.critical(err)
            quit('CRITICAL', e)
        jsonData = json.load(response)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("blueprint = " + jsonpp(jsonData))
        try:
            del jsonData['href']
            log.debug("stripped href as it's not valid if re-submitting the blueprint to Ambari")
        except KeyError, e:
            pass
        # Ambari 2.1.3 supports this according to https://cwiki.apache.org/confluence/display/AMBARI/Blueprints#Blueprints-ClusterCreationTemplateStructure
        # jsonData['config_recommendation_strategy'] = 'NEVER_APPLY' # default
        # jsonData['config_recommendation_strategy'] = 'ONLY_STACK_DEFAULTS_APPLY'
        # jsonData['config_recommendation_strategy'] = 'ALWAYS_APPLY'
        if self.strip_config:
            log.info('stripping out config sections of blueprints to make more generic')
            try:
                del jsonData['configurations']
                for hostgroup in jsonData['host_groups']:
                    del hostgroup['configurations']
            except KeyError, e:
                pass
        try:
            jsonData['host_groups'] = list_sort_dicts_by_value(jsonData['host_groups'], 'name')
            for hostgroup in jsonData['host_groups']:
                hostgroup['components'] = list_sort_dicts_by_value(hostgroup['components'], 'name')
        except KeyError, e:
            quit('CRITICAL', 'failed to sort blueprint: %s' % e)
        return jsonpp(jsonData)

    def send(self, url_suffix, data):
        # log.debug('send(%s, %s)' % url_suffix, data)
        self.url = self.url_base + '/' + url_suffix
        err = ''
        conflict_err = " (is there an existing blueprint with the same --blueprint name or a blueprint with the same Blueprints -> blueprint_name field? Try changing --blueprint and/or the blueprint_name field in the blueprint file you're trying to --push)"
        try:
            response = self.post(url_suffix, data)
        except URLError, e:
            err = "failed to POST Ambari Blueprint to '%s': %s - %s" % (self.url, e, e.read())
            if 'Conflict' in str(e):
                err += conflict_err
            # if data:
            #     err += ", response='%s'" % data
        # This happens with stale SSH tunnels
        except BadStatusLine, e:
            err = "failed to POST Ambari Blueprint to '%s' due to BadStatusLine returned: %s" % (self.url, e)
            if 'Conflict' in str(e):
                err += conflict_err
        if err:
            # log.critical(err)
            quit('CRITICAL', err)
        try:
            jsonData = json.load(response)
        except ValueError, e:
            log.debug('no valid json returned by Ambari server: %s' % e)
        if log.isEnabledFor(logging.DEBUG) and 'jsonData' in locals():
            log.debug("response = " + jsonpp(jsonData))
        return True

    def send_blueprint_file(self, file, name=''):
        # log.debug('send_blueprint_file(%s, %s)' % (file, name))
        validate_file(file, 'blueprint', nolog=True)
        try:
            fh = open(str(file))
            file_data = fh.read()
        except IOError, e:
            err = "failed to read Ambari Blueprint from file '%s': %s" % (file, e)
            # log.critical(err)
            quit('CRITICAL', err)
        if not name:
            try:
                name = self.parse_blueprint_name(file_data)
                log.info("name not specified, determined blueprint name from file contents as '%s'" % name)
            except KeyError, e:
                pass
        if not name:
            name = os.path.splitext(os.path.basename(file))[0]
            log.info("name not specified and couldn't determine blueprint name from blueprint data, reverting to using filename without extension '%s'" % name)
        # this solves the issue of having duplicate Blueprint.blueprint_name keys
        try:
            jsonData = json.loads(file_data)
            jsonData['Blueprints']['blueprint_name'] = name
            data = json.dumps(jsonData)
            log.info("reset blueprint field name to '%s'" % name)
        except ValueError, e:
            quit('CRITICAL', "invalid json found in file '%s': %s" % (file, name))
        except KeyError, e:
            log.warn('failed to reset the Blueprint name: %s' % e)
        return self.send_blueprint(name, data)

    def create_cluster(self, cluster, file, blueprint=''):
        # log.debug('create_cluster(%s, %s)' % (file, name))
        validate_file(file, 'cluster hosts mapping', nolog=True)
        try:
            fh = open(str(file))
            file_data = fh.read()
        except IOError, e:
            err = "failed to read Ambari cluster host mapping from file '%s': %s" % (file, e)
            # log.critical(err)
            quit('CRITICAL', err)
        log.info("creating cluster '%s' using file '%s'" % (cluster, file))
        if not isJson(file_data):
            quit('CRITICAL', "invalid json found in file '%s'" % file)
        # don't have access to a blueprint name to enforce reset here
        # jsonData = json.loads(file_data)
        # try:
        #     jsonData['Blueprints']['blueprint_name'] = blueprint
        # except KeyError, e:
        #     quit('CRITICAL', 'failed to (re)set blueprint name in cluster/hostmapping data before creating cluster')
        if blueprint:
            try:
                log.info("setting blueprint in cluster creation to '%s'" % blueprint)
                jsonData = json.loads(file_data)
                jsonData['blueprint'] = blueprint
                file_data = json.dumps(jsonData)
            except KeyError, e:
                log.warn("failed to inject blueprint name '%s' in to cluster creation" % blueprint)
        response = self.send('clusters/%s' % cluster, file_data)
        log.info("Cluster creation submitted, see Ambari web UI to track progress")
        return response

    def send_blueprint(self, name, data):
        # log.debug('save_blueprint(%s, %s)' % (name, data))
        blueprints = self.get_blueprints()
        if name in blueprints:
            log.warn("blueprint with name '%s' already exists" % name)
        log.info("sending blueprint '%s'" % name)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("blueprint data = '%s'" % data)
        # not exposing this to user via switches - shouldn't be using this right now
        # return self.send('blueprints/%s?validate_topology=false' % name, data)
        # quit('UNKNOWN', 'cluster creation not supported yet')
        return self.send('blueprints/%s' % name, data)

    def save_blueprint(self, blueprint, path=''):
        # log.debug('save_blueprint(%s, %s' % (blueprint, name))
        if not path:
            path = os.path.normpath(os.path.join(self.blueprint_dir, blueprint))
        data = self.get_blueprint(blueprint)
        # logged in save()
        # log.info("saving blueprint '%s' to file '%s" % (blueprint, path))
        if log.isEnabledFor(logging.DEBUG):
            log.debug("blueprint '%s' content = '%s'" % (blueprint, data))
        self.save(blueprint, path, data)

    def save_cluster(self, cluster, path=''):
        # log.debug('save_cluster(%s, %s)' % (cluster, name))
        if not path:
            path = os.path.normpath(os.path.join(self.blueprint_dir, cluster))
        data = self.get_cluster_blueprint(cluster)
        # logged in save()
        # log.info("saving cluster '%s' blueprint to file '%s'" % (cluster, path))
        if log.isEnabledFor(logging.DEBUG):
            log.debug("cluster '%s' blueprint content = '%s'" % (cluster, data))
        self.save(cluster, path, data)

    def save(self, name, path, data):
        # log.debug('save(%s, %s)' % (name, data))
        if data == None:
            err = "blueprint '%s' returned None" % name
            log.critical(err)
            quit('CRITICAL', err)
        # blueprint_file = os.path.basename(name).lower().rstrip('.json') + '.json'
        # if not os.pathsep not in blueprint_file:
        #     blueprint_file = os.path.normpath(os.path.join(self.blueprint_dir, blueprint_file))
        if os.path.splitext(path)[1] != '.json':
            path += '.json'
        try:
            log.info("writing blueprint '%s' to file '%s'" % (name, path))
            f = open(path, 'w')
            f.write(data)
            f.close()
            print("Saved blueprint '%s' to file '%s'" % (name, path))
        except IOError, e:
            quit('CRITICAL', "failed to write blueprint file to '%s': %s" % (path, e))

    def save_all(self):
        log.info('finding all blueprints and clusters to blueprint')
        blueprints = self.get_blueprints()
        clusters   = self.get_clusters()
        if not blueprints and not clusters:
            quit('UNKNOWN', 'no Ambari Blueprints or Clusters found on server')
        for blueprint in blueprints:
            self.save_blueprint(blueprint)
        for cluster in clusters:
            self.save_cluster(cluster)


def main():
    parser = OptionParser()
    parser.add_option('-H', '--host', dest='host', help='Ambari Host ($AMBARI_HOST)', metavar='<host>')
    parser.add_option('-P', '--port', dest='port', help='Ambari Port ($AMBARI_PORT, default: 8080)', metavar='8080')
    parser.add_option('-u', '--user', dest='user', help='Ambari login user ($AMBARI_USER, default: admin)', metavar='<user>')
    parser.add_option('-p', '--password', dest='password', help='Ambari login password ($AMBARI_PASSWORD)', metavar='<password>')
    # TODO: certificate validation not tested yet
    parser.add_option('-s', '--ssl', dest='ssl', help='Use SSL connection', action='store_true', default=False)
    parser.add_option('-b', '--blueprint', dest='blueprint', help='Ambari blueprint name', metavar='<name>')
    parser.add_option('-c', '--cluster', dest='cluster', help='Ambari cluster to blueprint (case sensitive)', metavar='<name>')
    parser.add_option('--get', dest='get', help='Get and store Ambari Blueprints locally in --dir or --file', action='store_true')
    parser.add_option('--push', dest='push',  help='Push a local Ambari blueprint to the Ambari server', action='store_true')
    parser.add_option('--create-cluster', dest='create_cluster',  help='Create a cluster (requires --cluster and --file as well as previously uploaded Ambari Blueprint)', action='store_true')
    parser.add_option('-f', '--file', dest='file', help='Ambari Blueprint or Cluster creation file to --get write to or --push send from', metavar='<file.json>')
    parser.add_option('-d', '--dir', dest='dir', help="Ambari Blueprints storage directory if saving all blueprints (defaults to 'ambari_blueprints' directory adjacent to this tool)", metavar='<dir>')
    parser.add_option('--list-blueprints', dest='list_blueprints', help='List available blueprints', action='store_true', default=False)
    parser.add_option('--list-clusters', dest='list_clusters', help='List available clusters', action='store_true', default=False)
    parser.add_option('--list-hosts', dest='list_hosts', help='List available hosts', action='store_true', default=False)
    parser.add_option('--strip-config', dest='strip_config', help="Strip configuration sections out to make more generic. Use with caution, more advanced configurations like HDFS HA require some configuration settings in order to validate the topology when submitting a blueprint, so you'd have to add those config keys back in (suggest via a fully config'd cluster blueprint)", action='store_true', default=False)
    parser.add_option('-v', '--verbose', dest='verbose', help='Verbose mode', action='count', default=0)

    host     = os.getenv('AMBARI_HOST')
    port     = os.getenv('AMBARI_PORT', 8080)
    user     = os.getenv('AMBARI_USER', 'admin')
    password = os.getenv('AMBARI_PASSWORD')
    ssl      = False

    (options, args) = parser.parse_args()

    host = options.host if options.host else host
    port = options.port if options.port else port
    user = options.user if options.user else user
    password = options.password if options.password else password
    ssl = options.ssl if options.ssl else ssl
    blueprint = options.blueprint if options.blueprint else None
    cluster = options.cluster if options.cluster else None
    verbose = options.verbose

    log.setLevel(logging.WARN)
    if verbose > 1:
        log.setLevel(logging.DEBUG)
    elif verbose:
        log.setLevel(logging.INFO)
    # log.info('verbose level: %s' % verbose)

    try:
        validate_host(host)
        validate_port(port)
        validate_user(user)
        validate_password(password)
        if options.dir:
            validate_dirname(options.dir, 'blueprints')
        if options.file:
            if options.push:
                validate_file(options.file, 'blueprint')
            if options.create_cluster:
                validate_file(options.file, 'cluster hosts mapping')
    except InvalidOptionException, e:
        usage(parser, e)

    if args:
        usage(parser, 'additional args detected')

    if options.get and blueprint and cluster:
        usage(parser, '--blueprint/--cluster are mutually exclusive when using --get')
    elif options.push and options.create_cluster:
        usage(parser, '--push and --create-cluster are mutually exclusive')
    elif options.create_cluster and not options.cluster:
        usage(parser, '--create-cluster requires specifying the name via --cluster')
    elif options.list_blueprints + options.list_clusters + options.list_hosts > 1:
        usage(parser, 'can only use one --list switch at a time')
    elif options.file and (options.get and not (options.blueprint or options.cluster) ):
        usage(parser, "cannot specify --file without --blueprint/--cluster as it's only used when getting or pushing a single blueprint")
    elif options.file and (options.push and not (options.create_cluster or options.blueprint)):
        usage(parser, "cannot specify --file without --blueprint/--create-cluster as it's only used when getting or pushing a single blueprint or creating a cluster based on the blueprint")

    a = AmbariBlueprintTool(host, port, user, password, ssl, dir=options.dir, strip_config=options.strip_config )
    if options.list_blueprints:
        blueprints = a.get_blueprints()
        print('\nBlueprints (%s found):\n' % len(blueprints))
        if blueprints:
            [ print(x) for x in blueprints ]
        else:
            print('<No Blueprints Found>')
        clusters = a.get_clusters()
        print('\nClusters available to blueprint (%s found):\n' % len(clusters))
        if clusters:
            [ print(x) for x in clusters ]
        else:
            print('<No Clusters Found>')
        print()
        print('%s total extractable blueprints' % str(len(blueprints) + len(clusters)))
        sys.exit(0)
    elif options.list_clusters:
        clusters = a.get_clusters()
        print('\nClusters available to blueprint (%s found):\n' % len(clusters))
        if clusters:
            [ print(x) for x in clusters ]
        else:
            print('<No Clusters Found>')
        print()
        sys.exit(0)
    elif options.list_hosts:
        hosts = a.get_hosts()
        print('\nHosts (%s found):\n' % len(hosts))
        if hosts:
            # seems to come out already sorted(hosts)
            [ print(x) for x in hosts ]
        else:
            print('<No Hosts Found>')
        sys.exit(0)
    elif options.get:
        if options.blueprint:
            a.save_blueprint(blueprint, options.file)
        elif options.cluster:
            a.save_cluster(cluster, options.file)
        else:
            a.save_all()
    elif options.push:
        if not options.file:
            usage(parser, '--file must be specified when pushing a blueprint to Ambari')
        a.send_blueprint_file(options.file, blueprint)
        print("Blueprint file '%s' sent and registered with Ambari as '%s'" % (options.file, blueprint))
    elif options.create_cluster:
        if not options.file:
            usage(parser, '--file must be specified with a hostsmapping.json file when creating a new Ambari cluster')
        a.create_cluster(cluster, options.file, options.blueprint)
        print("Ambari cluster '%s' creation job submitted, see '%s:%s' web UI for progress" % (cluster, host, port))
    else:
        usage(parser)
    log.info('Completed')

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
