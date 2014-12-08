#!/usr/bin/python
import requests
from requests import Session, Request
from pyquery import PyQuery
from urllib.parse import urljoin, parse_qsl, urlparse
import re
import os
import time
import sys
import subprocess
import telnetlib

BASE_URL = "http://www.flvcd.com"
QUALITY_DIC =  {"fluent":0, "normal":1, "high":2, "super":3, "super2":4, "super3":5, "real":6}
PARSE_PAGE = "parse.php"
DOWNLOAD_PARSE_PAGE = "downparse.php"
JOB_PAGE = "xdown.php"
DOWNLOAD_PAGE = "diy"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:30.0) Gecko/20100101 Firefox/30.0"


PARSE_URL = urljoin(BASE_URL, PARSE_PAGE)
DOWNLOAD_PARSE_URL = urljoin(BASE_URL, DOWNLOAD_PARSE_PAGE)
JOB_URL = urljoin(BASE_URL, JOB_PAGE)

class ReferedSession(Session):
    def __init__(self, *args, **kwargs):
        Session.__init__(self, *args, **kwargs)
        self.referer = None

    def fire(self, *args, **kwargs):
        req = Request(*args, **kwargs)
        req = self.prepare_request(req)
        if self.referer is not None:
            req.headers.update({"referer": self.referer})
        resp = self.send(req)
        self.referer = req.url
        print(self.referer)
        return resp
        
session = ReferedSession()
session.headers = {"User-Agent": USER_AGENT}

def moz_repl_send(command, host="localhost", port=4242):
    tn = telnetlib.Telnet(host, port, 5)
    print(tn.read_eager())
    _ = tn.read_until(b"repl")
    _ = tn.read_until(b">")
    tn.write('{}\n'.format(command).encode("utf-8"))
    res = b"\n".join(tn.read_until(b"repl> ").split(b"\n")[:-1])
    tn.close()
    res = res.decode("utf-8")
    return res

def inputs_to_data(input_list):
    data = {}
    for item in input_list:
        if item.name:
            data[item.name] = item.value
    return data

def get_parsed(vid_url):
    data = {"kw": vid_url,"format":"normal"}

    #selecting highest quality video
    is_highest_quality = False
    while not is_highest_quality:
        resp = session.fire(method='GET',url=PARSE_URL, params=data)
        selector = PyQuery(resp.text.encode("ISO-8859-1").decode("GBK"))
        quality_available_elems = selector('form[name="mform"] ~ a')
        quality_available_parsed = [dict(parse_qsl(urlparse(u.attrib["href"]).query)) for u in quality_available_elems]
        quality_available = [q['format'] for q in quality_available_parsed if 'format' in q]
        is_highest_quality = True
        for q in quality_available:
            if QUALITY_DIC[q] > QUALITY_DIC[data['format']]:
                data["format"] = q
                is_highest_quality = False

    #get download job id
    inputs = selector('form[name="mform"] input')
    data = inputs_to_data(inputs)
    resp = session.fire(method='GET', url=DOWNLOAD_PARSE_URL, params=data)
    job_id = re.findall(r'send\("'+JOB_URL+r'\?id=([0-9a-f]+)"\)</script>', resp.text)
    
    assert len(job_id) == 1, "Cannot get download job id!"
    job_id = job_id[0]

    #get and parse download link page
    url = "{}/{}/diy00{}.htm".format(BASE_URL, DOWNLOAD_PAGE, job_id)
    resp = session.fire('GET', url).text.encode("ISO-8859-1").decode("GBK")
    print(resp)
    resp = resp.split("\n")
    download_list = []
    fname = None
    link = None
    order = None
    for line in resp:
        if len(line) >= 3:
            if line == "<$>":
                fname = None
                link = None
            elif line == "<&>":
                download_list.append((fname, link, order))
            elif line[:3] == "<N>":
                fname = line[3:]
            elif line[:3] == "<C>":
                link = line[3:]
            elif len(line) >= 11 and line[:11] == "<EXPLODEID>":
                order = int(line[11:])
    return download_list


