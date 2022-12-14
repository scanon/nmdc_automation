#!/usr/bin/env python

import json
import sys
import os
import requests
import hashlib
import mimetypes
from time import time
from datetime import datetime


def _get_sha256(fn):
    hashfn = fn + '.sha256'
    if os.path.exists(hashfn):
        with open(hashfn) as f:
            sha = f.read().rstrip()
    else:
        print("hashing %s" % (fn))
        shahash = hashlib.sha256()
        with open(fn, "rb") as f:
            # Read and update hash string value in blocks of 4K
            for byte_block in iter(lambda: f.read(1048576), b""):
                shahash.update(byte_block)
        sha = shahash.hexdigest()
        with open(hashfn, 'w') as f:
            f.write(sha)
            f.write('\n')
    return sha


class remote_job():
    def __init__(self, jid, jdata):
        self.jobdata = jdata
        self.id = jid
        self.claimed = None
        self.opid = None

    def claim(self):
        self.claimed = True


class nmdcapi():
    _base_url = 'https://api.dev.microbiomedata.org/'

    def __init__(self):
        try:
            self.get_token()
        except Exception as e:
            self.expires = 0
            self.token = None
            raise e

    def refresh_token(self):
        # If it expires in 60 seconds, refresh
        if self.expires + 60 > time():
            self.get_token()

    def get_token(self):
        """
        Get a token using a client id/secret.
        """
        cfile = os.path.join(os.environ['HOME'], '.nmdc-creds.json')
        if not os.path.exists(cfile):
            self.token = None
            return

        with open(cfile) as f:
            creds = json.loads(f.read())
        h = {
                'accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
        data = {
                'grant_type': 'client_credentials',
                'client_id': creds['client_id'],
                'client_secret': creds['client_secret'],
                }
        url = self._base_url + 'token'
        resp = requests.post(url, headers=h, data=data).json()
        expt = resp['expires']
        self.expires = time() + expt['minutes'] * 60

        self.token = resp['access_token']
        self.header = {
                       'accept': 'application/json',
                       'Content-Type': 'application/json',
                       'Authorization': 'Bearer %s' % (self.token)
                       }
        return resp

    def get_header(self):
        return self.header

    def mint(self, ns, typ, ct):
        """
        Mint a new ID.
        Inputs: token (obtained using get_token)
                namespace (e.g. nmdc)
                type/shoulder (e.g. mga0, mta0)
                count/number of IDs to generate
        """
        url = self._base_url + 'ids/mint'
        d = {
                "populator": "",
                "naa": ns,
                "shoulder": typ,
                "number": ct
            }
        resp = requests.post(url, headers=self.header, data=json.dumps(d))
        return resp.json()

    def get_object(self, obj, decode=False):
        """
        Helper function to get object info
        """
        url = '%sobjects/%s' % (self._base_url, obj)
        resp = requests.get(url, headers=self.header)
        data = resp.json()
        if decode and 'description' in data:
            try:
                data['metadata'] = json.loads(data['description'])
            except Exception:
                data['metadata'] = None

        return data

    def create_object(self, fn, description, dataurl):
        """
        Helper function to create an object.
        """
        url = self._base_url + 'objects'
        fmeta = os.stat(fn)
        name = os.path.split(fn)[-1]
        mtypes = mimetypes.MimeTypes().guess_type(fn)
        if mtypes[1] is None:
            mt = mtypes[0]
        else:
            mt = 'application/%s' % (mtypes[1])

        sha = _get_sha256(fn)
        now = datetime.today().isoformat()
        d = {
             "aliases": None,
             "description": description,
             "mime_type": mt,
             "name": name,
             "access_methods": [
               {
                 "access_id": None,
                 "access_url": {
                    "url": dataurl,
                 },
                 "region": None,
                 "type": "https"
               }
             ],
             "checksums": [
               {
                 "checksum": sha,
                 "type": "sha256"
               }
             ],
             "contents": None,
             "created_time": now,
             "size": fmeta.st_size,
             "updated_time": None,
             "version": None,
             "id": sha,
             "self_uri": "todo"
        }
        resp = requests.post(url, headers=self.header, data=json.dumps(d))
        return resp.json()

    def set_type(self, obj, typ):
        url = '%sobjects/%s/types' % (self._base_url, obj)
        d = [typ]
        resp = requests.put(url, headers=self.header, data=json.dumps(d))
        return resp.json()

    def bump_time(self, obj):
        url = '%sobjects/%s' % (self._base_url, obj)
        now = datetime.today().isoformat()

        d = {
             "created_time": now
             }
        resp = requests.patch(url, headers=self.header, data=json.dumps(d))
        return resp.json()

    def list_jobs(self, filt=None, max=20):
        url = '%sjobs?max_page_size=%s' % (self._base_url, max)
        d = {}
        if filt:
            url += '&filter=%s' % (json.dumps(filt))
        orig_url = url
        results = []
        while True:
            resp = requests.get(url, data=json.dumps(d),
                                headers=self.header).json()
            if 'resources' not in resp:
                sys.stderr.write(str(resp))
                break
            results.extend(resp['resources'])
            if 'next_page_token' not in resp or not resp['next_page_token']:
                break
            url = orig_url + "&page_token=%s" % (resp['next_page_token'])
        return results

    def get_job(self, job):
        url = '%sjobs/%s' % (self._base_url, job)
        resp = requests.get(url, headers=self.header)
        return resp.json()

    def claim_job(self, job):
        url = '%sjobs/%s:claim' % (self._base_url, job)
        resp = requests.post(url, headers=self.header)
        if resp.status_code == 409:
            claimed = True
        else:
            claimed = False
        data = resp.json()
        data['claimed'] = claimed
        return data

    def _page_query(self, url):
        orig_url = url
        results = []
        while True:
            resp = requests.get(url, headers=self.header).json()
            if 'resources' not in resp:
                sys.stderr.write(str(resp))
                break
            results.extend(resp['resources'])
            if not resp['next_page_token']:
                break
            url = orig_url + "&page_token=%s" % (resp['next_page_token'])
        return results

    def list_objs(self, filt=None, max_page_size=40):
        url = '%sobjects?max_page_size=%d' % (self._base_url, max_page_size)
        if filt:
            url += '&filter=%s' % (json.dumps(filt))
        results = self._page_query(url)
        return results

    def list_ops(self, filt=None, max_page_size=40):
        url = '%soperations?max_page_size=%d' % (self._base_url, max_page_size)
        d = {}
        if filt:
            url += '&filter=%s' % (json.dumps(filt))
        orig_url = url
        results = []
        while True:
            resp = requests.get(url, data=json.dumps(d),
                                headers=self.header).json()
            if 'resources' not in resp:
                sys.stderr.write(str(resp))
                break
            results.extend(resp['resources'])
            if not resp['next_page_token']:
                break
            url = orig_url + "&page_token=%s" % (resp['next_page_token'])
        return results

    def get_op(self, opid):
        url = '%soperations/%s' % (self._base_url, opid)
        resp = requests.get(url, headers=self.header)
        return resp.json()

    def update_op(self, opid, done=None, results=None, meta=None):
        url = '%soperations/%s' % (self._base_url, opid)
        d = dict()
        if done is not None:
            d['done'] = done
        if results:
            d['result'] = results
        if meta:
            # Need to preserve the existing metadata
            cur = self.get_op(opid)
            if not cur['metadata']:
                # this means we messed up the record before.
                # This can't be fixed so just return
                return None
            d['metadata'] = cur['metadata']
            d['metadata']['extra'] = meta
        resp = requests.patch(url, headers=self.header, data=json.dumps(d))
        return resp.json()


def jprint(obj):
    print(json.dumps(obj, indent=2))


def usage():
    print("usage: ....")


if __name__ == "__main__":
    nmdc = nmdcapi()
    if len(sys.argv) < 2:
        usage()
    elif sys.argv[1] == 'set_type':
        obj = sys.argv[2]
        typ = sys.argv[3]
        nmdc.set_type(obj, typ)
    elif sys.argv[1] == 'undo':
        for opid in sys.argv[2:]:
            resp = nmdc.update_op(opid, done=False)
            jprint(resp)
    elif sys.argv[1] == 'get_job':
        obj = sys.argv[2]
        jprint(nmdc.get_job(obj))
    elif sys.argv[1] == 'list_jobs':
        filt = None
        if len(sys.argv) > 2:
            filt = sys.argv[2]
            print(filt)
        jprint(nmdc.list_jobs(filt=filt))
    elif sys.argv[1] == 'list_ops':
        filt = None
        if len(sys.argv) > 2:
            filt = json.loads(sys.argv[2])
        jprint(nmdc.list_ops(filt=filt))
    elif sys.argv[1] == 'list_objs':
        filt = None
        if len(sys.argv) > 2:
            filt = json.loads(sys.argv[2])
        jprint(nmdc.list_objs(filt=filt))
    elif sys.argv[1].startswith('get_obj'):
        obj = sys.argv[2]
        d = nmdc.get_object(obj, decode=True)
        jprint(d)
    elif sys.argv[1] == 'bump_time':
        obj = sys.argv[2]
        nmdc.bump_time(obj)
    elif sys.argv[1].startswith('get_op'):
        opid = sys.argv[2]
        jprint(nmdc.get_op(opid))
    elif sys.argv[1] == 'dumpops':
        site = sys.argv[2]
        ops = nmdc.list_ops(filt={'metadata.site_id': site})
        jprint(ops)
    elif sys.argv[1] == 'mint':
        typ = sys.argv[2]
        ct = int(sys.argv[3])
        ids = nmdc.mint("nmdc", typ, ct)
        jprint(ids)
    elif sys.argv[1] == 'mkobj':
        fn = sys.argv[2]
        desc = sys.argv[3]
        url = sys.argv[4]
        resp = nmdc.create_object(fn, desc, url)
        jprint(resp)
    elif sys.argv[1] == 'dumpjobs':
        filt = None
        if len(sys.argv) == 4:
            filt = {sys.argv[2]: sys.argv[3]}
        ops = nmdc.list_jobs(filt=filt)
        jprint(ops)
    else:
        usage()
