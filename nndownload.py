#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Download videos from Niconico (nicovideo.jp), formerly known as Nico Nico Douga."""

from bs4 import BeautifulSoup
import requests

import json
import math
import xml.dom.minidom
import re
import optparse
import os
import sys
import threading
import getpass
import time

__author__ = "Alex Aplin"
__copyright__ = "Copyright 2016 Alex Aplin"

__license__ = "MIT"
__version__ = "0.9"

LOGIN_URL = "https://account.nicovideo.jp/api/v1/login?site=niconico"
VIDEO_URL = "http://nicovideo.jp/watch/{0}"
CANT_LOGIN = "cant_login"
VIDEO_URL_RE = re.compile(r"(sm.*|1.*)")
DMC_HEARTBEAT_INTERVAL_S = 15
KILOBYTE = 1024
BLOCK_SIZE = 10 * KILOBYTE
EPSILON = 0.0001

FINISHED_DOWNLOADING = False

cmdl_usage = "%prog [options] video_id"
cmdl_version = __version__
cmdl_parser = optparse.OptionParser(usage=cmdl_usage, version=cmdl_version, conflict_handler="resolve")
cmdl_parser.add_option("-u", "--username", dest="username", metavar="USERNAME", help="account username")
cmdl_parser.add_option("-p", "--password", dest="password", metavar="PASSWORD", help="account password")
cmdl_parser.add_option("-d", "--save-to-user-directory", action="store_true", dest="use_user_directory", help="save videos to user directories")
cmdl_parser.add_option("-q", "--quiet", action="store_true", dest="quiet", help="activate quiet mode")
(cmdl_opts, cmdl_args) = cmdl_parser.parse_args()

account_username = None
account_password = None

if cmdl_opts.username is not None:
    account_username = cmdl_opts.username
    account_password = cmdl_opts.password
if cmdl_opts.username is None:
    account_username = input("Username: ")
if account_password is None:
    account_password = getpass.getpass("Password: ")
if len(cmdl_args) == 0:
    sys.exit("You must provide a video ID.")

video_id_mo = VIDEO_URL_RE.match(cmdl_args[0])
if video_id_mo is None:
    sys.exit("Not a valid video ID or URL.")
video_id = video_id_mo.group(1)

LOGIN_POST = {
    "mail_tel": account_username,
    "password": account_password
    }

HTML5_COOKIE = {
    "watch_html5": "1"
    }
    
def cond_print(string):
    """Print unless in quiet mode."""

    global cmdl_opts
    if not cmdl_opts.quiet:
        sys.stdout.write(string)
        sys.stdout.flush()

def login():
    """Login to Nico. Will raise an exception for errors."""

    cond_print("Logging in...")
    session = requests.session()
    session.headers.update({"User-Agent": "nndownload/%s".format(__version__)})
    response = session.post(LOGIN_URL, data=LOGIN_POST)
    response.raise_for_status()
    if CANT_LOGIN in response.url:
        cond_print(" failed.\n")
        sys.exit("Failed to login. Please verify your username and password.")
    cond_print(" done.\n")
    return session


def request_video(video_id):
    """Request the video page and initiate download of the video URI."""

    session = login()
    response = session.get(VIDEO_URL.format(video_id), cookies=HTML5_COOKIE)
    response.raise_for_status()
    document = BeautifulSoup(response.text, "html.parser")
    result = perform_api_request(session, document)
    download_video(session, result)


def perform_heartbeat(response, session, heartbeat_url):
    """Perform a response heartbeat to keep the download connection alive."""

    try:
        response = session.post(heartbeat_url, data=response.toxml())
        response.raise_for_status()
        response = xml.dom.minidom.parseString(response.text).getElementsByTagName("session")[0]
        if not FINISHED_DOWNLOADING:
            heartbeat_timer = threading.Timer(DMC_HEARTBEAT_INTERVAL_S, perform_heartbeat, (response, session, heartbeat_url))
            heartbeat_timer.daemon = True
            heartbeat_timer.start()
            time.sleep(1)

    except (KeyboardInterrupt, SystemExit):
        sys.exit("Got interrupt request.")


def sanitize_filename(filename):
    """Remove illegal characters from the filename."""

    for c in ':*?"<>|/\\':
        filename = filename.replace(c, "")


def format_bytes(number_bytes):
    try:
        exponent = int(math.log(number_bytes, KILOBYTE))
        suffix = 'BKMGTPE'[exponent]
        if exponent == 0:
            return "{0}{1}".format(number_bytes, suffix)
        converted = float(number_bytes / KILOBYTE ** exponent)
        return "{0:.2f}{1}".format(converted, suffix)
    except IndexError:
        sys.exit("Error formatting number of bytes.")


def calculate_speed(start, now, bytes):
    dif = now - start
    if bytes == 0 or dif < EPSILON:
        return "N/A b"
    return format_bytes(bytes / dif)


def download_video(session, result):
    """Download video from response URI and display progress."""

    if cmdl_opts.use_user_directory:
        try:
            if not os.path.exists("{0}".format(result["user"])):
                cond_print("Making directory for {0}...".format(result["user"]))
                os.makedirs("{0}".format(result["user"]))
                cond_print(" done.\n")

            filename = "{0}\{1} - {2}.mp4".format(result["user"], video_id, result["title"])
        except (IOError, OSError):
            sys.exit("Unable to open {0} for writing.".format(filename))

    else:
        filename = "{0} - {1}.mp4".format(video_id, result["title"])

    sanitize_filename(filename)

    if os.path.isfile(filename):
        sys.exit("File already exists. Skipping...")

    try:
        file = open(filename, "wb")
        dl_stream = session.get(result["uri"], stream=True)
        dl_stream.raise_for_status()
        video_len = int(dl_stream.headers["content-length"])

        dl = 0
        start_time = time.time()
        for block in dl_stream.iter_content(BLOCK_SIZE):
            dl += len(block)
            file.write(block)
            done = int(25 * dl / video_len)
            percent = int(100 * dl / video_len)
            speed_str = calculate_speed(start_time, time.time(), dl)
            cond_print("\r|{0}{1}| {2}/100 @ {3:8}/s".format("#" * done, " " * (25 - done), percent, speed_str))    
        file.close()
        global FINISHED_DOWNLOADING
        FINISHED_DOWNLOADING = True

    except KeyboardInterrupt:
        sys.exit()