def download(download_list, download_dir="./", chunk_size=256*1024, text_size_threshold=1024*1024):
    saved_list = [None] * len(download_list)
    all_done = True
    for fname, link, order in download_list:
        #get file extension from links
        orig_fname = urlparse(link).path.split("/")[-1]
        ext = orig_fname.split('.')[-1]
        fpath = os.path.join(download_dir, "{}.{}".format(fname, ext))
        print(fpath)
        is_stream = False
        while not is_stream:
            data = requests.get(link, stream=True)
            total_length = data.headers.get('content-length')
            data_type = data.headers.get('content-type')
            if data_type.lower() == "text/plain" and total_length and int(total_length) < text_size_threshold:
                #doesn't seem to be a video stream, maybe a redirect link
                #FIXME: dirty heuristic: find any quoted link 
                #with our file extension in the text as destination
                link = re.findall('(http://[^\'"]*?{}[^\'"]*)[\'"]'.format(ext), data.text)
                assert len(link) == 1, "the file doesn't contain a single link"
                link = link[0]
            elif data_type.lower() == "application/octet-stream":
                is_stream = True
            else:
                all_done = False
                print("Unknown data type: {}, but still downloading.".format(data_type))
                is_stream = True
        #start downloading ...
        downloaded_size = 0
        if total_length is None:
            total_length = "---"

        #skip downloaded parts
        elif os.path.exists(fpath):
            fsize = os.stat(fpath).st_size
            if fsize >= int(total_length):
                saved_list[order-1] = fpath
                continue

        #download unfinished parts
        with open(fpath, 'wb') as f:
            print("downloading {}".format(fpath))
            print(link)
            timestamp = time.perf_counter()
            for chunk in data.iter_content(chunk_size):
                f.write(chunk)
                downloaded_size += len(chunk)
                kbps = len(chunk) / (time.perf_counter() - timestamp) / 1024
                timestamp = time.perf_counter()
                sys.stdout.write("\r{} of {} @ {:.1f}KB/s".format(\
                        downloaded_size, total_length, kbps))
            print()
        saved_list[order-1] = fpath

        fsize = os.stat(fpath).st_size
        if fsize < int(total_length) or data.status_code != 200:
            print(data.status_code)
            print()
            all_done = False

    return saved_list, all_done

def concatenate(saved_list, output_path = None):
    if output_path is None:
        output_path = '{}.mp4'.format(saved_list[0])
    cmdstr = ('ffmpeg', '-y', '-f', 'concat', '-i', '-', '-c', 'copy', output_path)
    p = subprocess.Popen(cmdstr, stdin=subprocess.PIPE, shell=False)
    for fpath in saved_list:
        p.stdin.write("file '{}'\n".format(fpath.replace("'","\\'")).encode("utf-8"))
    p.stdin.close()
    return p.wait(), output_path



if __name__ == "__main__":
    from pprint import pprint
    import time
    if len(sys.argv) == 1:
        urls = moz_repl_send("content.location.href", port=9898)
        urls = [re.findall('(http://.*)"', url)[0]]
    elif len(sys.argv) >= 2:
        urls = sys.argv[1:]
    print(urls)
    '''
    for i in range(5,0,-1):
        print("{}...".format(i))
        time.sleep(1)
    '''
    for url in urls: 
        all_done = False
        while not all_done:
            dl_list = get_parsed(url)
            #dl_list = dl_list[-1:]
            pprint(dl_list)
            saved_list, all_done = download(dl_list)
            pprint(saved_list)
        print("merging files ...")
        status, fname = concatenate(saved_list)
        print("{}: status {}".format(fname, status))
        if status == 0:
            for fname in saved_list:
                os.unlink(fname)
        print(saved_list)