def perform_api_request(session, document):
    """Collect parameters from video document and build API request"""
    
    params = json.loads(document.find(id="js-initial-watch-data")["data-api-data"])

    result = {}
    result["title"] = params["video"]["title"]
    result["user"] = params["owner"]["nickname"].strip(" さん")

    if params["video"]["dmcInfo"]:
        api_url = params["video"]["dmcInfo"]["session_api"]["api_urls"][0] + "?suppress_response_codes=true&_format=xml"
        recipe_id = params["video"]["dmcInfo"]["session_api"]["recipe_id"]
        content_id = params["video"]["dmcInfo"]["session_api"]["content_id"]
        protocol = params["video"]["dmcInfo"]["session_api"]["protocols"][0]
        file_extension = params["video"]["movieType"]
        priority = params["video"]["dmcInfo"]["session_api"]["priority"]
        video_sources = params["video"]["dmcInfo"]["session_api"]["videos"]
        audio_sources = params["video"]["dmcInfo"]["session_api"]["audios"]
        heartbeat_lifetime = params["video"]["dmcInfo"]["session_api"]["heartbeat_lifetime"]
        token = params["video"]["dmcInfo"]["session_api"]["token"]
        signature = params["video"]["dmcInfo"]["session_api"]["signature"]
        auth_type = params["video"]["dmcInfo"]["session_api"]["auth_types"]["http"]
        service_user_id = params["video"]["dmcInfo"]["session_api"]["service_user_id"]
        player_id = params["video"]["dmcInfo"]["session_api"]["player_id"]

        # Build request
        post = """
                <session>
                  <recipe_id>{0}</recipe_id>
                  <content_id>{1}</content_id>
                  <content_type>movie</content_type>
                  <protocol>
                    <name>{2}</name>
                    <parameters>
                      <http_parameters>
                        <method>GET</method>
                        <parameters>
                          <http_output_download_parameters>
                            <file_extension>{3}</file_extension>
                          </http_output_download_parameters>
                        </parameters>
                      </http_parameters>
                    </parameters>
                  </protocol>
                  <priority>{4}</priority>
                  <content_src_id_sets>
                    <content_src_id_set>
                      <content_src_ids>
                        <src_id_to_mux>
                          <video_src_ids>
                          </video_src_ids>
                          <audio_src_ids>
                          </audio_src_ids>
                        </src_id_to_mux>
                      </content_src_ids>
                    </content_src_id_set>
                  </content_src_id_sets>
                  <keep_method>
                    <heartbeat>
                      <lifetime>{5}</lifetime>
                    </heartbeat>
                  </keep_method>
                  <timing_constraint>unlimited</timing_constraint>
                  <session_operation_auth>
                    <session_operation_auth_by_signature>
                      <token>{6}</token>
                      <signature>{7}</signature>
                    </session_operation_auth_by_signature>
                  </session_operation_auth>
                  <content_auth>
                    <auth_type>{8}</auth_type>
                    <service_id>nicovideo</service_id>
                    <service_user_id>{9}</service_user_id>
                    <max_content_count>10</max_content_count>
                    <content_key_timeout>600000</content_key_timeout>
                  </content_auth>
                  <client_info>
                    <player_id>{10}</player_id>
                  </client_info>
                </session>
            """.format(recipe_id,
                       content_id,
                       protocol,
                       file_extension,
                       priority,
                       heartbeat_lifetime,
                       token,
                       signature,
                       auth_type,
                       service_user_id,
                       player_id).strip()

        root = xml.dom.minidom.parseString(post)
        sources = root.getElementsByTagName("video_src_ids")[0]
        for video_source in video_sources:
            element = root.createElement("string")
            quality = root.createTextNode(video_source)
            element.appendChild(quality)
            sources.appendChild(element)

        sources = root.getElementsByTagName("audio_src_ids")[0]
        for audio_source in audio_sources:
            element = root.createElement("string")
            quality = root.createTextNode(audio_source)
            element.appendChild(quality)
            sources.appendChild(element)

        cond_print("Performing initial API request...")
        headers = {"Content-Type": "application/xml"}
        response = session.post(api_url, data=root.toxml())
        response.raise_for_status()
        response = xml.dom.minidom.parseString(response.text)
        result["uri"] = response.getElementsByTagName("content_uri")[0].firstChild.nodeValue
        cond_print(" done.\n")

        # Collect response for heartbeat
        session_id = response.getElementsByTagName("id")[0].firstChild.nodeValue
        response = response.getElementsByTagName("session")[0]
        heartbeat_url = params["video"]["dmcInfo"]["session_api"]["api_urls"][0] + "/" + session_id + "?_format=xml&_method=PUT"
        perform_heartbeat(response, session, heartbeat_url)

    # Legacy for old videos
    elif params["video"]["source"]:
        cond_print("Using legacy URI.\n")
        result["uri"] = params["video"]["source"]

    else:
        sys.exit("Failed to find video URI. Nico may have updated their player.")

    return result

request_video(video_id)
sys.exit()